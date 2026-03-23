"""Context compaction — three-layer strategy to keep conversations within limits (s06).

Layer 1 (micro_compact): Every turn, replace old tool results with placeholders.
Layer 2 (auto_compact):  When tokens exceed threshold, LLM summarizes the conversation.
Layer 3 (manual compact): Model calls ``compact`` tool explicitly → same as Layer 2.

Full transcripts are saved to disk before compaction so nothing is truly lost.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KEEP_RECENT_TOOL_RESULTS = 3
AUTO_COMPACT_THRESHOLD = 50_000  # estimated tokens
TRANSCRIPT_DIR = Path(".transcripts")


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate: ~4 chars per token across all message content."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("content", "")))
                    total_chars += len(str(part.get("text", "")))
        # tool_calls in assistant messages
        for tc in msg.get("tool_calls", []):
            total_chars += len(tc.get("function", {}).get("arguments", ""))
    return total_chars // 4


# ---------------------------------------------------------------------------
# Layer 1: micro_compact
# ---------------------------------------------------------------------------


def micro_compact(messages: list[dict[str, Any]]) -> None:
    """Replace old tool-result contents with compact placeholders.

    Modifies *messages* in place. Keeps the most recent
    ``KEEP_RECENT_TOOL_RESULTS`` tool results intact.
    """
    tool_indices: list[tuple[int, str]] = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            # Try to find the tool name from the preceding assistant message
            tool_name = _find_tool_name(messages, i, msg.get("tool_call_id", ""))
            tool_indices.append((i, tool_name))

    if len(tool_indices) <= KEEP_RECENT_TOOL_RESULTS:
        return

    for idx, tool_name in tool_indices[:-KEEP_RECENT_TOOL_RESULTS]:
        content = messages[idx].get("content", "")
        if isinstance(content, str) and len(content) > 200:
            summary = _summarize_tool_result(tool_name, content)
            messages[idx]["content"] = summary


def _summarize_tool_result(tool_name: str, content: str) -> str:
    """Create a compact placeholder that preserves key data from tool results.

    For browser_automation_skill and analyze_page_skill, the selected_urls
    list is critical for the agent to continue working after compaction.
    """
    placeholder = f"[Previous: used {tool_name}]"

    if tool_name not in ("browser_automation_skill", "analyze_page_skill"):
        # For persist_programs_skill, keep the summary line
        if tool_name == "persist_programs_skill":
            try:
                data = json.loads(content)
                imported = data.get("imported_count", 0)
                updated = data.get("updated_count", 0)
                total = data.get("total_submitted", 0)
                dry = data.get("dry_run", False)
                parsed = data.get("parsed_programs", [])
                placeholder += (
                    f" imported={imported} updated={updated}"
                    f" total_submitted={total} dry_run={dry}"
                    f" parsed_programs_count={len(parsed)}"
                )
            except (json.JSONDecodeError, TypeError):
                pass
        return placeholder

    # For browser results, preserve selected_urls which the agent needs
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return placeholder

    parts = [placeholder]

    # Preserve selected_urls (critical for index→detail workflow)
    selected = data.get("selected_urls")
    if selected and isinstance(selected, list):
        parts.append(f"selected_urls: {json.dumps(selected, ensure_ascii=False)}")

    # Preserve page_type if present
    page_type = data.get("page_type") or data.get("page_type_hint")
    if page_type:
        parts.append(f"page_type: {page_type}")

    # Preserve link count from analyze results
    link_count = data.get("detail_link_count") or data.get("link_count")
    if link_count is not None:
        parts.append(f"link_count: {link_count}")

    return "\n".join(parts)


def _find_tool_name(
    messages: list[dict[str, Any]], tool_msg_idx: int, tool_call_id: str
) -> str:
    """Walk backwards to find the tool name for a given tool_call_id."""
    for i in range(tool_msg_idx - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            if tc.get("id") == tool_call_id:
                return tc.get("function", {}).get("name", "unknown")
    return "unknown"


# ---------------------------------------------------------------------------
# Layer 2 & 3: auto_compact / manual compact
# ---------------------------------------------------------------------------


async def auto_compact(
    messages: list[dict[str, Any]],
    client: Any,
    model: str,
) -> list[dict[str, Any]]:
    """Save transcript to disk, then ask the LLM to summarize.

    Returns a fresh messages list with only [summary + ack].
    """
    _save_transcript(messages)

    # Build a condensed version of the conversation for summarization
    transcript_text = _messages_to_text(messages)
    # Truncate to avoid exceeding the model's own limit during summarization
    truncated = transcript_text[:80_000]

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": (
                    "Summarize the following agent conversation for continuity. "
                    "Include: what task was requested, what tools were called and "
                    "their key results, current progress (what's done vs remaining), "
                    "and any important state (URLs, slugs, counts). "
                    "CRITICAL: preserve ALL discovered URLs (selected_urls, detail "
                    "page URLs, index URLs) verbatim — these are needed for the "
                    "next steps. Be concise but complete — this summary replaces "
                    "the full history.\n\n"
                    f"{truncated}"
                ),
            }
        ],
        max_tokens=2000,
    )

    summary = response.choices[0].message.content or "(empty summary)"
    logger.info(
        "[Compact] Compressed %d messages (~%d tokens) into summary",
        len(messages),
        estimate_tokens(messages),
    )

    return [
        {"role": "user", "content": f"[Compressed conversation]\n\n{summary}"},
        {"role": "assistant", "content": "Understood. Continuing from the summary."},
    ]


def should_auto_compact(messages: list[dict[str, Any]]) -> bool:
    """Check whether the conversation has grown past the auto-compact threshold."""
    return estimate_tokens(messages) > AUTO_COMPACT_THRESHOLD


# ---------------------------------------------------------------------------
# Transcript persistence
# ---------------------------------------------------------------------------


def _save_transcript(messages: list[dict[str, Any]]) -> Path:
    """Save full message history to a JSONL file for recovery."""
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
    logger.info("[Compact] Saved transcript to %s", path)
    return path


def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    """Flatten messages into a readable text block for summarization."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False, default=str)
        # Include tool call info for assistant messages
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            tc_names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
            lines.append(f"[{role}] (called tools: {', '.join(tc_names)})")
        if content:
            # Truncate individual messages to keep the summary input reasonable
            lines.append(f"[{role}] {content[:2000]}")
    return "\n".join(lines)
