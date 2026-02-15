"""
Smart Scraping Engine for university admission pages.

Uses crawl4ai with stealth browsing to fetch pages, converts to Markdown,
and integrates with RouterAgent/LLMCleanerAgent for structured data extraction.
Supports dynamic crawl depth with LLM-driven heuristic scouting.
"""


import asyncio
import logging
import random
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set, cast
from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, CrawlResult

from tenacity import retry, stop_after_attempt, wait_exponential

from src.agents.cleaner_agent import LLMCleanerAgent, ParsedProgramData

from src.agents.factory import RouterAgent, create_router
from src.core.environment import ScraperError
from src.utils.text import generate_program_group_code
from src.models.scraper_models import (
    CrawlPageResult,
    PageType,
    ScoutedLink,
    ScoutedLinks,
    ScoutReport,
)
from src.storage.db_manager import DatabaseManager
from src.utils.pdf_processor import PDFProcessor, PDFProcessingError

logger = logging.getLogger(__name__)

# --- Constants ---

MAX_SCOUT_CALLS = 5  # Hard cap on LLM scout calls per crawl session
MAX_MARKDOWN_CHARS = 30000  # Truncate Markdown before sending to LLM

# --- Prompt Loading ---

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agents" / "prompts"


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = _PROMPTS_DIR / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


# --- Core Scraper ---


class AdmissionScraper:
    """
    Intelligent scraper for university admission pages.

    Uses crawl4ai + playwright stealth to fetch pages and convert to Markdown.
    Integrates with RouterAgent for link extraction and LLMCleanerAgent
    for structured data parsing. Supports dynamic crawl depth with
    LLM-driven heuristic scouting via --continue.
    """

    def __init__(
        self,
        router: Optional[RouterAgent] = None,
    ) -> None:
        if router is not None:
            self.router = router
        else:
            self.router = create_router()

        # Browser configuration: stealth + headless
        self.browser_config = BrowserConfig(
            enable_stealth=True,
            headless=True,
            verbose=False,
        )

        # Crawler run configuration
        self.crawler_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            simulate_user=True,
            wait_until="domcontentloaded",
            page_timeout=60000,
            verbose=False,
        )

        # Session state for depth-aware crawling
        self._visited_urls: Set[str] = set()
        self._scout_call_count: int = 0
        self._all_scouted_links: List[ScoutedLink] = []
        self._failed_urls: List[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=90),
    )
    async def crawl_page(self, url: str) -> CrawlPageResult:
        """
        Crawl a single URL and return its content as Markdown.

        Uses crawl4ai AsyncWebCrawler with stealth browsing.
        Automatically converts HTML → Markdown to reduce token cost.

        Args:
            url: The URL to crawl.

        Returns:
            CrawlPageResult with Markdown content, char count, and links.

        Raises:
            ScraperError: If the page cannot be fetched after retries.
        """
        logger.info("Crawling: %s", url)

        # Human-like delay before request
        delay = random.uniform(0.5, 2.0)
        await asyncio.sleep(delay)

        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            raw_result = await crawler.arun(
                url=url,
                config=self.crawler_config,
            )
            # arun returns Union[CrawlResultContainer, AsyncGenerator];
            # single-URL calls always return CrawlResultContainer.
            from crawl4ai.models import CrawlResultContainer
            container = cast("CrawlResultContainer[CrawlResult]", raw_result)
            result: CrawlResult = container[0]

            if not result.success:
                error_msg = result.error_message or "Unknown crawl error"
                logger.error("Crawl failed for %s: %s", url, error_msg)
                raise ScraperError(f"Failed to crawl {url}: {error_msg}")

            # Extract Markdown (strips scripts/CSS automatically)
            markdown_result = result.markdown
            raw_markdown = ""
            if markdown_result is not None:
                raw_markdown = markdown_result.raw_markdown

            # Extract links from crawl result
            page_links: List[str] = []
            if result.links:
                external = result.links.get("external", [])
                internal = result.links.get("internal", [])
                for link_item in external + internal:
                    href = link_item.get("href", "")
                    if href:
                        page_links.append(href)

            char_count = len(raw_markdown)
            logger.info(
                "Crawled %s: %s chars, %d links found",
                url, f"{char_count:,}", len(page_links),
            )

            return CrawlPageResult(
                url=url,
                markdown=raw_markdown,
                char_count=char_count,
                links=page_links,
                status_code=result.status_code,
            )

    def extract_links(self, markdown: str, base_url: str) -> List[str]:
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

        found_links: Set[str] = set()
        
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
                if any(ext in lower_link for ext in [".css", ".js", ".png", ".jpg", ".jpeg", ".ico", ".svg", ".woff", ".ttf"]):
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

    def scout_links(
        self, markdown: str, links: List[str], base_url: str,
    ) -> List[ScoutedLink]:
        """
        Use LLM to evaluate links for high-value admission content.

        Called when a detail page yields no structured data, to identify
        links worth exploring at a deeper level.

        Args:
            markdown: Markdown content of the page (truncated).
            links: All links found on the page.
            base_url: Base URL for resolution context.

        Returns:
            Top-3 ScoutedLink candidates sorted by confidence.
        """
        if self._scout_call_count >= MAX_SCOUT_CALLS:
            logger.warning(
                "Scout call limit reached (%d/%d). Skipping.",
                self._scout_call_count, MAX_SCOUT_CALLS,
            )
            return []

        self._scout_call_count += 1

        # Build link list text for prompt
        link_list_text = "\n".join(
            f"- {link}" for link in links[:50]  # Cap at 50 links
        )
        markdown_summary = markdown[:3000]  # Limit to save tokens

        prompt_template = _load_prompt("scout_links.txt")
        prompt = prompt_template.format(
            base_url=base_url,
            markdown_summary=markdown_summary,
            link_list=link_list_text,
        )

        logger.info(
            "Scout evaluation (%d/%d): analyzing %d links from %s",
            self._scout_call_count, MAX_SCOUT_CALLS,
            len(links), base_url,
        )

        try:
            response = self.router.generate(prompt, ScoutedLinks)

            if not response.text:
                return []

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

            return scouted.links

        except Exception as e:
            logger.error("Scout evaluation failed: %s", e)
            return []

    def detect_page_type(
        self, markdown: str, link_count: int,
    ) -> PageType:
        """Determines if a page is an INDEX or DETAIL page using heuristics.
        
        Optimization: Replaced LLM call with content & link density check.
        Strong content signals ("Tuition", "Deadline") => DETAIL.
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
            logger.info(f"Page Type Detection: INDEX (Links={link_count} > {threshold})")
            return PageType.INDEX
        
        logger.info(f"Page Type Detection: DETAIL (Links={link_count} <= {threshold})")
        return PageType.DETAIL

    async def crawl_and_clean(
        self,
        url: str,
        univ_slug: str,
        year: int,
        continue_depth: int = 0,
    ) -> int:
        """
        Full pipeline: crawl page → extract links → crawl detail pages →
        parse with LLMCleanerAgent → upsert to database.

        Supports dynamic depth via --continue flag. When a detail page
        yields no data and continue_depth > 0, the Heuristic Scout
        evaluates links for deeper exploration.

        Args:
            url: Starting URL (e.g., a program listing page).
            univ_slug: University slug for DB association.
            year: Academic year.
            continue_depth: Extra depth levels allowed (default 0).

        Returns:
            Number of programs successfully imported.
        """
        # Reset session state
        self._visited_urls = set()
        self._scout_call_count = 0
        self._all_scouted_links = []
        self._failed_urls = []

        if not url:
             logger.error("No URL provided for crawl.")
             return 0

        # Probe the entry URL to detect type
        logger.info("Probing entry URL to detect page type: %s", url)
        try:
            probe_result = await self.crawl_page(url)
        except ScraperError:
             logger.error("Failed to probe entry URL: %s", url)
             return 0

        if not probe_result.markdown:
             logger.error("Entry URL yielded no content: %s", url)
             return 0

        # Detect type
        page_type = self.detect_page_type(
            markdown=probe_result.markdown,
            link_count=len(probe_result.links),
        )
        
        # If detected as DETAIL, we set is_index_layer=False
        # This tells _crawl_depth to skip link extraction and parse the page directly.
        is_index = (page_type == PageType.INDEX)
        
        logger.info(
            "Entry Point detected as: %s (is_index_layer=%s)", 
            page_type.value.upper(), is_index
        )
        
        # Reset visited so _crawl_depth can process it again (it dedups against visited)
        # Since we just probed it, we might want to keep it in visited?
        # No, _crawl_depth expects to crawl it. 
        # But wait, we just crawled it. 
        # Optimization: We can pass the probe result to avoid re-crawling?
        # _crawl_depth implementation doesn't support passing results currently.
        # For now, let it re-crawl (simpler change). The crawler caches if enabled, 
        # but here we use CacheMode.BYPASS.
        # However, re-crawling is robust.

        imported = await self._crawl_depth(
            urls=[url],
            univ_slug=univ_slug,
            year=year,
            current_depth=0,
            max_continue=continue_depth,
            is_index_layer=is_index,
        )

        # --- Scout Report (Human-in-the-loop) ---
        if imported == 0 and self._all_scouted_links:
            self._print_scout_report(
                univ_slug=univ_slug,
                year=year,
                depth_reached=2 + continue_depth,
                imported=imported,
            )

        logger.info(
            "Crawl pipeline complete: %d programs imported for %s",
            imported, univ_slug,
        )
        return imported

    # ------------------------------------------------------------------
    # Internal — Depth-Aware Crawl
    # ------------------------------------------------------------------

    async def _crawl_depth(
        self,
        urls: List[str],
        univ_slug: str,
        year: int,
        current_depth: int,
        max_continue: int,
        is_index_layer: bool,
    ) -> int:
        """
        Recursively crawl and parse pages with depth tracking.

        Args:
            urls: URLs to crawl at this depth.
            univ_slug: University slug.
            year: Academic year.
            current_depth: Current recursion depth (0 = index page).
            max_continue: Remaining continue depth budget.
            is_index_layer: True if this is the index (L1) layer.

        Returns:
            Total programs imported across this depth and deeper.
        """
        pdf_processor = PDFProcessor()
        cleaner = LLMCleanerAgent(router=self.router)
        db_manager = DatabaseManager()
        total_imported = 0

        # Dedup URLs
        urls_to_crawl = [
            u for u in urls if u not in self._visited_urls
        ]
        if not urls_to_crawl:
            return 0

        # Mark as visited
        for u in urls_to_crawl:
            self._visited_urls.add(u)

        logger.info(
            "[Depth %d] Crawling %d pages...",
            current_depth, len(urls_to_crawl),
        )

        # --- Crawl pages ---
        page_results: List[CrawlPageResult] = []
        for link in urls_to_crawl:
            try:
                if link.lower().endswith(".pdf"):
                    logger.info("Detected PDF link: %s", link)
                    pdf_result = pdf_processor.convert_to_markdown(link)
                    page_results.append(
                        CrawlPageResult(
                            url=link,
                            markdown=pdf_result.markdown,
                            char_count=pdf_result.char_count,
                        )
                    )
                else:
                    detail = await self.crawl_page(link)
                    page_results.append(detail)
            except (ScraperError, PDFProcessingError) as e:
                logger.warning("Skipping %s: %s", link, e)

        # --- Index layer: extract detail links ---
        if is_index_layer:
            all_detail_links: List[str] = []
            for page in page_results:
                if not page.markdown:
                    continue
                detail_links = self.extract_links(
                    markdown=page.markdown,
                    base_url=page.url,
                )
                all_detail_links.extend(detail_links)

            if not all_detail_links:
                logger.info(
                    "No detail links extracted. "
                    "Treating starting page as single program page."
                )
                # Fall through to parse the index page itself
            else:
                # Recurse into detail pages
                return await self._crawl_depth(
                    urls=all_detail_links,
                    univ_slug=univ_slug,
                    year=year,
                    current_depth=current_depth + 1,
                    max_continue=max_continue,
                    is_index_layer=False,
                )

        # --- Detail layer: parse each page ---
        scout_candidates: List[CrawlPageResult] = []

        for page in page_results:
            if not page.markdown:
                continue

            try:
                parsed: Optional[ParsedProgramData] = cleaner.clean_markdown(
                    markdown=page.markdown,
                    source_url=page.url,
                )
                if parsed is None:
                    logger.warning(
                        "No structured data from %s", page.url,
                    )
                    self._failed_urls.append(page.url)
                    scout_candidates.append(page)
                    continue

                # Build program data for DB
                program_data: Dict[str, object] = {
                    "academic_year": year,
                    "name_en": _extract_program_name(page.markdown), # Re-extract to ensure we use what we passed to DB
                    "name_zh": "",
                }

                if parsed.faculty:
                    program_data["faculty"] = parsed.faculty

                if parsed.tuition:
                    program_data["tuition_amount"] = parsed.tuition.amount
                    program_data["currency"] = parsed.tuition.currency

                if parsed.study_options:
                    program_data["study_options"] = [
                        opt.model_dump(mode="json")
                        for opt in parsed.study_options
                    ]

                if parsed.deadlines:
                    # Sort by date
                    sorted_deadlines = sorted(
                        parsed.deadlines,
                        key=lambda x: x.cutoff_date or datetime.max,
                    )
                    program_data["deadlines"] = []
                    for i, d in enumerate(sorted_deadlines, 1):
                        d_dict = d.model_dump(mode="json")
                        d_dict["round"] = i
                        program_data["deadlines"].append(d_dict)

                # --- Deterministic program_group_code (local) ---
                name_en = program_data.get("name_en")
                if name_en:
                    code = generate_program_group_code(univ_slug, str(name_en))
                    program_data["program_group_code"] = code
                    logger.info(
                        "[New Program] 检测到新学科: %s，已分配 ID: %s",
                        name_en, code,
                    )

                extra_metadata: Dict[str, object] = {
                    "source_url": page.url,
                    "crawl_depth": current_depth,
                }
                program_data["extra_metadata"] = extra_metadata

                name_en = program_data.get("name_en")
                if not name_en:
                    logger.warning(
                        "Could not extract program name from %s",
                        page.url,
                    )
                    self._failed_urls.append(page.url)
                    scout_candidates.append(page)
                    continue

                _, created = db_manager.upsert_program(
                    program_data,  # type: ignore[arg-type]
                    univ_slug,
                )
                total_imported += 1
                action = "Inserted" if created else "Updated"
                logger.info(
                    f"{action}: {program_data['name_en']} ({year}) [Group: {program_data.get('program_group_code')}]",
                )

            except Exception as e:
                logger.error("Failed to process %s: %s", page.url, e)
                self._failed_urls.append(page.url)
                scout_candidates.append(page)

        # --- Heuristic Scout: deeper exploration ---
        if max_continue > 0 and scout_candidates:
            deeper_urls = self._run_scout(scout_candidates)
            if deeper_urls:
                # Detect if scouted pages are index or detail
                first_candidate = scout_candidates[0]
                detected_type = self.detect_page_type(
                    markdown=first_candidate.markdown,
                    link_count=len(first_candidate.links),
                )
                is_deeper_index = (detected_type == PageType.INDEX)

                logger.info(
                    "[Scout] Diving deeper with %d links as %s layer "
                    "(continue budget: %d → %d)",
                    len(deeper_urls),
                    "INDEX" if is_deeper_index else "DETAIL",
                    max_continue, max_continue - 1,
                )
                deeper_imported = await self._crawl_depth(
                    urls=deeper_urls,
                    univ_slug=univ_slug,
                    year=year,
                    current_depth=current_depth + 1,
                    max_continue=max_continue - 1,
                    is_index_layer=is_deeper_index,
                )
                total_imported += deeper_imported

        return total_imported

    def _run_scout(
        self, candidates: List[CrawlPageResult],
    ) -> List[str]:
        """Run Heuristic Scout on failed pages and collect deeper URLs."""
        deeper_urls: List[str] = []

        for page in candidates:
            if self._scout_call_count >= MAX_SCOUT_CALLS:
                break

            scouted = self.scout_links(
                markdown=page.markdown,
                links=page.links,
                base_url=page.url,
            )

            self._all_scouted_links.extend(scouted)

            for sl in scouted:
                if sl.url not in self._visited_urls:
                    deeper_urls.append(sl.url)

        return deeper_urls

    def _print_scout_report(
        self,
        univ_slug: str,
        year: int,
        depth_reached: int,
        imported: int,
    ) -> None:
        """Print a terminal Scout Report for human review."""
        report = ScoutReport(
            explored_urls=sorted(self._visited_urls),
            failed_urls=self._failed_urls,
            scouted_links=self._all_scouted_links,
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
                if sl.url not in self._visited_urls
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


# --- Helpers ---


def _split_markdown_chunks(
    markdown: str, max_chars: int,
) -> List[str]:
    """Split Markdown into chunks that fit within the LLM context window.

    Splits on double-newline (paragraph) boundaries to avoid cutting
    mid-link or mid-sentence. Falls back to hard split if no paragraph
    break is found within the chunk.

    Args:
        markdown: Full Markdown content.
        max_chars: Maximum characters per chunk.

    Returns:
        List of Markdown chunks, each ≤ max_chars.
    """
    if len(markdown) <= max_chars:
        return [markdown]

    chunks: List[str] = []
    remaining = markdown

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        # Find a paragraph break near the end of the chunk
        slice_end = remaining[:max_chars]
        split_pos = slice_end.rfind("\n\n")

        if split_pos < max_chars // 2:
            # No good paragraph break found — try single newline
            split_pos = slice_end.rfind("\n")

        if split_pos < max_chars // 2:
            # No newline at all — hard split
            split_pos = max_chars

        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")

    logger.info(
        "Split %s chars into %d chunks",
        f"{len(markdown):,}", len(chunks),
    )
    return chunks





def _extract_program_name(markdown: str) -> str:
    """
    Best-effort extraction of program name from Markdown.

    Looks for the first H1 or H2 heading as a heuristic.
    """
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.lstrip("# ").strip()
        if stripped.startswith("## "):
            return stripped.lstrip("# ").strip()
    return ""
