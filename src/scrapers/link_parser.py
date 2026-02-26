"""
Link parser module for URL extraction and page type detection.

Contains functionality for extracting links from markdown content
and determining whether a page is an index or detail page.
"""

import logging
import re
from typing import List
from urllib.parse import urljoin

from src.models.scraper_models import PageType

logger = logging.getLogger(__name__)


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
