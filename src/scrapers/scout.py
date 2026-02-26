"""
Scout module for heuristic link exploration.

Contains functionality for LLM-driven evaluation of links
for potential admission content and reporting.
"""

import logging
from typing import List, Set
from urllib.parse import urljoin

from src.agents.factory import RouterAgent
from src.models.scraper_models import (
    CrawlPageResult,
    ScoutedLink,
    ScoutedLinks,
    ScoutReport,
)
from src.scrapers.helpers import load_prompt

logger = logging.getLogger(__name__)

# --- Constants ---
MAX_SCOUT_CALLS = 5  # Hard cap on LLM scout calls per crawl session


def scout_links(
    router: RouterAgent,
    markdown: str,
    links: List[str],
    base_url: str,
    scout_call_count: int,
) -> tuple[List[ScoutedLink], int]:
    """
    Use LLM to evaluate links for high-value admission content.

    Called when a detail page yields no structured data, to identify
    links worth exploring at a deeper level.

    Args:
        router: RouterAgent for LLM calls.
        markdown: Markdown content of the page (truncated).
        links: All links found on the page.
        base_url: Base URL for resolution context.
        scout_call_count: Current scout call count.

    Returns:
        Tuple of (ScoutedLink candidates, updated scout_call_count).
    """
    if scout_call_count >= MAX_SCOUT_CALLS:
        logger.warning(
            "Scout call limit reached (%d/%d). Skipping.",
            scout_call_count, MAX_SCOUT_CALLS,
        )
        return [], scout_call_count

    scout_call_count += 1

    # Build link list text for prompt
    link_list_text = "\n".join(
        f"- {link}" for link in links[:50]  # Cap at 50 links
    )
    markdown_summary = markdown[:3000]  # Limit to save tokens

    prompt_template = load_prompt("scout_links.txt")
    prompt = prompt_template.format(
        base_url=base_url,
        markdown_summary=markdown_summary,
        link_list=link_list_text,
    )

    logger.info(
        "Scout evaluation (%d/%d): analyzing %d links from %s",
        scout_call_count, MAX_SCOUT_CALLS,
        len(links), base_url,
    )

    try:
        response = router.generate(prompt, ScoutedLinks)

        if not response.text:
            return [], scout_call_count

        scouted = ScoutedLinks.model_validate_json(response.text)

        # Resolve relative URLs in scouted links
        for link in scouted.links:
            if not link.url.startswith(("http://", "https://")):
                link.url = urljoin(base_url, link.url)

        logger.info(
            "Scout found %d potential links", len(scouted.links),
        )
        for sl in scouted.links:
            logger.info(
                "  [%s] %s — %s",
                sl.confidence.upper(), sl.url, sl.reason,
            )

        return scouted.links, scout_call_count

    except Exception as e:
        logger.error("Scout evaluation failed: %s", e)
        return [], scout_call_count


def run_scout(
    router: RouterAgent,
    candidates: List[CrawlPageResult],
    visited_urls: Set[str],
    scout_call_count: int,
    all_scouted_links: List[ScoutedLink],
) -> tuple[List[str], int, List[ScoutedLink]]:
    """Run Heuristic Scout on failed pages and collect deeper URLs.
    
    Args:
        router: RouterAgent for LLM calls.
        candidates: Pages that failed to yield structured data.
        visited_urls: Set of already visited URLs.
        scout_call_count: Current scout call count.
        all_scouted_links: Accumulated scouted links.
        
    Returns:
        Tuple of (deeper_urls, updated_scout_call_count, updated_all_scouted_links).
    """
    deeper_urls: List[str] = []

    for page in candidates:
        if scout_call_count >= MAX_SCOUT_CALLS:
            break

        scouted, scout_call_count = scout_links(
            router=router,
            markdown=page.markdown,
            links=page.links,
            base_url=page.url,
            scout_call_count=scout_call_count,
        )

        all_scouted_links.extend(scouted)

        for sl in scouted:
            if sl.url not in visited_urls:
                deeper_urls.append(sl.url)

    return deeper_urls, scout_call_count, all_scouted_links


def print_scout_report(
    univ_slug: str,
    year: int,
    depth_reached: int,
    imported: int,
    visited_urls: Set[str],
    failed_urls: List[str],
    all_scouted_links: List[ScoutedLink],
) -> None:
    """Print a terminal Scout Report for human review."""
    report = ScoutReport(
        explored_urls=sorted(visited_urls),
        failed_urls=failed_urls,
        scouted_links=all_scouted_links,
        depth_reached=depth_reached,
        programs_imported=imported,
    )

    separator = "=" * 60
    print(f"\n{separator}")
    print("📋 SCOUT REPORT — Human Decision Required")
    print(separator)
    print(f"University:       {univ_slug}")
    print(f"Year:             {year}")
    print(f"Depth Reached:    {report.depth_reached}")
    print(f"Programs Found:   {report.programs_imported}")
    print(f"Pages Explored:   {len(report.explored_urls)}")
    print(f"Pages Failed:     {len(report.failed_urls)}")

    if report.scouted_links:
        unexplored = [
            sl for sl in report.scouted_links
            if sl.url not in visited_urls
        ]
        if unexplored:
            print(f"\n🔍 Unexplored High-Potential Links ({len(unexplored)}):")
            for i, sl in enumerate(unexplored, 1):
                print(f"  {i}. [{sl.confidence.upper()}] {sl.url}")
                print(f"     Reason: {sl.reason}")
        else:
            print("\n✅ All scouted links were explored.")
    else:
        print("\n⚠️  No high-potential links were identified by scout.")

    if report.failed_urls:
        print(f"\n❌ Failed Pages ({len(report.failed_urls)}):")
        for url in report.failed_urls:
            print(f"  - {url}")

    print(separator)
    print(
        "💡 Tip: Try running with a deeper --continue value, "
        "or manually visit the links above."
    )
    print(f"{separator}\n")
