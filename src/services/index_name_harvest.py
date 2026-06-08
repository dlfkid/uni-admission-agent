"""Names-only harvest from a program index page.

On a course-listing page each program is a heading-level markdown link:

    ##  [Accounting and Finance MSc](https://courses.leeds.ac.uk/f921/...) Duration

while navigation, faculty, and footer links appear as inline links. Parsing
the heading-level links alone yields the exact course titles (with degree
suffix) with no detail-page crawl and no per-page LLM call — the cheapest
and most accurate way to collect program names.

This module is deliberately deterministic (regex + noise filter), so a
"names only" crawl costs one index fetch and nothing more.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urljoin, urlsplit

from src.scrapers.helpers import is_noise_program_name

# Heading-level markdown link: optional leading spaces, 1-4 '#', spaces,
# then [text](url). Trailing page text after the link (e.g. "Duration") is
# ignored. MULTILINE so ^ matches each line.
_HEADING_LINK_RE = re.compile(
    r"^\s{0,3}#{1,4}\s+\[([^\]]+)\]\(\s*([^)\s]+)",
    re.MULTILINE,
)


def _clean_name(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _canonical_url_key(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.rstrip("/")
    if not parts.scheme or not parts.netloc:
        return url.rstrip("/")
    path = parts.path.rstrip("/") or "/"
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


def harvest_index_program_names(
    markdown: str,
    base_url: str,
    univ_slug: str = "",
) -> List[Dict[str, Any]]:
    """Extract program names + source URLs from an index page's heading links.

    Returns a list of ``{"name_en": str, "source_url": str}`` in page order,
    deduped by canonical URL, with navigation/noise headings filtered out.
    """
    del univ_slug  # reserved for future per-university heuristics
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for match in _HEADING_LINK_RE.finditer(markdown or ""):
        name = _clean_name(match.group(1))
        raw_url = str(match.group(2) or "").strip()
        if not name or not raw_url:
            continue
        if is_noise_program_name(name):
            continue
        url = urljoin(base_url, raw_url)
        key = _canonical_url_key(url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name_en": name, "source_url": url})
    return out
