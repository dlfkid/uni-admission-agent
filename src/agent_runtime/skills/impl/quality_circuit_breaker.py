"""Two-layer quality circuit breaker for batches of extracted course programs.

Layer 1: fast heuristic scoring (no LLM) — handles clear pass/fail.
Layer 2: LLM review — called only when the heuristic score is uncertain
         (0.4 <= score < 0.7).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from typing import Optional

from src.agent_runtime.skills.contracts import QualityCheckResult
from src.scrapers.helpers import is_noise_program_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
_PASS_THRESHOLD = 0.7
_FAIL_THRESHOLD = 0.4

# ---------------------------------------------------------------------------
# Heuristic weights
# ---------------------------------------------------------------------------
_W_NAME_VALID = 0.4   # name_en non-empty and non-noise
_W_KEY_FIELDS = 0.3   # at least 1 of faculty/tuition_amount/study_options has value
_W_NO_DUPS = 0.2      # duplicate penalty when > 50% of names are identical
_W_NAME_LEN = 0.1     # name_en length in range 5-200


def heuristic_quality_score(programs: list[dict]) -> float:
    """Compute a quality score in [0.0, 1.0] for a batch of extracted programs.

    Weights:
    - 0.4  name_en non-empty and not noise (via is_noise_program_name)
    - 0.3  at least one of faculty / tuition_amount / study_options has value
    - 0.2  not too many duplicates (> 50% identical names => penalty)
    - 0.1  name_en length in range [5, 200]

    Returns:
        Float in [0.0, 1.0].
    """
    if not programs:
        return 0.0

    total = len(programs)

    # --- Component 1: valid, non-noise name ---
    valid_name_count = sum(
        1
        for p in programs
        if str(p.get("name_en") or "").strip()
        and not is_noise_program_name(str(p.get("name_en") or ""))
    )
    c1 = valid_name_count / total

    # --- Component 2: key field fill rate ---
    key_field_count = 0
    for p in programs:
        faculty = p.get("faculty") or ""
        tuition = p.get("tuition_amount") or ""
        study_options = p.get("study_options") or []
        if faculty or tuition or study_options:
            key_field_count += 1
    c2 = key_field_count / total

    # --- Component 3: duplicate detection ---
    names = [str(p.get("name_en") or "").strip() for p in programs]
    if names:
        most_common_count = Counter(names).most_common(1)[0][1]
        duplicate_ratio = most_common_count / total
    else:
        duplicate_ratio = 0.0
    # > 50% identical names → apply a multiplier penalty to the whole score.
    # At 50% dup ratio, multiplier = 1.0.  At 100%, multiplier = 0.0.
    if duplicate_ratio <= 0.5:
        dup_multiplier = 1.0
    else:
        # Scale linearly from 1.0 at 50% to 0.0 at 100%
        dup_multiplier = 1.0 - 2.0 * (duplicate_ratio - 0.5)

    # --- Component 4: name length in range [5, 200] ---
    length_ok_count = sum(
        1
        for p in programs
        if 5 <= len(str(p.get("name_en") or "").strip()) <= 200
    )
    c4 = length_ok_count / total

    # Additive score (c3 contributes its weight at full when no dup penalty)
    base_score = (
        _W_NAME_VALID * c1
        + _W_KEY_FIELDS * c2
        + _W_NO_DUPS * 1.0   # always full weight; multiplier applied below
        + _W_NAME_LEN * c4
    )
    score = base_score * dup_multiplier
    return min(max(score, 0.0), 1.0)


def quality_check(
    programs: list[dict],
    *,
    page_index: Optional[int] = None,
    total_program_count: Optional[int] = None,
) -> QualityCheckResult:
    """Run a quality check on a batch of extracted programs.

    Layer 1 (heuristic):
    - score >= 0.7  → PASS (no LLM)
    - score < 0.4   → FAIL (no LLM)
    - 0.4 <= score < 0.7 → Layer 2 (LLM)

    Layer 2 (LLM):
    - Builds a compact prompt listing program names + key fields.
    - Asks the LLM: {"verdict": "PASS"|"FAIL", "reason": "..."}.
    - Defaults to "pass" if LLM fails or response is unparseable.

    Args:
        programs: List of program dicts (must contain at least "name_en").
        page_index: Optional page number (stored in result on failure).
        total_program_count: Optional running count of programs (stored in result).

    Returns:
        QualityCheckResult with verdict, score, llm_used flag, and reason.
    """
    score = heuristic_quality_score(programs)
    logger.debug("[QualityCheck] heuristic_score=%.3f for %d programs", score, len(programs))

    if score >= _PASS_THRESHOLD:
        return QualityCheckResult(
            verdict="pass",
            heuristic_score=score,
            llm_used=False,
            reason="heuristic pass",
        )

    if score < _FAIL_THRESHOLD:
        return QualityCheckResult(
            verdict="fail",
            heuristic_score=score,
            llm_used=False,
            reason="heuristic fail",
            failed_at_page=page_index,
            failed_at_program_count=total_program_count,
        )

    # Uncertain zone: call LLM
    logger.info(
        "[QualityCheck] Uncertain score %.3f — invoking LLM review for %d programs",
        score, len(programs),
    )
    verdict, reason = _llm_review(programs)
    return QualityCheckResult(
        verdict=verdict,
        heuristic_score=score,
        llm_used=True,
        reason=reason,
        failed_at_page=page_index if verdict == "fail" else None,
        failed_at_program_count=total_program_count if verdict == "fail" else None,
    )


# ---------------------------------------------------------------------------
# LLM Layer 2 helpers
# ---------------------------------------------------------------------------

def _build_prompt(programs: list[dict]) -> str:
    """Build a compact prompt listing program names and key fields."""
    lines = ["Review this batch of extracted university programs for quality."]
    lines.append(
        "Respond ONLY with valid JSON: {\"verdict\": \"PASS\" or \"FAIL\", \"reason\": \"one sentence\"}."
    )
    lines.append("")
    lines.append("Programs:")
    for i, p in enumerate(programs, 1):
        name = str(p.get("name_en") or "").strip() or "(empty)"
        faculty = str(p.get("faculty") or "").strip()
        tuition = str(p.get("tuition_amount") or "").strip()
        study_opts = p.get("study_options") or []
        study_str = ", ".join(str(o) for o in study_opts) if study_opts else ""
        fields_parts = [s for s in [faculty, tuition, study_str] if s]
        fields_str = " | ".join(fields_parts) if fields_parts else "no key fields"
        lines.append(f"  {i}. {name} [{fields_str}]")
    lines.append("")
    lines.append(
        "FAIL if: most names are empty, navigation noise, or duplicates. "
        "PASS if most names look like real academic programs."
    )
    return "\n".join(lines)


async def _call_llm(prompt: str) -> str:
    """Call the LLM router and return the raw text response."""
    from src.agents.factory import create_router
    router = create_router()
    return await router.generate_text(prompt)


def _llm_review(programs: list[dict]) -> tuple[str, str]:
    """Run LLM review; returns (verdict, reason).

    Defaults to ("pass", "llm_error") if anything goes wrong.
    """
    prompt = _build_prompt(programs)
    try:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already inside an event loop (e.g. in tests with pytest-asyncio)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, _call_llm(prompt))
                    raw = future.result(timeout=30)
            else:
                raw = loop.run_until_complete(_call_llm(prompt))
        except RuntimeError:
            raw = asyncio.run(_call_llm(prompt))

        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
            text = text.rstrip("`").strip()

        data = json.loads(text)
        verdict_raw = str(data.get("verdict", "PASS")).strip().upper()
        verdict: str = "fail" if verdict_raw == "FAIL" else "pass"
        reason: str = str(data.get("reason", "")).strip()
        logger.info("[QualityCheck] LLM verdict=%s reason=%s", verdict, reason)
        return verdict, reason

    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[QualityCheck] LLM review failed (%s), defaulting to pass", exc)
        return "pass", "llm_error"
