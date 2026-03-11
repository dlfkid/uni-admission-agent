"""Parsing helpers for dynamic onhold index selection."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

_TOKEN_RE = re.compile(r"[\s,]+")
_RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
_INT_RE = re.compile(r"^\d+$")


class SelectionParseResult(BaseModel):
    """Parsed user selection for onhold item indices."""

    selected: list[int] = Field(default_factory=list)
    invalid_tokens: list[str] = Field(default_factory=list)


def parse_selected_indices(text: str) -> SelectionParseResult:
    """Parse comma/space/range index expressions from user text."""
    raw = str(text or "").strip()
    if not raw:
        return SelectionParseResult()

    normalized = raw.lower()
    normalized = normalized.replace("continue", " ")
    normalized = normalized.replace("process", " ")
    normalized = normalized.replace("处理", " ")
    normalized = normalized.replace("继续", " ")

    tokens = [token.strip() for token in _TOKEN_RE.split(normalized) if token.strip()]
    selected: set[int] = set()
    invalid_tokens: list[str] = []

    for token in tokens:
        if _INT_RE.match(token):
            value = int(token)
            if value > 0:
                selected.add(value)
            else:
                invalid_tokens.append(token)
            continue

        range_match = _RANGE_RE.match(token)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start <= 0 or end <= 0 or end < start:
                invalid_tokens.append(token)
                continue
            for value in range(start, end + 1):
                selected.add(value)
            continue

        invalid_tokens.append(token)

    return SelectionParseResult(
        selected=sorted(selected),
        invalid_tokens=invalid_tokens,
    )
