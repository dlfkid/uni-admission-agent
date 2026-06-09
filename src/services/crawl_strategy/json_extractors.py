"""Config-driven JSON extraction for the ``api`` FetchStrategy.

Pure and JSON-only (markdown extractors live in ``extractors.py``).  An api
site's registry params specify dotted field paths; ``make_json_api_extractor``
turns them into the standard ``Extractor(content, base_url) -> [ExtractItem]``
callable used everywhere else.
"""
from __future__ import annotations

import html
import json
from typing import Any, Callable, List

from src.scrapers.helpers import is_noise_program_name
from src.services.crawl_strategy.types import ExtractItem

Extractor = Callable[[str, str], List[ExtractItem]]


def _dig(obj: Any, dotted_path: str) -> Any:
    """Walk *obj* by a dotted path like ``a.b.c``; None if any step is missing."""
    cur = obj
    for seg in dotted_path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _parse_items(text: str, items_path: str) -> list:
    """Parse *text* as JSON and return items_path as a list (else [])."""
    try:
        data = json.loads(text or "")
    except (ValueError, TypeError):
        return []
    items = _dig(data, items_path)
    return items if isinstance(items, list) else []


def json_is_usable(text: str, items_path: str) -> bool:
    """True iff *text* is JSON whose *items_path* yields a non-empty list."""
    return len(_parse_items(text, items_path)) > 0


def _clean_name(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return html.unescape(raw).strip()


def make_json_api_extractor(items_path: str, name_path: str,
                            detail_url_path: str) -> Extractor:
    """Return an Extractor that parses JSON and emits one ExtractItem per element
    of *items_path*, reading *name_path* / *detail_url_path* (dotted, relative to
    each element).  Names are HTML-unescaped; items with no usable name (or a
    noise name) are skipped; results are de-duplicated by name (casefold)."""

    def _extract(content: str, base_url: str) -> List[ExtractItem]:
        del base_url  # api detail URLs are absolute in the payload
        out: List[ExtractItem] = []
        seen: set = set()
        for element in _parse_items(content, items_path):
            name = _clean_name(_dig(element, name_path))
            if not name or is_noise_program_name(name):
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            url = _dig(element, detail_url_path)
            out.append(ExtractItem(name, url if isinstance(url, str) and url else None))
        return out

    return _extract
