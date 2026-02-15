"""
Deterministic text normalization utilities for program identification.

Provides functions to generate consistent, reproducible program_group_code
values based purely on university slug and program name — no LLM required.
"""

import re
from typing import Set


# Stopwords removed during normalization to collapse trivial variations
_STOPWORDS: Set[str] = {
    "of", "and", "the", "in", "for", "with", "at", "a", "an", "to",
}


def normalize_program_name(name: str) -> str:
    """Normalize a program name into a compact, deterministic identifier.

    Rules:
        1. Convert to lowercase.
        2. Tokenize on non-alphanumeric boundaries.
        3. Remove stopwords.
        4. Concatenate remaining tokens (no separator).

    Examples:
        >>> normalize_program_name("Master of Science (Business Analytics)")
        'mscbusinessanalytics'
        >>> normalize_program_name("MA in Translation & Interpretation")
        'matranslationinterpretation'

    Args:
        name: Raw English program name.

    Returns:
        Compact normalized string, or ``"unknown"`` if the input is empty.
    """
    if not name or not name.strip():
        return "unknown"

    lowered = name.lower()
    tokens = re.split(r"[^a-z0-9]+", lowered)
    filtered = [t for t in tokens if t and t not in _STOPWORDS]

    if not filtered:
        # Edge case: every token was a stopword — fall back to raw concat
        fallback = re.sub(r"[^a-z0-9]", "", lowered)
        return fallback or "unknown"

    return "".join(filtered)


def generate_program_group_code(univ_slug: str, name_en: str) -> str:
    """Generate a deterministic ``program_group_code``.

    Format: ``{univ_slug}#{normalize_program_name(name_en)}``

    As long as the university slug and cleaned program name are identical,
    the output is guaranteed to be the same — enabling zero-latency local
    computation during imports.

    Args:
        univ_slug: University identifier, e.g. ``"hku"``.
        name_en: English program name.

    Returns:
        Deterministic program group code, e.g. ``"hku#mscfinance"``.
    """
    return f"{univ_slug}#{normalize_program_name(name_en)}"
