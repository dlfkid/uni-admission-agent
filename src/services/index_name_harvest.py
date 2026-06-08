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
# ignored. MULTILINE so ^ matches each line. (Leeds / Edinburgh style.)
_HEADING_LINK_RE = re.compile(
    r"^\s{0,3}#{1,4}\s+\[([^\]]+)\]\(\s*([^)\s]+)",
    re.MULTILINE,
)

# Any markdown link [text](url). Used to also catch INLINE course links
# (UCL style) whose anchor text is itself a program name.
_ANY_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*([^)\s]+)")

# A program-name anchor ends with a degree token, optionally followed by a
# parenthetical like "(Hons)" / "(Year Abroad)". Distinguishes real course
# links ("Anthropology BSc", "Architecture MSci") from navigation.
_DEGREE_SUFFIX_RE = re.compile(
    r"\b(?:BA|BSc|BASc|BEng|LLB|MArch|MBA|MChem|MComp|MEng|MMath|MPhil|MRes|"
    r"MSci|MSc|MA|LLM|PhD|DPhil|PGDip|PGCert|FdA|FdSc)\b"
    r"\s*(?:\([^)]*\))?\s*$",
)


def _looks_like_program_name(text: str) -> bool:
    return bool(_DEGREE_SUFFIX_RE.search(str(text or "").strip()))


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

    # Heading-level links (Leeds/Edinburgh) are course cards by structure.
    # Inline links (UCL) qualify only when the anchor looks like a program
    # name (ends with a degree token). Heading positions are recorded so an
    # inline scan doesn't double-process them.
    heading_spans = set()
    candidates: List[tuple[str, str]] = []
    for match in _HEADING_LINK_RE.finditer(markdown or ""):
        heading_spans.add(match.start())
        candidates.append((match.group(1), match.group(2)))
    for match in _ANY_LINK_RE.finditer(markdown or ""):
        if match.start() in heading_spans:
            continue
        if _looks_like_program_name(match.group(1)):
            candidates.append((match.group(1), match.group(2)))

    out: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_names: set[str] = set()
    for raw_name, raw_url in candidates:
        name = _clean_name(raw_name)
        raw_url = str(raw_url or "").strip()
        if not name or not raw_url:
            continue
        if is_noise_program_name(name):
            continue
        url = urljoin(base_url, raw_url)
        url_key = _canonical_url_key(url)
        # Name-based dedup is safe here: the name comes straight from the
        # course-card anchor (reliable), so identical names = same course.
        # Some sites (Edinburgh) list each course twice under URLs that
        # differ only by a /<year>/ segment — URL-only dedup would keep
        # both, so dedup on name too.
        name_key = name.casefold()
        if url_key in seen_urls or name_key in seen_names:
            continue
        seen_urls.add(url_key)
        seen_names.add(name_key)
        out.append({"name_en": name, "source_url": url})
    return out
