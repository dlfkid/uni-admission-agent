"""
Link parser module for URL extraction and page type detection.

Contains functionality for extracting links from markdown content
and determining whether a page is an index or detail page.
Includes LLM-powered link filtering for index pages.
"""

import logging
import re
from typing import List, Tuple
from urllib.parse import urljoin, urlparse

from src.agents.factory import RouterAgent
from src.models.scraper_models import FilteredLinks, PageType
from src.scrapers.helpers import load_prompt

logger = logging.getLogger(__name__)

# Maximum number of link items to send to the LLM in one call
_MAX_LINKS_FOR_LLM = 80

_DEGREE_TEXT_PATTERN = re.compile(
    r"\b("
    r"msc|ma|mba|llm|mres|mphil|pgdip|pgcert|master|masters|programme|program"
    r")\b",
    re.IGNORECASE,
)
_COURSE_PATH_PATTERN = re.compile(
    r"/(course|courses|programme|programmes?)/",
    re.IGNORECASE,
)
_COURSE_DETAIL_PATTERN = re.compile(
    r"/study/masters/courses/list/\d+/",
    re.IGNORECASE,
)
_NAV_TEXT_PATTERN = re.compile(
    r"\b("
    r"home|about|contact|research|news|privacy|cookies|terms|support|menu|search|"
    r"international|undergraduate|postgraduate"
    r")\b",
    re.IGNORECASE,
)


def _course_link_score(url: str, text: str, base_url: str) -> int:
    """Score how likely a link points to a course detail page."""
    score = 0
    normalized_url = str(url or "").strip()
    normalized_text = str(text or "").strip()
    lower_url = normalized_url.lower()

    if _COURSE_DETAIL_PATTERN.search(lower_url):
        score += 8
    elif _COURSE_PATH_PATTERN.search(lower_url):
        score += 3

    if _DEGREE_TEXT_PATTERN.search(normalized_text):
        score += 4
    if _NAV_TEXT_PATTERN.search(normalized_text):
        score -= 4

    try:
        link_host = urlparse(normalized_url).netloc.lower()
        base_host = urlparse(base_url).netloc.lower()
        if link_host and base_host and link_host == base_host:
            score += 1
    except Exception:  # pragma: no cover - defensive
        pass

    return score


def _prioritize_links_for_llm(
    link_pairs: List[Tuple[str, str]],
    base_url: str,
) -> List[Tuple[str, str]]:
    """Move likely course links earlier so truncation keeps useful candidates."""
    scored = [
        (_course_link_score(url, text, base_url), idx, (url, text))
        for idx, (url, text) in enumerate(link_pairs)
    ]
    if not scored:
        return []
    if max(score for score, _, _ in scored) <= 0:
        return link_pairs

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [pair for _, _, pair in scored]


def filter_links_by_heuristic(
    link_pairs: List[Tuple[str, str]],
    base_url: str,
) -> List[str]:
    """Deterministically keep course-like links without calling any LLM."""
    if not link_pairs:
        return []

    scored: List[Tuple[int, int, str]] = []
    for idx, (url, text) in enumerate(link_pairs):
        score = _course_link_score(url, text, base_url)
        if score > 0:
            scored.append((score, idx, url))

    if not scored:
        return []

    scored.sort(key=lambda item: (-item[0], item[1]))
    seen: set[str] = set()
    selected: List[str] = []
    for _score, _idx, url in scored:
        if url in seen:
            continue
        seen.add(url)
        selected.append(url)

    return selected


def extract_links(markdown: str, base_url: str) -> List[str]:
    """
    Extract potential program detail page URLs from Markdown using Regex.
    
    Optimized to avoid LLM calls. Finds all [text](url) and raw URLs,
    then filters for valid absolute URLs.

    Args:
        markdown: Markdown content of the page.
        base_url: Base URL for resolving relative links.

    Returns:
        List of absolute URLs for program detail pages.
    """
    logger.info("Extracting links via Regex (heuristic)...")
    
    # Regex to find markdown links: [text](href)
    md_link_pattern = re.compile(r"\[.*?\]\((.*?)\)")
    # Regex to find raw http(s) links
    raw_url_pattern = re.compile(r"(https?://[^\s\)]+)")

    found_links: set[str] = set()
    
    # 1. Extract from markdown links
    for match in md_link_pattern.findall(markdown):
        # Clean up link (remove title parts like " title")
        href = match.split(" ")[0].strip()
        if href:
            found_links.add(href)
            
    # 2. Extract raw URLs
    for match in raw_url_pattern.findall(markdown):
        found_links.add(match)

    # 3. Resolve and Filter
    resolved_links: List[str] = []
    for link in found_links:
        # Skip empty or anchor links
        if not link or link.startswith("#") or link.startswith("mailto:"):
            continue

        try:
            absolute = urljoin(base_url, link)
            
            # Heuristic: Filter out obviously irrelevant links
            # (e.g., CSS, JS, images, login pages)
            lower_link = absolute.lower()
            skip_extensions = [
                ".css", ".js", ".png", ".jpg", ".jpeg", 
                ".ico", ".svg", ".woff", ".ttf"
            ]
            if any(ext in lower_link for ext in skip_extensions):
                continue
            if "login" in lower_link or "signin" in lower_link or "admin" in lower_link:
                continue
                
            # Ensure it's not the base URL itself
            if absolute.rstrip("/") != base_url.rstrip("/"):
                resolved_links.append(absolute)
        except Exception:
            continue

    logger.info("Extracted %d unique links via Regex", len(resolved_links))
    return resolved_links


def extract_links_with_text(
    markdown: str, base_url: str,
) -> List[Tuple[str, str]]:
    """Extract links together with their anchor (display) text.

    Returns a deduplicated list of ``(absolute_url, anchor_text)`` tuples,
    applying the same basic filters as :func:`extract_links`.
    """
    md_link_pattern = re.compile(r"\[([^\]]*?)\]\(([^)]+?)\)")

    seen: set[str] = set()
    pairs: List[Tuple[str, str]] = []

    skip_extensions = (
        ".css", ".js", ".png", ".jpg", ".jpeg",
        ".ico", ".svg", ".woff", ".ttf",
    )

    for text, href in md_link_pattern.findall(markdown):
        href = href.split(" ")[0].strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        try:
            absolute = urljoin(base_url, href)
        except Exception:  # pylint: disable=broad-except
            continue

        lower = absolute.lower()
        if any(ext in lower for ext in skip_extensions):
            continue
        if "login" in lower or "signin" in lower or "admin" in lower:
            continue
        if absolute.rstrip("/") == base_url.rstrip("/"):
            continue

        if absolute not in seen:
            seen.add(absolute)
            pairs.append((absolute, text.strip()))

    logger.info(
        "Extracted %d links with anchor text from %s",
        len(pairs), base_url,
    )
    return pairs


def filter_links_by_llm(
    router: RouterAgent,
    link_pairs: List[Tuple[str, str]],
    base_url: str,
) -> List[str]:
    """Use the LLM to identify which links are likely course detail pages.

    Sends anchor text + URL list to the LLM in batches to avoid
    truncating long index pages. Returns only the URLs that the
    LLM considers course-related.

    Args:
        router: LLM router agent.
        link_pairs: ``(url, anchor_text)`` tuples from
            :func:`extract_links_with_text`.
        base_url: The index page URL (for context).

    Returns:
        Filtered list of absolute URLs that are likely course pages.
    """
    if not link_pairs:
        return []

    prioritized = _prioritize_links_for_llm(link_pairs, base_url)
    total_links = len(prioritized)
    batch_size = _MAX_LINKS_FOR_LLM
    batch_total = max(1, (total_links + batch_size - 1) // batch_size)

    if total_links > batch_size:
        logger.info(
            "Link list has %d entries; filtering in %d batches (size=%d)",
            total_links,
            batch_total,
            batch_size,
        )

    filtered_all: List[str] = []
    for batch_index in range(batch_total):
        start = batch_index * batch_size
        end = min(start + batch_size, total_links)
        batch_pairs = prioritized[start:end]
        if not batch_pairs:
            continue
        filtered_batch = _filter_link_batch_by_llm(
            router=router,
            link_pairs=batch_pairs,
            base_url=base_url,
            batch_index=batch_index + 1,
            batch_total=batch_total,
        )
        filtered_all.extend(filtered_batch)

    seen: set[str] = set()
    deduped: List[str] = []
    for url in filtered_all:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)

    logger.info(
        "[LLM Filter] Selected %d/%d links as course detail pages",
        len(deduped), total_links,
    )
    return deduped


def _filter_link_batch_by_llm(
    router: RouterAgent,
    link_pairs: List[Tuple[str, str]],
    base_url: str,
    *,
    batch_index: int,
    batch_total: int,
) -> List[str]:
    """Filter one batch of candidate links via LLM."""
    lines: List[str] = []
    for idx, (url, text) in enumerate(link_pairs, 1):
        display = text if text else "(no text)"
        lines.append(f"{idx}. [{display}]({url})")
    link_list_text = "\n".join(lines)

    prompt_template = load_prompt("filter_index_links.txt")
    prompt = prompt_template.format(
        base_url=base_url,
        link_count=len(link_pairs),
        link_list=link_list_text,
    )

    logger.info(
        "[LLM Filter] Evaluating batch %d/%d with %d links from %s",
        batch_index,
        batch_total,
        len(link_pairs),
        base_url,
    )

    try:
        response = router.generate(prompt, FilteredLinks)

        if not response.text:
            logger.warning(
                "[LLM Filter] Empty response on batch %d/%d; "
                "falling back to all %d links in batch",
                batch_index,
                batch_total,
                len(link_pairs),
            )
            return [u for u, _ in link_pairs]

        result = FilteredLinks.model_validate_json(response.text)
        valid_urls = {u for u, _ in link_pairs}
        filtered = [u for u in result.urls if u in valid_urls]

        if filtered:
            return filtered

        heuristic_fallback = [
            url
            for url, text in link_pairs
            if _course_link_score(url, text, base_url) > 0
        ]
        if heuristic_fallback:
            logger.warning(
                "[LLM Filter] Batch %d/%d returned empty list; "
                "using %d heuristic course-like links",
                batch_index,
                batch_total,
                len(heuristic_fallback),
            )
            return heuristic_fallback
        return []
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "LLM link filtering failed on batch %d/%d (%s). "
            "Falling back to all %d links in batch.",
            batch_index,
            batch_total,
            exc,
            len(link_pairs),
        )
        return [u for u, _ in link_pairs]


def detect_page_type(markdown: str, link_count: int) -> PageType:
    """
    Determines if a page is an INDEX or DETAIL page using heuristics.
    
    Optimization: Replaced LLM call with content & link density check.
    Strong content signals ("Tuition", "Deadline") => DETAIL.
    
    Args:
        markdown: Markdown content of the page.
        link_count: Number of links found on the page.
        
    Returns:
        PageType.INDEX or PageType.DETAIL.
    """
    # 1. Strong content signals for Detail Page
    # If these keywords appear, it's likely a program page regardless of links
    content_lower = markdown.lower()
    detail_signals = [
        "tuition fee", "program fee", "application deadline", 
        "entry requirements", "admission requirements",
        "course structure", "module list", "what you will study",
        "program overview", "degree requirements"
    ]
    
    if any(signal in content_lower for signal in detail_signals):
        logger.info("Page Type Detection: DETAIL (Found content signal)")
        return PageType.DETAIL

    # 2. Heuristic: Indices usually have many links
    threshold = 15
    
    if link_count > threshold:
        logger.info("Page Type Detection: INDEX (Links=%d > %d)", link_count, threshold)
        return PageType.INDEX
    
    logger.info("Page Type Detection: DETAIL (Links=%d <= %d)", link_count, threshold)
    return PageType.DETAIL
