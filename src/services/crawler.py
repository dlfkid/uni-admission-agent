"""
Unified business logic for UniAdmission Agent.

Provides framework-agnostic functions that all entry points (CLI, API, MCP)
call. Each function accepts plain Python types and returns typed Pydantic
result models — no argparse, no HTTP, no framework coupling.

Functions:
    crawl_url      — Crawl a university page and import structured data.
    import_file    — Import data from an Excel/PDF file.
    export_data    — Export program data to Excel.
    get_db_status  — Return database statistics.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, List

from pydantic import BaseModel, Field
from sqlmodel import select, func, col, desc

from src.core.environment import ensure_ready
from src.models.admission import University, Program
from src.scrapers.engine import AdmissionScraper
from src.storage.db_manager import DatabaseManager
from src.storage.exporter import ExcelExporter
from src.storage.importer import ExcelImporter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Result models
# ---------------------------------------------------------------------------


class CrawlResult(BaseModel):
    """Result of a crawl operation."""

    imported_count: int = Field(description="Number of programs imported")
    univ_slug: str = Field(description="University slug")
    year: int = Field(description="Academic year")


class ImportResult(BaseModel):
    """Result of a file import operation."""

    source_file: str = Field(description="Path to the imported file")
    univ_slug: str = Field(description="University slug")
    year: int = Field(description="Academic year")
    success: bool = Field(default=True)


class UniversityStats(BaseModel):
    """Per-university statistics."""

    name: str
    slug: str
    year_breakdown: dict[int, int] = Field(
        default_factory=dict,
        description="Mapping of academic_year → program count",
    )


class StatusResult(BaseModel):
    """Database status summary."""

    university_count: int = 0
    program_count: int = 0
    universities: List[UniversityStats] = Field(default_factory=list)


class ProgramSummary(BaseModel):
    """Lightweight program record for API responses."""

    id: Optional[int] = None
    name_en: str = ""
    name_zh: Optional[str] = None
    academic_year: int = 0
    faculty: Optional[str] = None
    program_group_code: Optional[str] = None
    tuition_amount: Optional[float] = None
    currency: Optional[str] = None


# ---------------------------------------------------------------------------
#  Business logic
# ---------------------------------------------------------------------------


async def crawl_url(
    url: str,
    univ_slug: str,
    year: int,
    continue_depth: int = 0,
    page_type_hint: str = "auto",
    export_md: bool = False,
    export_path: Optional[str] = None,
    html_content: Optional[str] = None,
) -> CrawlResult:
    """Crawl a university admission page and import structured data.

    This is the main crawling pipeline:
        1. Fetch page with stealth browsing via crawl4ai (or use provided HTML)
        2. Detect page type (index vs detail) - can be overridden by page_type_hint
        3. Extract links / parse program data via LLM
        4. Upsert to database
        5. Optionally export markdown files to disk

    Args:
        url: Starting URL to crawl.
        univ_slug: University identifier (e.g. ``"hku"``).
        year: Academic year (e.g. ``2026``).
        continue_depth: Extra depth for LLM-driven scouting.
        page_type_hint: Page type hint ('auto', 'index', or 'detail').
        export_md: Whether to export markdown files.
        export_path: Path to export markdown files.
        html_content: Pre-rendered HTML from browser (bypasses crawling).

    Returns:
        CrawlResult with the number of programs imported.
    """
    scraper = AdmissionScraper()
    imported = await scraper.crawl_and_clean(
        url=url,
        univ_slug=univ_slug,
        year=year,
        continue_depth=continue_depth,
        page_type_hint=page_type_hint,
        export_md=export_md,
        export_path=export_path,
        html_content=html_content,
    )
    logger.info("Crawl complete: %d programs imported", imported)
    return CrawlResult(imported_count=imported, univ_slug=univ_slug, year=year)


def import_file(
    file_path: str,
    univ_slug: str,
    year: int,
    use_llm: bool = False,
) -> ImportResult:
    """Import program data from an Excel or PDF file.

    Args:
        file_path: Path to the input file (.xlsx / .xls / .pdf).
        univ_slug: University identifier.
        year: Academic year.
        use_llm: Enable LLM-assisted parsing for missing fields.

    Returns:
        ImportResult indicating success.
    """
    importer = ExcelImporter(file_path, use_llm=use_llm)
    importer.import_data(univ_slug=univ_slug, year=year)
    return ImportResult(source_file=file_path, univ_slug=univ_slug, year=year)


def export_data(
    univ_slug: str,
    output_path: str,
    year: Optional[int] = None,
) -> int:
    """Export program data to an Excel file.

    Args:
        univ_slug: University identifier.
        output_path: Destination .xlsx path.
        year: If provided, export only that year; otherwise all years.

    Returns:
        Number of programs exported.
    """
    exporter = ExcelExporter(output_path=output_path)
    return exporter.export_data(univ_slug=univ_slug, year=year)


def get_db_status() -> StatusResult:
    """Return a summary of the database contents.

    Returns:
        StatusResult with university/program counts and per-university breakdown.
    """
    db = DatabaseManager()
    result = StatusResult()

    with db.get_session() as session:
        result.university_count = session.exec(
            select(func.count()).select_from(University)
        ).one()
        result.program_count = session.exec(
            select(func.count()).select_from(Program)
        ).one()

        univs = session.exec(select(University)).all()
        for u in univs:
            year_breakdown: dict[int, int] = {}
            stmt = (
                select(Program.academic_year, func.count())
                .where(Program.university_id == u.id)
                .group_by(col(Program.academic_year))
                .order_by(desc(col(Program.academic_year)))
            )
            for year_val, count in session.exec(stmt).all():
                year_breakdown[year_val] = count

            result.universities.append(
                UniversityStats(
                    name=u.name,
                    slug=u.slug,
                    year_breakdown=year_breakdown,
                )
            )

    return result


def query_programs(
    univ_slug: str,
    year: Optional[int] = None,
) -> List[ProgramSummary]:
    """Query programs for a university, optionally filtered by year.

    Args:
        univ_slug: University identifier.
        year: Optional academic year filter.

    Returns:
        List of ProgramSummary objects.
    """
    db = DatabaseManager()
    with db.get_session() as session:
        univ = session.exec(
            select(University).where(University.slug == univ_slug)
        ).first()
        if not univ:
            return []

        stmt = select(Program).where(Program.university_id == univ.id)
        if year is not None:
            stmt = stmt.where(Program.academic_year == year)

        programs = session.exec(stmt).all()
        return [
            ProgramSummary(
                id=p.id,
                name_en=p.name_en,
                name_zh=p.name_zh,
                academic_year=p.academic_year,
                faculty=p.faculty,
                program_group_code=p.program_group_code,
                tuition_amount=float(p.tuition_amount) if p.tuition_amount else None,
                currency=p.currency.value if p.currency else None,
            )
            for p in programs
        ]


def check_environment(verbose: bool = False) -> bool:
    """Run pre-flight environment checks.

    Args:
        verbose: Enable verbose output.

    Returns:
        True if all checks pass.

    Raises:
        EnvironmentError subclasses on failure.
    """
    ensure_ready(verbose=verbose)
    return True
