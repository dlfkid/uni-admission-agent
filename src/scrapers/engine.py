"""
Smart Scraping Engine for university admission pages.

Uses crawl4ai with stealth browsing to fetch pages, converts to Markdown,
and integrates with LLMCleanerAgent for structured data extraction.
"""

import asyncio
import logging
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, CrawlResult
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from src.agents.cleaner_agent import LLMCleanerAgent, ParsedProgramData
from src.core.environment import ScraperError
from src.core.token_tracker import tracker
from src.models.scraper_models import CrawlPageResult, ExtractedLinks
from src.storage.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

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
    Integrates with Gemini Flash for link extraction and LLMCleanerAgent
    for structured data parsing.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        if not self.api_key:
            logger.warning(
                "GOOGLE_API_KEY/GEMINI_API_KEY not found. "
                "Link extraction will be disabled."
            )
            self.genai_client: Optional[genai.Client] = None
        else:
            self.genai_client = genai.Client(api_key=self.api_key)

        self.model_id = (
            model_id
            or os.environ.get("GEMINI_MODEL_NAME")
            or "gemini-2.0-flash-exp"
        )

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
        logger.info(f"Crawling: {url}")

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
                logger.error(f"Crawl failed for {url}: {error_msg}")
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
                f"Crawled {url}: {char_count:,} chars, "
                f"{len(page_links)} links found"
            )

            return CrawlPageResult(
                url=url,
                markdown=raw_markdown,
                char_count=char_count,
                links=page_links,
                status_code=result.status_code,
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def extract_links(
        self, markdown: str, base_url: str
    ) -> List[str]:
        """
        Use Gemini Flash to extract program detail page URLs from Markdown.

        Args:
            markdown: Markdown content of the page.
            base_url: Base URL for resolving relative links.

        Returns:
            List of absolute URLs for program detail pages.
        """
        if not self.genai_client:
            logger.warning("GenAI client unavailable. Skipping link extraction.")
            return []

        # Load and format prompt
        prompt_template = _load_prompt("extract_links.txt")
        prompt = prompt_template.format(base_url=base_url, markdown=markdown)

        # Track input characters fed to LLM
        logger.info(
            f"Extracting links via LLM: {len(markdown):,} chars of Markdown"
        )

        try:
            response = self.genai_client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractedLinks,
                ),
            )

            if not response.text:
                logger.warning("Empty response from GenAI for link extraction")
                return []

            # Track token usage
            if response.usage_metadata:
                tracker.track_usage(
                    input_tokens=response.usage_metadata.prompt_token_count or 0,
                    output_tokens=response.usage_metadata.candidates_token_count or 0,
                    model=self.model_id,
                )

            # Parse structured output
            extracted = ExtractedLinks.model_validate_json(response.text)

            # Resolve relative URLs
            resolved: List[str] = []
            for link in extracted.links:
                if link.startswith(("http://", "https://")):
                    resolved.append(link)
                else:
                    resolved.append(urljoin(base_url, link))

            logger.info(f"Extracted {len(resolved)} program links via LLM")
            return resolved

        except Exception as e:
            logger.error(f"Link extraction failed: {e}")
            raise

    async def crawl_and_clean(
        self,
        url: str,
        univ_slug: str,
        year: int,
    ) -> int:
        """
        Full pipeline: crawl page → extract links → crawl detail pages →
        parse with LLMCleanerAgent → upsert to database.

        Args:
            url: Starting URL (e.g., a program listing page).
            univ_slug: University slug for DB association.
            year: Academic year.

        Returns:
            Number of programs successfully imported.
        """
        logger.info(f"Starting crawl pipeline for {univ_slug} ({year})")

        # Step 1: Crawl the starting page
        page_result = await self.crawl_page(url)

        if not page_result.markdown:
            logger.warning(f"No Markdown content from {url}")
            return 0

        # Step 2: Extract program detail links via LLM
        detail_links = await self.extract_links(
            markdown=page_result.markdown,
            base_url=url,
        )

        if not detail_links:
            logger.info(
                "No detail links extracted. "
                "Treating starting page as single program page."
            )
            detail_links = [url]

        # Step 3: Crawl each detail page and collect Markdown
        logger.info(f"Crawling {len(detail_links)} detail pages...")
        detail_results: List[CrawlPageResult] = []

        for link in detail_links:
            try:
                detail = await self.crawl_page(link)
                detail_results.append(detail)
            except ScraperError as e:
                logger.warning(f"Skipping {link}: {e}")

        # Step 4: Parse each detail page with LLMCleanerAgent
        cleaner = LLMCleanerAgent(api_key=self.api_key, model_id=self.model_id)
        db_manager = DatabaseManager()
        imported_count = 0

        for detail in detail_results:
            if not detail.markdown:
                continue

            # Build raw data dict for cleaner agent
            raw_row: Dict[str, Any] = {
                "source_url": detail.url,
                "raw_content": detail.markdown[:5000],  # Limit to save tokens
            }

            try:
                parsed: Optional[ParsedProgramData] = cleaner.clean_row(raw_row)
                if parsed is None:
                    logger.warning(f"No structured data from {detail.url}")
                    continue

                # Build program data for DB
                program_data: Dict[str, Any] = {
                    "academic_year": year,
                    "name_en": _extract_program_name(detail.markdown),
                    "name_zh": "",
                }

                # Merge parsed fields
                if parsed.tuition:
                    program_data["tuition_amount"] = parsed.tuition.amount
                    program_data["currency"] = parsed.tuition.currency

                if parsed.study_options:
                    program_data["study_options"] = [
                        opt.model_dump(mode="json") for opt in parsed.study_options
                    ]

                if parsed.deadlines:
                    program_data["deadlines"] = [
                        d.model_dump(mode="json") for d in parsed.deadlines
                    ]

                program_data["extra_metadata"] = {"source_url": detail.url}

                # Skip if no program name could be extracted
                if not program_data["name_en"]:
                    logger.warning(
                        f"Could not extract program name from {detail.url}"
                    )
                    continue

                # Upsert to DB
                _, created = db_manager.upsert_program(program_data, univ_slug)
                imported_count += 1
                action = "Inserted" if created else "Updated"
                logger.info(
                    f"{action}: {program_data['name_en']} ({year})"
                )

            except Exception as e:
                logger.error(f"Failed to process {detail.url}: {e}")

        logger.info(
            f"Crawl pipeline complete: {imported_count}/{len(detail_results)} "
            f"programs imported for {univ_slug}"
        )
        return imported_count


# --- Helpers ---


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
