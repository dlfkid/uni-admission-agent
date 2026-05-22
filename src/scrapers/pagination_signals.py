"""Deterministic pagination stop signals — LLM-free drift detection.

When an agent auto-paginates, it can wander off the intended index page
into unrelated sections (about pages, faculty listings, etc.). Without
detection, this wastes LLM tokens and pollutes the DB with noise.

These checks run BEFORE LLM extraction on each new page so a drifted
crawl is stopped without spending tokens. They complement (not replace)
the post-extraction quality circuit breaker.
"""
from __future__ import annotations

import re
from typing import List
from urllib.parse import urlparse


# Path segments matching this pattern are treated as "page index" tokens
# and normalized away — so /programs/page/1 and /programs/page/2 collapse
# to the same pattern.
_NUMERIC_SEGMENT_RE = re.compile(r"^\d+$")


def extract_url_pattern(url: str) -> str:
    """Return a normalized 'shape' for a URL.

    Two URLs with the same shape are considered the same logical page
    in the pagination sense (e.g. ?page=1 vs ?page=2). Strips query
    string, fragment, trailing slash, and replaces any numeric path
    segment with a placeholder.
    """
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    segments = [s for s in path.split("/") if s]
    normalized_segments = [
        "{N}" if _NUMERIC_SEGMENT_RE.match(s) else s.lower()
        for s in segments
    ]
    normalized_path = "/" + "/".join(normalized_segments) if normalized_segments else "/"

    return f"{host}{normalized_path}"


def urls_diverged(prev_url: str, current_url: str) -> bool:
    """Return True when ``current_url`` represents a structurally different
    page than ``prev_url`` (the established index page).

    Same pattern → not diverged (it's the next page of the same listing).
    Different pattern → diverged (the crawler wandered off).
    """
    return extract_url_pattern(prev_url) != extract_url_pattern(current_url)


def should_stop_for_decreasing_yield(
    yield_history: List[int],
    *,
    min_history: int = 4,
    ratio_floor: float = 0.20,
    consecutive_zero_stop: int = 2,
) -> bool:
    """Return True when per-page program yield indicates exhaustion.

    Two independent triggers:
      1. Latest page yield is below ``ratio_floor`` of the average of
         prior pages (only fires once we have ``min_history`` pages of
         data, so one bad early page doesn't stop the crawl).
      2. The last ``consecutive_zero_stop`` pages all returned 0
         (fires earlier — two zeros is a clearer signal than just one).
    """
    # Trigger 2: trailing zeros — fires even with shorter history.
    if 0 < consecutive_zero_stop <= len(yield_history):
        tail = yield_history[-consecutive_zero_stop:]
        if all(y == 0 for y in tail):
            return True

    # Trigger 1: needs enough history for the ratio to be meaningful.
    if len(yield_history) < min_history:
        return False

    prior = yield_history[:-1]
    latest = yield_history[-1]
    avg_prior = sum(prior) / len(prior) if prior else 0.0
    if avg_prior <= 0:
        return False
    return latest / avg_prior < ratio_floor
