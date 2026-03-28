"""Common skill handlers that wrap existing core services."""

from __future__ import annotations

import logging

from src.agent_bridge.client_automation_bridge import ClientAutomationBridge
from src.agent_bridge.contracts import BrowserFetchInput

import threading
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM link-filter cache
# Keyed by (url, page_type_hint).  Entries expire after TTL seconds so that
# stale index pages are eventually re-analysed without penalising the common
# case where the agent mistakenly re-fetches the same index URL several times
# within a single task run.
# ---------------------------------------------------------------------------
_llm_filter_cache: dict[tuple[str, str], tuple[float, list, dict]] = {}
_llm_filter_lock = threading.Lock()
_LLM_FILTER_CACHE_TTL = 300  # seconds


def _get_cached_llm_filter(url: str, hint: str) -> tuple[list, dict] | None:
    key = (url, hint)
    with _llm_filter_lock:
        entry = _llm_filter_cache.get(key)
        if entry and (time.monotonic() - entry[0]) < _LLM_FILTER_CACHE_TTL:
            return entry[1], entry[2]
    return None


def _set_cached_llm_filter(url: str, hint: str, urls: list, texts: dict) -> None:
    key = (url, hint)
    with _llm_filter_lock:
        _llm_filter_cache[key] = (time.monotonic(), urls, texts)


from src.agent_runtime.skills.contracts import (
    BrowserAutomationSkillInput,
    PersistProgramsSkillInput,
    QueryDbSkillInput,
    ReviewPatchSkillInput,
    SelectDetailCandidatesSkillInput,
)
from src.services.crawler import (
    ingest_program_records_external,
    patch_program_snapshot,
    query_programs,
)


def select_detail_candidates_skill_handler(payload: SelectDetailCandidatesSkillInput) -> dict:
    """Select top-k detail URLs from analyzed link candidates."""
    selected_urls: list[str] = []
    for item in payload.links:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        selected_urls.append(url)
        if len(selected_urls) >= payload.top_k:
            break
    return {"selected_urls": selected_urls}


def persist_programs_skill_handler(payload: PersistProgramsSkillInput) -> dict:
    """Persist caller-structured program records using external-ingest path."""
    return ingest_program_records_external(
        univ_slug=payload.univ_slug,
        year=payload.year,
        programs=payload.programs,
        dry_run=payload.dry_run,
    )


def review_patch_skill_handler(payload: ReviewPatchSkillInput) -> dict:
    """Apply one review patch to a persisted program."""
    if not payload.patch:
        return {
            "updated": False,
            "program_id": payload.program_id,
            "summary": "empty patch",
        }

    updated_program = patch_program_snapshot(payload.program_id, payload.patch)
    if updated_program is None:
        return {
            "updated": False,
            "program_id": payload.program_id,
            "summary": "not found",
        }

    return {
        "updated": True,
        "program_id": payload.program_id,
        "summary": "updated 1 record",
    }


def query_db_skill_handler(payload: QueryDbSkillInput) -> dict:
    """Query stored programs for one university/year."""
    rows = query_programs(univ_slug=payload.univ_slug, year=payload.year)
    return {
        "programs": [row.model_dump(mode="json") for row in rows],
    }


def _html_to_markdown(html: str, url: str) -> str:
    """Convert raw HTML to lightweight text for LLM-friendly context.

    Uses a fast stdlib-based approach instead of crawl4ai which takes
    1-2 minutes on large (100-200K) HTML pages.
    """
    import re
    from html import unescape

    if not html:
        return html

    text = html
    # Remove script/style/noscript blocks
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Convert <br>, <hr> to newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<hr\s*/?>", "\n---\n", text, flags=re.IGNORECASE)
    # Convert block-level closing tags to newlines
    text = re.sub(r"</(?:p|div|tr|li|h[1-6]|section|article|header|footer|nav|main)>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities
    text = unescape(text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def browser_automation_skill_handler(
    payload: BrowserAutomationSkillInput,
    bridge: ClientAutomationBridge,
) -> dict:
    """Fetch browser payload from connected client runtime.

    HTML is converted to markdown before returning to keep the agent
    conversation context small enough for LLM API limits.
    """
    output = bridge.fetch_browser_payload(
        BrowserFetchInput(
            url=payload.url,
            page_type_hint=payload.page_type_hint,
            client_id=payload.client_id,
        )
    )
    result = output.model_dump(mode="json")

    # For index pages, replace the client-side heuristic selected_urls with
    # LLM-ranked candidates.  The client heuristic matches on URL keywords
    # (e.g. "/undergraduate") and returns links in DOM order — so navigation
    # links that appear early in the page fill the limit before real course
    # detail links are reached.  Running filter_links_by_llm on the raw HTML
    # gives the agent a clean, correctly-ranked candidate list.
    html = result.get("html_content") or ""
    if payload.page_type_hint == "index" and html:
        cached = _get_cached_llm_filter(payload.url, payload.page_type_hint)
        if cached is not None:
            cached_urls, cached_texts = cached
            result["selected_urls"] = cached_urls
            if cached_texts:
                result["selected_link_texts"] = cached_texts
            logger.info(
                "[BrowserSkill] LLM filter cache hit (%d links) for %s",
                len(cached_urls),
                payload.url,
            )
        else:
            try:
                from src.services.crawler import analyze_page
                analysis = analyze_page(payload.url, html, "index")
                llm_links = analysis.get("links") or []
                filtered_urls = [link["url"] for link in llm_links]
                filtered_texts = {
                    link["url"]: link["text"]
                    for link in llm_links
                    if link.get("text")
                }
                _set_cached_llm_filter(payload.url, payload.page_type_hint, filtered_urls, filtered_texts)
                if filtered_urls:
                    result["selected_urls"] = filtered_urls
                    result["selected_link_texts"] = filtered_texts
                    logger.info(
                        "[BrowserSkill] LLM filtered %d/%d candidate links for %s",
                        len(filtered_urls),
                        analysis.get("total_found", 0),
                        payload.url,
                    )
                else:
                    logger.info(
                        "[BrowserSkill] LLM found no detail links (total_found=%d) for %s",
                        analysis.get("total_found", 0),
                        payload.url,
                    )
            except Exception as exc:
                logger.warning("[BrowserSkill] LLM link filter failed, using heuristic: %s", exc)

    # Convert raw HTML to markdown to avoid context bloat
    if html:
        result["html_content"] = _html_to_markdown(html, payload.url)

    # For index pages with selected_urls, strip the full HTML to avoid
    # blowing context (150K+ markdown → auto_compact → lose everything).
    # The agent only needs the URL list to proceed.
    selected = result.get("selected_urls") or []
    if selected:
        md = result.get("html_content") or ""
        # Keep a small excerpt (first 2000 chars) for page context
        result["html_content"] = (
            f"[Index page with {len(selected)} detail URLs. "
            f"Full HTML omitted to save context. Use selected_urls below.]\n\n"
            + md[:2000]
            + ("\n...(truncated)" if len(md) > 2000 else "")
        )
    elif payload.page_type_hint == "index":
        # Index page but client heuristics found no detail links.
        # Trim HTML to a manageable size so the agent can use
        # analyze_page_skill without blowing the context window.
        md = result.get("html_content") or ""
        MAX_INDEX_HTML = 15_000
        if len(md) > MAX_INDEX_HTML:
            result["html_content"] = (
                f"[Index page HTML trimmed to {MAX_INDEX_HTML} chars. "
                f"Use analyze_page_skill with url and this html_content to extract detail links.]\n\n"
                + md[:MAX_INDEX_HTML]
                + "\n...(truncated)"
            )

    return result
