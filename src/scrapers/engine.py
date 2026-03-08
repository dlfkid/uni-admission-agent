"""
Smart Scraping Engine for university admission pages.

Uses crawl4ai with stealth browsing to fetch pages, converts to Markdown,
and integrates with RouterAgent/LLMCleanerAgent for structured data extraction.
Supports dynamic crawl depth with LLM-driven heuristic scouting.
"""

import asyncio
import logging
import random
from pathlib import Path
from typing import Dict, List, Optional, Set, cast

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, CrawlResult
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

from src.agents.cleaner_agent import LLMCleanerAgent
from src.agents.factory import RouterAgent, create_router
from src.core.environment import ScraperError
from src.models.scraper_models import CrawlPageResult, PageType
from src.scrapers.helpers import extract_program_name, save_markdown, save_html_debug
from src.scrapers.link_parser import (
    extract_links,
    extract_links_with_text,
    filter_links_by_llm,
    detect_page_type,
)
from src.scrapers.page_processor import process_page_for_program, process_pages_batch
from src.scrapers.scout import run_scout, print_scout_report
from src.storage.db_manager import DatabaseManager
from src.utils.pdf_processor import PDFProcessor, PDFProcessingError

logger = logging.getLogger(__name__)


def _unwrap_retry_error(exc: RetryError) -> Exception:
    """Extract the last underlying exception from a tenacity RetryError."""
    cause = exc.last_attempt.exception()
    return cause if cause is not None else exc


class AdmissionScraper:
    """
    Intelligent scraper for university admission pages.

    Uses crawl4ai + playwright stealth to fetch pages and convert to Markdown.
    Integrates with RouterAgent for link extraction and LLMCleanerAgent
    for structured data parsing.
    """

    def __init__(self, router: Optional[RouterAgent] = None) -> None:
        self.router = router if router is not None else create_router()

        self.browser_config = BrowserConfig(
            enable_stealth=True,
            headless=True,
            verbose=False,
        )

        # JS snippet to auto-dismiss cookie-consent overlays
        # before they can redirect the browser away from the target page.
        _dismiss_cookie_js = """
        (function() {
            const sels = [
                'button[id*="cookie" i]', 'button[class*="cookie" i]',
                'a[id*="cookie" i]',     'a[class*="cookie" i]',
                'button[id*="consent" i]','button[class*="consent" i]',
                'button[id*="accept" i]', 'button[class*="accept" i]',
                '[data-cookiebanner] button',
                '.cookie-banner button', '#cookie-banner button',
            ];
            for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                    const txt = (el.textContent || '').toLowerCase();
                    if (/accept|agree|ok|got it|i.m ok/i.test(txt)) {
                        el.click(); return;
                    }
                }
            }
        })();
        """

        self.crawler_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            simulate_user=False,
            remove_overlay_elements=True,
            wait_until="domcontentloaded",
            delay_before_return_html=2.0,
            js_code=_dismiss_cookie_js,
            page_timeout=60000,
            verbose=False,
        )

        # Session state
        self._visited_urls: Set[str] = set()
        self._scout_call_count: int = 0
        self._all_scouted_links: List = []
        self._failed_urls: List[str] = []
        self._export_md: bool = False
        self._export_path: Optional[str] = None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=90))
    async def crawl_page(self, url: str) -> CrawlPageResult:
        """Crawl a single URL and return its content as Markdown."""
        logger.info("Crawling: %s", url)

        await asyncio.sleep(random.uniform(0.5, 2.0))

        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            raw_result = await crawler.arun(url=url, config=self.crawler_config)
            from crawl4ai.models import CrawlResultContainer
            container = cast("CrawlResultContainer[CrawlResult]", raw_result)
            result: CrawlResult = container[0]

            if not result.success:
                error_msg = result.error_message or "Unknown crawl error"
                logger.error("Crawl failed for %s: %s", url, error_msg)
                raise ScraperError(f"Failed to crawl {url}: {error_msg}")

            self._debug_crawl_result(result, url)

            raw_markdown = result.markdown.raw_markdown if result.markdown else ""
            page_links = self._extract_page_links(result)

            if self._export_md and self._export_path and raw_markdown:
                save_markdown(self._export_path, url, raw_markdown)

            return CrawlPageResult(
                url=url,
                markdown=raw_markdown,
                char_count=len(raw_markdown),
                links=page_links,
                status_code=result.status_code,
                html=result.html,
            )

    def _debug_crawl_result(self, result: CrawlResult, url: str) -> None:
        """Log debug info and save HTML if markdown conversion seems poor."""
        if result.html and len(result.html) > 1000:
            md_len = len(result.markdown.raw_markdown) if result.markdown else 0
            if md_len < 100:
                logger.warning("HTML: %d bytes, markdown: %d bytes", len(result.html), md_len)
                if self._export_md and self._export_path:
                    save_html_debug(self._export_path, url, result.html)

    def _extract_page_links(self, result: CrawlResult) -> List[str]:
        """Extract links from crawl result."""
        page_links: List[str] = []
        if result.links:
            for link_item in result.links.get("external", []) + result.links.get("internal", []):
                href = link_item.get("href", "")
                if href:
                    page_links.append(href)
        return page_links

    async def crawl_and_clean(
        self,
        url: str,
        univ_slug: str,
        year: int,
        continue_depth: int = 0,
        page_type_hint: str = "auto",
        export_md: bool = False,
        export_path: Optional[str] = None,
        html_content: Optional[str] = None,
    ) -> int:
        """
        Full pipeline: crawl → extract links → parse → upsert to database.

        Returns:
            Number of programs successfully imported.
        """
        self._reset_session_state()
        self._export_md = export_md
        self._export_path = export_path
        
        if export_md and export_path:
            Path(export_path).mkdir(parents=True, exist_ok=True)
            logger.info("Markdown export enabled: %s", export_path)

        if not url:
            logger.error("No URL provided for crawl.")
            return 0

        probe_result = await self._prepare_probe_result(url, html_content)
        if probe_result is None:
            return 0

        is_index = self._determine_page_type(probe_result, page_type_hint)

        # Browser HTML + DETAIL page: process directly
        # Use asyncio.to_thread to avoid blocking the event loop during LLM calls
        if html_content and probe_result and not is_index:
            imported = await asyncio.to_thread(
                self._process_browser_html, probe_result, univ_slug, year
            )
        else:
            imported = await self._crawl_depth(
                urls=[url],
                univ_slug=univ_slug,
                year=year,
                current_depth=0,
                max_continue=continue_depth,
                is_index_layer=is_index,
            )

        if imported == 0 and self._all_scouted_links:
            print_scout_report(
                univ_slug, year, 2 + continue_depth, imported,
                self._visited_urls, self._failed_urls, self._all_scouted_links,
            )

        logger.info("Crawl pipeline complete: %d programs imported for %s", imported, univ_slug)
        return imported

    def analyze_page_links(
        self, url: str, html_content: str, page_type_hint: str = "auto",
    ) -> Dict:
        """Analyze an index page and return candidate detail-page links.

        Returns a dict with ``page_type`` (``'index'`` | ``'detail'``) and
        a ``links`` list of ``{url, text}`` dicts.  ``total_found`` gives the
        count of links before LLM filtering.
        """
        probe = self._create_result_from_browser_html(url, html_content)
        is_index = self._determine_page_type(probe, page_type_hint)

        if not is_index:
            return {"page_type": "detail", "links": [], "total_found": 0}

        link_pairs = extract_links_with_text(probe.markdown, url)
        total_found = len(link_pairs)

        if not link_pairs:
            return {"page_type": "index", "links": [], "total_found": 0}

        filtered_urls = filter_links_by_llm(self.router, link_pairs, url)

        url_to_text: Dict[str, str] = dict(link_pairs)
        links = [
            {"url": u, "text": url_to_text.get(u, "")}
            for u in filtered_urls
        ]
        return {"page_type": "index", "links": links, "total_found": total_found}

    async def crawl_selected_urls(
        self,
        urls: List[str],
        univ_slug: str,
        year: int,
        export_md: bool = False,
        export_path: Optional[str] = None,
    ) -> int:
        """Crawl a user-curated list of URLs as detail pages.

        Each page is parsed and inserted into the database immediately.
        """
        self._reset_session_state()
        self._export_md = export_md
        self._export_path = export_path

        if export_md and export_path:
            Path(export_path).mkdir(parents=True, exist_ok=True)
            logger.info("Markdown export enabled: %s", export_path)

        logger.info(
            "Crawling %d user-selected detail URLs for %s/%d",
            len(urls), univ_slug, year,
        )
        return await self._crawl_depth(
            urls=urls,
            univ_slug=univ_slug,
            year=year,
            current_depth=0,
            max_continue=0,
            is_index_layer=False,
        )

    def _reset_session_state(self) -> None:
        """Reset session state for a new crawl."""
        self._visited_urls = set()
        self._scout_call_count = 0
        self._all_scouted_links = []
        self._failed_urls = []

    async def _prepare_probe_result(
        self, url: str, html_content: Optional[str]
    ) -> Optional[CrawlPageResult]:
        """Prepare probe result from browser HTML or by crawling."""
        if html_content:
            return self._create_result_from_browser_html(url, html_content)
        return None

    def _create_result_from_browser_html(self, url: str, html_content: str) -> CrawlPageResult:
        """Create CrawlPageResult from browser-provided HTML."""
        logger.info("Using pre-rendered HTML from browser (length: %d bytes)", len(html_content))
        
        raw_markdown = html_content
        try:
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
            md_generator = DefaultMarkdownGenerator()
            markdown_obj = md_generator.generate_markdown(input_html=html_content, base_url=url)
            if markdown_obj and hasattr(markdown_obj, 'raw_markdown'):
                raw_markdown = markdown_obj.raw_markdown
                logger.info("Converted browser HTML to markdown: %d chars", len(raw_markdown))
        except Exception as e:
            logger.warning("Failed to convert HTML to markdown: %s", e)
            
        result = CrawlPageResult(
            url=url, markdown=raw_markdown, char_count=len(raw_markdown),
            links=extract_links(raw_markdown, url), status_code=200, html=html_content,
        )
        
        if self._export_md and self._export_path and raw_markdown != html_content:
            save_markdown(self._export_path, url, raw_markdown)
            
        return result

    def _determine_page_type(
        self, probe_result: Optional[CrawlPageResult], page_type_hint: str
    ) -> bool:
        """Determine if page is index (True) or detail (False)."""
        if page_type_hint == "index":
            logger.info("Page type manually set to: INDEX")
            return True
        if page_type_hint == "detail":
            logger.info("Page type manually set to: DETAIL")
            return False
        
        if probe_result and probe_result.markdown:
            page_type = detect_page_type(
                probe_result.markdown,
                len(probe_result.links),
                page_url=probe_result.url,
            )
            is_index = (page_type == PageType.INDEX)
            logger.info("Entry Point detected as: %s", page_type.value.upper())
            return is_index
        return False

    def _process_browser_html(
        self, probe_result: CrawlPageResult, univ_slug: str, year: int
    ) -> int:
        """Process browser-provided HTML as a DETAIL page."""
        logger.info("Processing browser-provided HTML as DETAIL page (skipping URL crawl)")
        
        cleaner = LLMCleanerAgent(router=self.router)
        db_manager = DatabaseManager()
        
        success, _ = process_page_for_program(
            page=probe_result,
            cleaner=cleaner,
            db_manager=db_manager,
            univ_slug=univ_slug,
            year=year,
            current_depth=0,
            from_browser=True,
        )
        return 1 if success else 0

    async def _crawl_depth(
        self,
        urls: List[str],
        univ_slug: str,
        year: int,
        current_depth: int,
        max_continue: int,
        is_index_layer: bool,
    ) -> int:
        """Recursively crawl and parse pages with depth tracking."""
        urls_to_crawl = [u for u in urls if u not in self._visited_urls]
        if not urls_to_crawl:
            return 0

        for u in urls_to_crawl:
            self._visited_urls.add(u)

        logger.info("[Depth %d] Crawling %d pages...", current_depth, len(urls_to_crawl))

        page_results = await self._crawl_urls(urls_to_crawl)

        if is_index_layer:
            return await self._handle_index_layer(
                page_results, univ_slug, year, current_depth, max_continue
            )

        return await self._handle_detail_layer(
            page_results, univ_slug, year, current_depth, max_continue
        )

    async def _crawl_urls(self, urls: List[str]) -> List[CrawlPageResult]:
        """Crawl a list of URLs, handling PDFs and errors."""
        pdf_processor = PDFProcessor()
        page_results: List[CrawlPageResult] = []
        
        for link in urls:
            try:
                if link.lower().endswith(".pdf"):
                    logger.info("Detected PDF link: %s", link)
                    pdf_result = pdf_processor.convert_to_markdown(link)
                    page_results.append(CrawlPageResult(
                        url=link, markdown=pdf_result.markdown, char_count=pdf_result.char_count,
                    ))
                else:
                    page_results.append(await self.crawl_page(link))
            except RetryError as exc:
                logger.warning("Skipping %s after retries: %s", link, _unwrap_retry_error(exc))
                self._failed_urls.append(link)
            except (ScraperError, PDFProcessingError) as e:
                logger.warning("Skipping %s: %s", link, e)
        
        return page_results

    async def _handle_index_layer(
        self,
        page_results: List[CrawlPageResult],
        univ_slug: str,
        year: int,
        current_depth: int,
        max_continue: int,
    ) -> int:
        """Handle index layer: extract links, filter via LLM, then recurse."""
        all_link_pairs: list[tuple[str, str]] = []
        for page in page_results:
            if page.markdown:
                all_link_pairs.extend(
                    extract_links_with_text(page.markdown, page.url),
                )

        if not all_link_pairs:
            logger.info("No links extracted from index. Treating as single program page.")
            return await self._handle_detail_layer(
                page_results, univ_slug, year, current_depth, max_continue,
            )

        # Ask the LLM which links are likely course detail pages
        # Use asyncio.to_thread to avoid blocking the event loop during LLM calls
        base_url = page_results[0].url if page_results else ""
        detail_links = await asyncio.to_thread(
            filter_links_by_llm,
            self.router,
            all_link_pairs,
            base_url,
        )

        if not detail_links:
            # Fallback: if LLM returned nothing, use all regex-extracted links
            logger.warning(
                "LLM filter returned 0 links. "
                "Falling back to all %d regex-extracted links.",
                len(all_link_pairs),
            )
            detail_links = [u for u, _ in all_link_pairs]

        logger.info(
            "[Index] LLM selected %d/%d links as course detail pages",
            len(detail_links), len(all_link_pairs),
        )

        return await self._crawl_depth(
            urls=detail_links,
            univ_slug=univ_slug,
            year=year,
            current_depth=current_depth + 1,
            max_continue=max_continue,
            is_index_layer=False,
        )

    async def _handle_detail_layer(
        self,
        page_results: List[CrawlPageResult],
        univ_slug: str,
        year: int,
        current_depth: int,
        max_continue: int,
    ) -> int:
        """Handle detail layer: parse pages and optionally scout deeper."""
        # Use asyncio.to_thread to avoid blocking the event loop during LLM calls
        total_imported, scout_candidates, failed_urls = await asyncio.to_thread(
            process_pages_batch,
            page_results,
            self.router,
            univ_slug,
            year,
            current_depth,
        )
        self._failed_urls.extend(failed_urls)

        if max_continue > 0 and scout_candidates:
            # Use asyncio.to_thread to avoid blocking the event loop during LLM calls
            deeper_urls, self._scout_call_count, self._all_scouted_links = await asyncio.to_thread(
                run_scout,
                self.router,
                scout_candidates,
                self._visited_urls,
                self._scout_call_count,
                self._all_scouted_links,
            )
            
            if deeper_urls:
                first_candidate = scout_candidates[0]
                detected_type = detect_page_type(first_candidate.markdown, len(first_candidate.links))
                is_deeper_index = (detected_type == PageType.INDEX)
                
                logger.info(
                    "[Scout] Diving deeper with %d links (continue: %d → %d)",
                    len(deeper_urls), max_continue, max_continue - 1,
                )
                total_imported += await self._crawl_depth(
                    urls=deeper_urls,
                    univ_slug=univ_slug,
                    year=year,
                    current_depth=current_depth + 1,
                    max_continue=max_continue - 1,
                    is_index_layer=is_deeper_index,
                )

        return total_imported
