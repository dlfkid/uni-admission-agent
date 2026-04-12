"""Heuristic pagination detector for university index pages.

Analyzes raw HTML to determine if a page uses URL-based pagination,
SPA-button pagination, or has no pagination at all. No LLM calls.
"""

from __future__ import annotations

import html
import re
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode

from src.agent_runtime.skills.contracts import PaginationInfo

# Known query parameter names used for page numbering
_PAGE_PARAM_NAMES = {"page", "p", "offset", "start_rank", "pg"}

# Regex to find nav/ul/ol tags with pagination-related class or aria-label.
# Excludes "swiper-pagination" (image carousel) and "page-nav" (in-page section nav).
_PAGINATION_CONTAINER_RE = re.compile(
    r'<(?:nav|ul|ol)([^>]*)>',
    re.IGNORECASE,
)

_HREF_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']*)["\'][^>]*>', re.IGNORECASE)
_BUTTON_DATA_PAGE_RE = re.compile(r'<button[^>]*\bdata-page=["\'](\d+)["\'][^>]*>', re.IGNORECASE)


def _has_pagination_marker(attrs: str) -> bool:
    """Return True if the tag attributes contain a pagination marker."""
    lowered = attrs.lower()
    if "swiper-pagination" in lowered:
        return False
    # Match "pagination" but not "page-nav" (in-page section navigation)
    if re.search(r'pagination', lowered) and not re.search(r'page-nav', lowered):
        return True
    return False


def _extract_container_content(html_text: str, start: int) -> str:
    """Extract content of the matched container element starting at start index."""
    # Find the closing tag by simple depth counting. We support nav, ul, ol.
    tag_match = re.match(r'<(nav|ul|ol)', html_text[start:], re.IGNORECASE)
    if not tag_match:
        return ""
    tag_name = tag_match.group(1).lower()
    close_tag = f'</{tag_name}>'
    open_tag_re = re.compile(rf'<{tag_name}[\s>]', re.IGNORECASE)

    depth = 0
    pos = start
    while pos < len(html_text):
        open_m = open_tag_re.search(html_text, pos)
        close_m = re.search(close_tag, html_text, re.IGNORECASE)
        # Find next open or close occurrence
        open_pos = open_m.start() if open_m else len(html_text)
        close_pos = close_m.start() if close_m else len(html_text)
        # Ensure we only look past current pos
        open_m2 = open_tag_re.search(html_text, pos)
        close_m2 = re.search(close_tag, html_text[pos:], re.IGNORECASE)

        open_pos2 = open_m2.start() if open_m2 else len(html_text)
        close_pos2 = (pos + close_m2.start()) if close_m2 else len(html_text)

        if open_pos2 < close_pos2:
            depth += 1
            pos = open_pos2 + 1
        else:
            depth -= 1
            end_pos = close_pos2 + len(close_tag)
            if depth == 0:
                return html_text[start:end_pos]
            pos = close_pos2 + 1
    # Fallback: return 2000 chars from start
    return html_text[start:start + 2000]


def _extract_hrefs_from_html(fragment: str) -> list[str]:
    """Extract and HTML-unescape all href values from <a> tags."""
    return [html.unescape(m.group(1)) for m in _HREF_RE.finditer(fragment)]


def _parse_page_param(href: str, base_url: str) -> Optional[tuple[str, int]]:
    """Return (param_name, value) if the href contains a known page param, else None."""
    # Resolve relative URLs
    full_url = urljoin(base_url, href)
    parsed = urlparse(full_url)
    qs = parse_qs(parsed.query, keep_blank_values=False)
    for param in _PAGE_PARAM_NAMES:
        if param in qs:
            try:
                return (param, int(qs[param][0]))
            except (ValueError, IndexError):
                continue
    return None


def _build_page_urls(template_href: str, base_url: str, param_name: str,
                     min_val: int, max_val: int) -> list[str]:
    """Generate page URLs for all values from min_val to max_val (inclusive)."""
    full_url = urljoin(base_url, template_href)
    parsed = urlparse(full_url)
    qs = parse_qs(parsed.query, keep_blank_values=False)

    urls = []
    for val in range(min_val, max_val + 1):
        new_qs = dict(qs)
        new_qs[param_name] = [str(val)]
        new_query = urlencode(new_qs, doseq=True)
        new_parsed = parsed._replace(query=new_query)
        urls.append(urlunparse(new_parsed))
    return urls


def _strategy1_pagination_container(html_text: str, base_url: str) -> Optional[PaginationInfo]:
    """Strategy 1: scan for <nav>/<ul>/<ol> with pagination class/aria-label."""
    param_values: dict[str, set[int]] = {}
    representative_hrefs: dict[str, str] = {}

    for m in _PAGINATION_CONTAINER_RE.finditer(html_text):
        attrs = m.group(1)
        if not _has_pagination_marker(attrs):
            continue

        container_html = _extract_container_content(html_text, m.start())
        hrefs = _extract_hrefs_from_html(container_html)

        for href in hrefs:
            # Skip pure fragment anchors (in-page section nav like #content)
            if href.startswith('#'):
                continue
            result = _parse_page_param(href, base_url)
            if result is None:
                continue
            param_name, value = result
            if param_name not in param_values:
                param_values[param_name] = set()
                representative_hrefs[param_name] = href
            param_values[param_name].add(value)

    # Need at least 2 distinct values for a param
    for param_name, values in param_values.items():
        if len(values) >= 2:
            min_val = min(values)
            max_val = max(values)
            template_href = representative_hrefs[param_name]
            page_urls = _build_page_urls(template_href, base_url, param_name, min_val, max_val)
            total_pages = max_val - min_val + 1
            return PaginationInfo(
                pagination_type="url_param",
                page_urls=page_urls,
                total_pages=total_pages,
                current_page=min_val + 1 if min_val == 0 else 1,
                confidence=0.9,
            )

    return None


def _strategy2_loose_page_link(html_text: str, base_url: str) -> Optional[PaginationInfo]:
    """Strategy 2: scan ALL <a href> for pagination-like query params (>= 3 distinct values)."""
    param_values: dict[str, set[int]] = {}
    representative_hrefs: dict[str, str] = {}

    for href in _extract_hrefs_from_html(html_text):
        if href.startswith('#'):
            continue
        result = _parse_page_param(href, base_url)
        if result is None:
            continue
        param_name, value = result
        if param_name not in param_values:
            param_values[param_name] = set()
            representative_hrefs[param_name] = href
        param_values[param_name].add(value)

    for param_name, values in param_values.items():
        if len(values) >= 3:
            min_val = min(values)
            max_val = max(values)
            template_href = representative_hrefs[param_name]
            page_urls = _build_page_urls(template_href, base_url, param_name, min_val, max_val)
            total_pages = max_val - min_val + 1
            return PaginationInfo(
                pagination_type="url_param",
                page_urls=page_urls,
                total_pages=total_pages,
                current_page=1,
                confidence=0.6,
            )

    return None


def _strategy3_spa_button(html_text: str, base_url: str) -> Optional[PaginationInfo]:
    """Strategy 3: detect SPA-style <button data-page="N"> patterns."""
    values: set[int] = set()
    for m in _BUTTON_DATA_PAGE_RE.finditer(html_text):
        try:
            values.add(int(m.group(1)))
        except ValueError:
            pass

    if len(values) >= 2:
        return PaginationInfo(
            pagination_type="spa_button",
            page_urls=[],
            total_pages=max(values),
            current_page=1,
            confidence=0.7,
        )

    return None


def detect_pagination(html_text: str, base_url: str) -> PaginationInfo:
    """Detect pagination type and page URLs from raw HTML.

    Strategies (in priority order):
    1. Pagination container scan (<nav>/<ul>/<ol> with pagination class/aria-label)
    2. Loose page-link scan (all <a> tags with >= 3 distinct page param values)
    3. SPA button detection (<button data-page="N">)
    4. Fallback: single_page

    Args:
        html_text: Raw HTML content of the page.
        base_url: The canonical URL of the page (used to resolve relative hrefs).

    Returns:
        PaginationInfo describing the detected pagination.
    """
    result = _strategy1_pagination_container(html_text, base_url)
    if result is not None:
        return result

    result = _strategy2_loose_page_link(html_text, base_url)
    if result is not None:
        return result

    result = _strategy3_spa_button(html_text, base_url)
    if result is not None:
        return result

    # Strategy 4: no pagination
    return PaginationInfo(
        pagination_type="single_page",
        page_urls=[],
        total_pages=1,
        current_page=1,
        confidence=1.0,
    )
