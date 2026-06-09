"""Feature-signal classifier for the crawl-strategy subsystem."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

from src.services.crawl_strategy.extractors import EXTRACTORS
from src.services.crawl_strategy.types import ExtractKind

_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*([^)\s]+)")
_MIN_CONFIDENT = 5
_PREFERENCE = [
    ExtractKind.BLOB,
    ExtractKind.HEADING_LINK,
    ExtractKind.INLINE_DEGREE,
    ExtractKind.MERGED_COLUMNS,
    ExtractKind.TEXT_HEADING,
]


@dataclass
# pylint: disable=too-few-public-methods
class ClassifyResult:
    """Result returned by :func:`classify`."""

    kind: Optional[ExtractKind]
    confident: bool
    count: int
    scores: Dict[str, int]


def feature_signals(markdown: str, base_url: str) -> Dict[str, int]:
    """Return raw hit-counts for every extractor plus a total link count."""
    scores = {k.value: len(fn(markdown, base_url)) for k, fn in EXTRACTORS.items()}
    scores["link_total"] = len(_LINK_RE.findall(markdown or ""))
    return scores


def classify(markdown: str, base_url: str) -> ClassifyResult:
    """Pick the best-matching :class:`ExtractKind` for *markdown*.

    Scores each extractor by hit-count, breaks ties by preference order
    (earlier in :data:`_PREFERENCE` wins), and gates confidence at
    :data:`_MIN_CONFIDENT` hits.
    """
    scores = {k: len(EXTRACTORS[k](markdown, base_url)) for k in _PREFERENCE}
    best_kind = max(_PREFERENCE, key=lambda k: (scores[k], -_PREFERENCE.index(k)))
    best = scores[best_kind]
    confident = best >= _MIN_CONFIDENT
    return ClassifyResult(
        kind=best_kind if confident else None,
        confident=confident,
        count=best,
        scores={k.value: v for k, v in scores.items()},
    )
