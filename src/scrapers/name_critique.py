"""Name self-critique: detect bad program names and re-prompt the LLM.

When the first extraction pass picks something that doesn't look like a
real program name (e.g. "Faculty of Business" or "Apply Now"), this
module re-prompts the LLM with the failed guess and taxonomy hints,
asking it to look again on the same page.

Mirrors the cleaner empty-shell self-critique pattern (PR #24), but
focused specifically on the program name — names have different failure
modes than content emptiness and need their own critique prompt.

Taxonomy is treated as a HINT (here's what the page is probably about),
not a CONSTRAINT (LLM must pick from this list). Variants like "MSc
Finance with AI Specialization" are perfectly fine when the hint is
"MSc Finance".
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, List, Optional

from pydantic import BaseModel, Field

from src.scrapers.helpers import is_noise_program_name


logger = logging.getLogger(__name__)


# Minimum name length to even consider it. Anything shorter is suspect.
_MIN_NAME_LENGTH = 3

# Taxonomy score above which we trust the alignment check — if best
# match is below this, taxonomy is too uncertain to judge the LLM's
# choice.
_ALIGNMENT_TRIGGER_SCORE = 0.50

# Token-overlap floor for "name aligns with taxonomy". If the LLM's
# name shares at least this fraction of the high-confidence candidate's
# tokens, it's considered a reasonable variant.
_ALIGNMENT_OVERLAP_FLOOR = 0.5


def _tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens, length >= 2."""
    return [t for t in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(t) >= 2]


def _name_looks_suspect(  # pylint: disable=too-many-return-statements
    name: str, taxonomy_matches: Iterable[dict] | None
) -> bool:
    """Return True if the extracted name is obviously wrong.

    Two independent signals:
      1. Name is empty / too short / matches the project's noise filter.
      2. Taxonomy has a high-confidence match for this page, but the
         LLM's chosen name shares almost no tokens with ANY of those
         high-confidence candidates — strong indication LLM grabbed the
         wrong heading.

    Either signal triggers suspicion. False = looks like a real program.
    """
    cleaned = str(name or "").strip()
    if not cleaned or len(cleaned) < _MIN_NAME_LENGTH:
        return True
    if is_noise_program_name(cleaned):
        return True

    # Taxonomy-alignment check
    matches = list(taxonomy_matches or [])
    if not matches:
        return False
    confident = [
        m for m in matches
        if float(m.get("score") or 0.0) >= _ALIGNMENT_TRIGGER_SCORE
    ]
    if not confident:
        return False

    name_tokens = set(_tokenize(cleaned))
    if not name_tokens:
        return True

    for match in confident:
        candidate = str(match.get("name_en") or "").strip()
        cand_tokens = set(_tokenize(candidate))
        if not cand_tokens:
            continue
        overlap = len(name_tokens & cand_tokens) / len(cand_tokens)
        if overlap >= _ALIGNMENT_OVERLAP_FLOOR:
            return False  # name shares enough with at least one strong candidate

    # No confident candidate aligned with the chosen name → suspect.
    return True


# ---------------------------------------------------------------------------
# Refine via LLM critique
# ---------------------------------------------------------------------------


class RefinedName(BaseModel):
    """Structured output for the refine LLM call."""

    name: Optional[str] = Field(
        default=None,
        description=(
            "The real program name found on the page, or null if none "
            "could be confidently identified."
        ),
    )


def _build_critique_prompt(
    *,
    bad_name: str,
    taxonomy_hints: List[str],
    source_url: str,
    markdown: str,
) -> str:
    hints_block = ""
    if taxonomy_hints:
        hints_lines = "\n".join(f"  - {h}" for h in taxonomy_hints)
        hints_block = (
            "Context from URL and page metadata suggests this page is about\n"
            "a program in one of these areas (use as guidance, not exact match):\n"
            f"{hints_lines}\n\n"
            "Reasonable variants are fine — for example, if a hint is\n"
            "'MSc Finance', the page's real name might be 'MSc Finance with AI\n"
            "Specialization' or 'Master of Science in Finance'. The hints tell\n"
            "you the SUBJECT, the page tells you the SPECIFIC name.\n\n"
        )

    return (
        "PROGRAM NAME LOOKS WRONG — PLEASE RE-EXAMINE\n"
        "============================================\n"
        f"Source page: {source_url}\n\n"
        f"A previous extraction picked this as the program name: {bad_name!r}\n"
        "This looks like a navigation heading, a generic faculty label, or\n"
        "boilerplate text — NOT a real degree program (e.g. an MSc/BA/PhD\n"
        "with a specific subject).\n\n"
        f"{hints_block}"
        "Re-read the page content below carefully. Find the ACTUAL program\n"
        "name — typically a degree code (MSc/MA/BSc/PhD/Bachelor/Master/etc.)\n"
        "followed by a specific subject.\n\n"
        "If after careful re-reading you cannot confidently find a real\n"
        "degree program name on this page, return null. Do not fabricate\n"
        "a plausible-sounding name to satisfy this request.\n"
        "============================================\n\n"
        "PAGE CONTENT:\n"
        f"{markdown}\n"
    )


def refine_name_with_critique(
    *,
    router: Any,
    markdown: str,
    bad_name: str,
    taxonomy_hints: List[str],
    source_url: str,
) -> Optional[str]:
    """Re-prompt the LLM with critique to find the real program name.

    Returns the refined name on success, or None when:
      - The LLM call fails.
      - The LLM returns null (honest "can't find one").
      - The LLM returns ANOTHER noise name (refuses to fix).

    Capped at one call — no critique chains, matching the cleaner
    self-critique discipline.
    """
    prompt = _build_critique_prompt(
        bad_name=bad_name,
        taxonomy_hints=list(taxonomy_hints or []),
        source_url=source_url,
        markdown=markdown,
    )

    try:
        response = router.generate(prompt, RefinedName)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "Name critique LLM call failed for %s (%s)", source_url, exc
        )
        return None

    if not getattr(response, "text", None):
        return None

    try:
        parsed = RefinedName.model_validate_json(response.text)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "Name critique returned invalid JSON for %s (%s)", source_url, exc
        )
        return None

    refined = (parsed.name or "").strip()
    if not refined:
        return None

    # Guard against the LLM stubbornly returning another junk name.
    if is_noise_program_name(refined) or len(refined) < _MIN_NAME_LENGTH:
        logger.info(
            "Name critique returned another noise/short name (%r); discarding",
            refined,
        )
        return None

    logger.info(
        "[NameCritique] Refined %r -> %r for %s",
        bad_name, refined, source_url,
    )
    return refined
