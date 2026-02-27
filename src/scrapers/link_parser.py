"""
Link parser module for URL extraction and page type detection.

Contains functionality for extracting links from markdown content
and determining whether a page is an index or detail page.
Includes LLM-powered link filtering for index pages.
"""

import logging
import re
from typing import List, Tuple
from urllib.parse import urljoin

from src.agents.factory import RouterAgent
from src.models.scraper_models import FilteredLinks, PageType
from src.scrapers.helpers import load_prompt

logger = logging.getLogger(__name__)

# Maximum number of link items to send to the LLM in one call
_MAX_LINKS_FOR_LLM = 80


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

    Sends the anchor text + URL list to the LLM in a single call.
    Returns only the URLs that the LLM considers course-related.

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

    # Truncate if too many links
    truncated = link_pairs[:_MAX_LINKS_FOR_LLM]
    if len(link_pairs) > _MAX_LINKS_FOR_LLM:
        logger.warning(
            "Truncated link list from %d to %d for LLM filtering",
            len(link_pairs), _MAX_LINKS_FOR_LLM,
        )

    # Build numbered link list for prompt
    lines: List[str] = []
    for idx, (url, text) in enumerate(truncated, 1):
        display = text if text else "(no text)"
        lines.append(f"{idx}. [{display}]({url})")
    link_list_text = "\n".join(lines)

    prompt_template = load_prompt("filter_index_links.txt")
    prompt = prompt_template.format(
        base_url=base_url,
        link_count=len(truncated),
        link_list=link_list_text,
    )

    logger.info(
        "[LLM Filter] Asking LLM to evaluate %d links from %s",
        len(truncated), base_url,
    )

    try:
        response = router.generate(prompt, FilteredLinks)

        if not response.text:
            logger.warning("LLM returned empty response for link filtering")
            return [u for u, _ in truncated]

        result = FilteredLinks.model_validate_json(response.text)

        # Build url set from original pairs for validation
        valid_urls = {u for u, _ in truncated}
        filtered = [u for u in result.urls if u in valid_urls]

        logger.info(
            "[LLM Filter] Selected %d/%d links as course detail pages",
            len(filtered), len(truncated),
        )
        return filtered

    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "LLM link filtering failed (%s). "
            "Falling back to all %d links.",
            exc, len(truncated),
        )
        return [u for u, _ in truncated]


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
