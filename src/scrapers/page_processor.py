"""
Page processor module for parsing and storing program data.

Contains functionality for processing crawled pages, extracting
structured data via LLM, and upserting to database.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from src.agents.cleaner_agent import LLMCleanerAgent, ParsedProgramData
from src.agents.factory import RouterAgent
from src.models.scraper_models import CrawlPageResult
from src.scrapers.helpers import extract_program_name
from src.storage.db_manager import DatabaseManager
from src.utils.text import generate_program_group_code

logger = logging.getLogger(__name__)


def process_page_for_program(
    page: CrawlPageResult,
    cleaner: LLMCleanerAgent,
    db_manager: DatabaseManager,
    univ_slug: str,
    year: int,
    current_depth: int,
    from_browser: bool = False,
) -> tuple[bool, Optional[str]]:
    """
    Process a single page to extract and store program data.
    
    Args:
        page: Crawled page result.
        cleaner: LLM cleaner agent for structured extraction.
        db_manager: Database manager for upserting.
        univ_slug: University slug.
        year: Academic year.
        current_depth: Current crawl depth.
        from_browser: Whether HTML came from browser extension.
        
    Returns:
        Tuple of (success: bool, error_message: Optional[str]).
    """
    if not page.markdown:
        return False, "No markdown content"

    # Determine content to use for LLM extraction
    content_for_llm = page.markdown
    content_type = "markdown"
    
    # HTML Fallback: If markdown is suspiciously short but HTML exists
    if page.html and len(page.html) > 1000:
        markdown_length = len(page.markdown)
        html_length = len(page.html)
        # If markdown is less than 5% of HTML length, use HTML instead
        if markdown_length < html_length * 0.05:
            logger.warning(
                "Markdown conversion poor (MD: %d, HTML: %d). Using HTML for LLM extraction.",
                markdown_length, html_length
            )
            content_for_llm = page.html
            content_type = "html"
    
    try:
        parsed: Optional[ParsedProgramData] = cleaner.clean_markdown(
            markdown=content_for_llm,
            source_url=page.url,
        )
        
        if content_type == "html":
            logger.info("Successfully extracted data from HTML fallback for %s", page.url)
        
        if parsed is None:
            logger.warning(
                "No structured data from %s (used %s)", page.url, content_type
            )
            return False, "No structured data extracted"

        # Build program data for DB
        program_data: Dict[str, object] = {
            "academic_year": year,
            "name_en": extract_program_name(page.markdown),
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
            # Sort by date (handle both offset-aware and offset-naive datetimes)
            def sort_key(deadline):
                if deadline.cutoff_date is None:
                    return datetime.max
                # Convert to naive datetime if offset-aware
                dt = deadline.cutoff_date
                if dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
                return dt
            
            sorted_deadlines = sorted(parsed.deadlines, key=sort_key)
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
        if from_browser:
            extra_metadata["from_browser"] = True
        program_data["extra_metadata"] = extra_metadata

        if not name_en:
            logger.warning(
                "Could not extract program name from %s",
                page.url,
            )
            return False, "Could not extract program name"

        _, created = db_manager.upsert_program(
            program_data,  # type: ignore[arg-type]
            univ_slug,
        )
        action = "Inserted" if created else "Updated"
        logger.info(
            "%s: %s (%d) [Group: %s]",
            action, program_data['name_en'], year, program_data.get('program_group_code'),
        )
        return True, None

    except Exception as e:
        logger.exception("Failed to process %s", page.url)
        return False, str(e)


def process_pages_batch(
    pages: List[CrawlPageResult],
    router: RouterAgent,
    univ_slug: str,
    year: int,
    current_depth: int,
) -> tuple[int, List[CrawlPageResult], List[str]]:
    """
    Process a batch of pages for program data extraction.
    
    Args:
        pages: List of crawled page results.
        router: RouterAgent for LLM calls.
        univ_slug: University slug.
        year: Academic year.
        current_depth: Current crawl depth.
        
    Returns:
        Tuple of (imported_count, failed_candidates, failed_urls).
    """
    cleaner = LLMCleanerAgent(router=router)
    db_manager = DatabaseManager()
    
    total_imported = 0
    scout_candidates: List[CrawlPageResult] = []
    failed_urls: List[str] = []

    for page in pages:
        if not page.markdown:
            continue

        success, error = process_page_for_program(
            page=page,
            cleaner=cleaner,
            db_manager=db_manager,
            univ_slug=univ_slug,
            year=year,
            current_depth=current_depth,
        )
        
        if success:
            total_imported += 1
        else:
            failed_urls.append(page.url)
            scout_candidates.append(page)

    return total_imported, scout_candidates, failed_urls
