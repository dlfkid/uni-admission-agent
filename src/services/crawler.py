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
import uuid
from typing import Any, Callable, Optional, List

from pydantic import BaseModel, Field
from sqlmodel import select, func, col, desc

from src.core.environment import ensure_ready
from src.models.admission import University, Program, ProgramCatalog
from src.models.requirement import (
    ProgramStudyOption,
    ProgramDeadline,
    ProgramRequirement,
    SubjectDim,
    ExamDim,
    FrameworkDim,
    RequirementEvidence,
    RequirementVersion,
)
from src.models.ingestion import IngestionStage
from src.scrapers.engine import AdmissionScraper
from src.services import browser_provider as browser_provider_service
from src.services.ingestion_pipeline import IngestionPipeline
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
    ingestion_job_id: Optional[str] = Field(
        default=None,
        description="Phase 2 ingestion job identifier",
    )
    resolved_browser_provider: str = Field(
        default="server",
        description="Resolved browser provider for this request",
    )
    client_id_used: Optional[str] = Field(
        default=None,
        description="Selected client id when browser provider resolves to client",
    )
    review_token: Optional[str] = Field(
        default=None,
        description="Token for follow-up review and correction operations",
    )
    review_items: List[dict[str, Any]] = Field(
        default_factory=list,
        description="Ordered persisted program records with stable program_id",
    )


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
    study_options: list = Field(default_factory=list)
    deadlines: list = Field(default_factory=list)
    requirements: list = Field(default_factory=list)
    requirement_version: Optional[dict] = None
    source_url: Optional[str] = None


# ---------------------------------------------------------------------------
#  Business logic
# ---------------------------------------------------------------------------


def analyze_page(
    url: str,
    html_content: str,
    page_type_hint: str = "auto",
) -> dict:
    """Analyze a page's HTML to determine type and extract candidate links.

    Synchronous wrapper around :pymethod:`AdmissionScraper.analyze_page_links`.

    Returns:
        dict with ``page_type``, ``links`` and ``total_found``.
    """
    scraper = AdmissionScraper()
    return scraper.analyze_page_links(url, html_content, page_type_hint)


async def analyze_url_candidates(
    *,
    url: str,
    page_type_hint: str = "auto",
    html_content: Optional[str] = None,
    browser_provider: str = "auto",
    client_id: Optional[str] = None,
    strict_client: bool = False,
) -> dict[str, Any]:
    """Analyze one URL and return detail-link candidates for interactive selection.

    If ``html_content`` is missing, this function resolves browser inputs via the
    configured browser provider (server/client/auto), then runs the normal
    ``analyze_page`` logic.
    """
    source = "provided" if html_content else "unknown"
    resolved_browser_provider = "server"
    client_id_used: Optional[str] = None
    if not html_content:
        resolved_browser_inputs = await browser_provider_service.resolve_browser_inputs(
            url=url,
            page_type_hint=page_type_hint,
            html_content=None,
            detail_pages_batch=None,
            browser_provider=browser_provider,
            client_id=client_id,
            strict_client=strict_client,
        )
        resolved_provider_value = resolved_browser_inputs.get("resolved_browser_provider")
        if resolved_provider_value:
            resolved_browser_provider = str(resolved_provider_value)
        elif resolved_browser_inputs.get("html_content"):
            resolved_browser_provider = "client"
        else:
            resolved_browser_provider = "server"
        resolved_client_id = resolved_browser_inputs.get("client_id_used")
        client_id_used = str(resolved_client_id).strip() if resolved_client_id else None
        resolved_html = resolved_browser_inputs.get("html_content")
        html_content = str(resolved_html or "").strip() or None
        if html_content:
            source = "client" if resolved_browser_provider == "client" else "server"

    if not html_content:
        raise RuntimeError(
            "No HTML content available for analyze. "
            "Provide html_content or use browser_provider=client with an online client."
        )

    result = await asyncio.to_thread(analyze_page, url, html_content, page_type_hint)
    payload = dict(result or {})
    payload["html_source"] = source
    payload["resolved_browser_provider"] = resolved_browser_provider
    payload["client_id_used"] = client_id_used
    return payload


async def crawl_selected_detail_urls_via_client(
    *,
    index_url: str,
    selected_urls: list[str],
    univ_slug: str,
    year: int,
    batch_size: int = 4,
    client_id: Optional[str] = None,
    strict_client: bool = True,
    selected_link_texts: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Fetch selected detail URLs via client browser automation, then crawl in batches."""
    deduped_urls: list[str] = []
    seen: set[str] = set()
    for raw_url in selected_urls:
        detail_url = str(raw_url or "").strip()
        if not detail_url or detail_url in seen:
            continue
        seen.add(detail_url)
        deduped_urls.append(detail_url)

    if not deduped_urls:
        return {
            "imported_count": 0,
            "total_selected": 0,
            "batch_total": 0,
            "failed_urls": [],
            "job_ids": [],
        }

    bounded_batch_size = max(1, int(batch_size))
    total_selected = len(deduped_urls)
    batch_total = (total_selected + bounded_batch_size - 1) // bounded_batch_size
    link_text_map = {
        str(key).strip(): str(value).strip()
        for key, value in dict(selected_link_texts or {}).items()
        if str(key).strip() and str(value).strip()
    }

    total_imported = 0
    failed_urls: list[str] = []
    job_ids: list[str] = []
    batch_results: list[dict[str, Any]] = []

    for batch_index in range(batch_total):
        start = batch_index * bounded_batch_size
        end = min(start + bounded_batch_size, total_selected)
        batch_urls = deduped_urls[start:end]

        detail_pages_batch: list[dict[str, Any]] = []
        for detail_url in batch_urls:
            try:
                browser_payload = await browser_provider_service.fetch_index_and_details_via_client(
                    url=detail_url,
                    page_type_hint="detail",
                    client_id=client_id,
                )
            except Exception:
                if strict_client:
                    raise
                failed_urls.append(detail_url)
                continue

            html_content = str(browser_payload.get("html_content") or "").strip()
            if not html_content:
                if strict_client:
                    raise RuntimeError(f"Client returned empty html_content for detail url: {detail_url}")
                failed_urls.append(detail_url)
                continue

            row: dict[str, Any] = {
                "url": detail_url,
                "html_content": html_content,
            }
            anchor_text = link_text_map.get(detail_url)
            if anchor_text:
                row["selected_anchor_text"] = anchor_text
            detail_pages_batch.append(row)

        if not detail_pages_batch:
            batch_results.append(
                {
                    "batch_index": batch_index + 1,
                    "imported_count": 0,
                    "submitted": 0,
                }
            )
            continue

        batch_text_map = {
            item["url"]: link_text_map[item["url"]]
            for item in detail_pages_batch
            if item["url"] in link_text_map
        }
        crawl_result = await crawl_url(
            url=index_url,
            univ_slug=univ_slug,
            year=year,
            page_type_hint="detail",
            browser_provider="server",
            detail_pages_batch=detail_pages_batch,
            batch_index=batch_index + 1,
            batch_total=batch_total,
            selected_link_texts=batch_text_map,
        )
        total_imported += int(crawl_result.imported_count or 0)
        if crawl_result.ingestion_job_id:
            job_ids.append(str(crawl_result.ingestion_job_id))
        batch_results.append(
            {
                "batch_index": batch_index + 1,
                "imported_count": int(crawl_result.imported_count or 0),
                "submitted": len(detail_pages_batch),
            }
        )

    return {
        "imported_count": total_imported,
        "total_selected": total_selected,
        "batch_total": batch_total,
        "failed_urls": failed_urls,
        "job_ids": job_ids,
        "batches": batch_results,
    }


async def crawl_url(
    url: str,
    univ_slug: str,
    year: int,
    continue_depth: int = 0,
    page_type_hint: str = "auto",
    export_md: bool = False,
    export_path: Optional[str] = None,
    html_content: Optional[str] = None,
    selected_urls: Optional[list[str]] = None,
    selected_link_texts: Optional[dict[str, str]] = None,
    browser_automation_enabled: bool = False,
    detail_pages_batch: Optional[List[dict[str, Any]]] = None,
    batch_index: Optional[int] = None,
    batch_total: Optional[int] = None,
    browser_provider: str = "auto",
    client_id: Optional[str] = None,
    strict_client: bool = False,
    candidate_taxonomy_filter_enabled: bool = False,
    candidate_taxonomy_filter_threshold: float = 0.75,
    candidate_taxonomy_filter_top_k: int = 30,
    taxonomy_enabled: Optional[bool] = None,
    taxonomy_low_threshold: Optional[float] = None,
    taxonomy_high_threshold: Optional[float] = None,
    taxonomy_hint_top_k: Optional[int] = None,
    taxonomy_override_enabled: Optional[bool] = None,
    progress_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
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
        selected_urls: User-selected detail URLs (skips index analysis).
        selected_link_texts: Optional mapping of selected URL → anchor text.
        browser_automation_enabled: Whether browser-tab automation is enabled for index flow.
        detail_pages_batch: Browser-collected detail HTML batch payload.
        batch_index: 1-based batch index for current submission.
        batch_total: Total number of batches for current submission.
        browser_provider: Browser HTML provider strategy: auto/server/client.
        client_id: Optional target connected client id.
        strict_client: Whether to fail instead of fallback when client flow is unavailable.
        candidate_taxonomy_filter_enabled: Enable taxonomy filter on index candidate links.
        candidate_taxonomy_filter_threshold: Minimum taxonomy score for candidate keep.
        candidate_taxonomy_filter_top_k: Max candidate links retained after taxonomy filter.
        taxonomy_enabled: Optional per-request taxonomy toggle.
        taxonomy_low_threshold: Optional hint injection score threshold.
        taxonomy_high_threshold: Optional override score threshold.
        taxonomy_hint_top_k: Optional cap for injected taxonomy hints.
        taxonomy_override_enabled: Optional per-request name override toggle.

    Returns:
        CrawlResult with the number of programs imported.
    """
    resolved_browser_inputs = await browser_provider_service.resolve_browser_inputs(
        url=url,
        page_type_hint=page_type_hint,
        html_content=html_content,
        detail_pages_batch=detail_pages_batch,
        browser_provider=browser_provider,
        client_id=client_id,
        strict_client=strict_client,
    )
    resolved_browser_provider = str(
        resolved_browser_inputs.get("resolved_browser_provider") or "server"
    )
    resolved_client_id = resolved_browser_inputs.get("client_id_used")
    client_id_used = str(resolved_client_id).strip() if resolved_client_id else None
    if "html_content" in resolved_browser_inputs:
        html_content = resolved_browser_inputs.get("html_content")
    if "detail_pages_batch" in resolved_browser_inputs:
        detail_pages_batch = resolved_browser_inputs.get("detail_pages_batch")
    if "selected_urls" in resolved_browser_inputs:
        selected_urls = resolved_browser_inputs.get("selected_urls")
    if "selected_link_texts" in resolved_browser_inputs:
        selected_link_texts = resolved_browser_inputs.get("selected_link_texts")

    pipeline = IngestionPipeline()
    result = await pipeline.run_new_job(
        url=url,
        univ_slug=univ_slug,
        year=year,
        continue_depth=continue_depth,
        page_type_hint=page_type_hint,
        export_md=export_md,
        export_path=export_path,
        html_content=html_content,
        selected_urls=selected_urls,
        selected_link_texts=selected_link_texts,
        browser_automation_enabled=browser_automation_enabled,
        detail_pages_batch=detail_pages_batch,
        batch_index=batch_index,
        batch_total=batch_total,
        candidate_taxonomy_filter_enabled=candidate_taxonomy_filter_enabled,
        candidate_taxonomy_filter_threshold=candidate_taxonomy_filter_threshold,
        candidate_taxonomy_filter_top_k=candidate_taxonomy_filter_top_k,
        taxonomy_enabled=taxonomy_enabled,
        taxonomy_low_threshold=taxonomy_low_threshold,
        taxonomy_high_threshold=taxonomy_high_threshold,
        taxonomy_hint_top_k=taxonomy_hint_top_k,
        taxonomy_override_enabled=taxonomy_override_enabled,
        event_callback=progress_callback,
    )
    imported = int(result.get("imported_count") or 0)
    persisted_program_ids = [
        int(item)
        for item in (result.get("persisted_program_ids") or [])
        if str(item).strip()
    ]
    review_items = _build_review_items(
        univ_slug=univ_slug,
        year=year,
        persisted_program_ids=persisted_program_ids,
    )
    review_token = str(result.get("job_uid") or "").strip() or uuid.uuid4().hex
    logger.info(
        "Crawl complete (phase2 pipeline): %d programs imported, job=%s",
        imported,
        result.get("job_uid"),
    )
    return CrawlResult(
        imported_count=imported,
        univ_slug=univ_slug,
        year=year,
        ingestion_job_id=result.get("job_uid"),
        resolved_browser_provider=resolved_browser_provider,
        client_id_used=client_id_used,
        review_token=review_token,
        review_items=review_items,
    )


def list_ingestion_jobs(limit: int = 20) -> List[dict]:
    """List recent ingestion jobs for replay/resume workflows."""
    return IngestionPipeline().list_jobs(limit=limit)


def get_ingestion_job(job_uid: str) -> Optional[dict]:
    """Get one ingestion job and its stage/task state."""
    return IngestionPipeline().get_job(job_uid)


async def resume_crawl_job(
    job_uid: str,
    resume_from_stage: Optional[str] = None,
    progress_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> CrawlResult:
    """Resume a previously failed/poisoned ingestion job."""
    parsed_stage: Optional[IngestionStage] = None
    if resume_from_stage:
        parsed_stage = IngestionStage(resume_from_stage)

    result = await IngestionPipeline().resume_job(
        job_uid=job_uid,
        resume_from_stage=parsed_stage,
        event_callback=progress_callback,
    )
    return CrawlResult(
        imported_count=int(result.get("imported_count") or 0),
        univ_slug=str(result.get("univ_slug") or ""),
        year=int(result.get("year") or 0),
        ingestion_job_id=str(result.get("job_uid") or job_uid),
    )


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

        stmt = (
            select(Program, ProgramCatalog)
            .join(ProgramCatalog, ProgramCatalog.id == Program.program_catalog_id, isouter=True)
            .where(Program.university_id == univ.id)
            .order_by(desc(col(Program.academic_year)), col(Program.name_en))
        )
        if year is not None:
            stmt = stmt.where(Program.academic_year == year)

        rows = session.exec(stmt).all()
        out: List[ProgramSummary] = []

        for program, catalog in rows:
            option_rows = session.exec(
                select(ProgramStudyOption)
                .where(ProgramStudyOption.program_id == program.id)
                .order_by(col(ProgramStudyOption.id))
            ).all()
            deadline_rows = session.exec(
                select(ProgramDeadline)
                .where(ProgramDeadline.program_id == program.id)
                .order_by(col(ProgramDeadline.cutoff_date), col(ProgramDeadline.id))
            ).all()
            latest_requirement_version = session.exec(
                select(RequirementVersion)
                .where(RequirementVersion.program_id == program.id)
                .order_by(desc(col(RequirementVersion.version_no)))
            ).first()

            requirement_stmt = (
                select(
                    ProgramRequirement,
                    SubjectDim,
                    ExamDim,
                    FrameworkDim,
                    RequirementEvidence,
                )
                .join(SubjectDim, SubjectDim.id == ProgramRequirement.subject_dim_id, isouter=True)
                .join(ExamDim, ExamDim.id == ProgramRequirement.exam_dim_id, isouter=True)
                .join(FrameworkDim, FrameworkDim.id == ProgramRequirement.framework_dim_id, isouter=True)
                .join(RequirementEvidence, RequirementEvidence.id == ProgramRequirement.evidence_id, isouter=True)
                .order_by(col(ProgramRequirement.sort_order), col(ProgramRequirement.id))
            )
            if latest_requirement_version and latest_requirement_version.id is not None:
                requirement_stmt = requirement_stmt.where(
                    ProgramRequirement.version_id == latest_requirement_version.id
                )
            else:
                requirement_stmt = requirement_stmt.where(
                    ProgramRequirement.program_id == program.id
                )
            requirement_rows = session.exec(requirement_stmt).all()

            if option_rows:
                study_options = [
                    {
                        "mode": opt.mode.value if opt.mode else "Unknown",
                        "duration_months": opt.duration_months,
                        "notes": opt.notes,
                    }
                    for opt in option_rows
                ]
            else:
                study_options = program.study_options or []

            if deadline_rows:
                deadlines = [
                    {
                        "round": d.round,
                        "description": d.description,
                        "cutoff_date": d.cutoff_date.isoformat() if d.cutoff_date else None,
                    }
                    for d in deadline_rows
                ]
            else:
                deadlines = program.deadlines or []

            requirements = []
            for req, subject_dim, exam_dim, framework_dim, evidence in requirement_rows:
                requirements.append(
                    {
                        "category": req.category.value if req.category else "other",
                        "subject_name": (
                            subject_dim.canonical_name
                            if subject_dim and subject_dim.canonical_name
                            else req.subject_name
                        ),
                        "framework": (
                            framework_dim.display_name
                            if framework_dim and framework_dim.display_name
                            else req.framework
                        ),
                        "exam_name": exam_dim.display_name if exam_dim else None,
                        "minimum_value": req.minimum_value,
                        "unit": req.unit,
                        "applicant_scope": req.applicant_scope,
                        "requirement_text": req.requirement_text,
                        "evidence_url": (
                            evidence.source_url
                            if evidence and evidence.source_url
                            else req.evidence_url
                        ),
                        "evidence_snippet": evidence.page_snippet if evidence else None,
                        "evidence_locator_type": evidence.locator_type if evidence else None,
                        "evidence_locator_value": evidence.locator_value if evidence else None,
                        "evidence_captured_at": (
                            evidence.captured_at.isoformat()
                            if evidence and evidence.captured_at
                            else None
                        ),
                        "sort_order": req.sort_order,
                    }
                )

            requirement_version = None
            if latest_requirement_version:
                requirement_version = {
                    "version_no": latest_requirement_version.version_no,
                    "effective_at": latest_requirement_version.effective_at.isoformat()
                    if latest_requirement_version.effective_at
                    else None,
                    "valid_from": latest_requirement_version.valid_from.isoformat()
                    if latest_requirement_version.valid_from
                    else None,
                    "valid_to": latest_requirement_version.valid_to.isoformat()
                    if latest_requirement_version.valid_to
                    else None,
                    "change_summary": latest_requirement_version.change_summary,
                    "diff_payload": latest_requirement_version.diff_payload or {},
                }

            source_url = program.source_url or (program.extra_metadata or {}).get("source_url")
            group_code = (
                (catalog.program_group_code if catalog else None)
                or program.program_group_code
            )

            out.append(
                ProgramSummary(
                    id=program.id,
                    name_en=program.name_en,
                    name_zh=program.name_zh,
                    academic_year=program.academic_year,
                    faculty=program.faculty,
                    program_group_code=group_code,
                    tuition_amount=float(program.tuition_amount) if program.tuition_amount else None,
                    currency=program.currency.value if program.currency else None,
                    study_options=study_options,
                    deadlines=deadlines,
                    requirements=requirements,
                    requirement_version=requirement_version,
                    source_url=source_url,
                )
            )

        return out


def _build_review_items(
    *,
    univ_slug: str,
    year: int,
    persisted_program_ids: Optional[List[int]] = None,
) -> List[dict[str, Any]]:
    summaries = query_programs(univ_slug=univ_slug, year=year)
    if not summaries:
        return []

    summary_by_id = {
        int(item.id): item
        for item in summaries
        if item.id is not None
    }
    ordered_ids = [int(item) for item in (persisted_program_ids or []) if item is not None]

    ordered_summaries: List[ProgramSummary] = []
    seen: set[int] = set()
    for program_id in ordered_ids:
        summary = summary_by_id.get(program_id)
        if summary is None or program_id in seen:
            continue
        ordered_summaries.append(summary)
        seen.add(program_id)

    if not ordered_summaries:
        ordered_summaries = summaries

    review_items: List[dict[str, Any]] = []
    for index, summary in enumerate(ordered_summaries, start=1):
        if summary.id is None:
            continue
        review_items.append(
            {
                "index": index,
                "program_id": int(summary.id),
                "name_en": summary.name_en,
                "source_url": summary.source_url,
                "faculty": summary.faculty,
                "tuition_amount": summary.tuition_amount,
                "currency": summary.currency,
            }
        )
    return review_items


def _program_to_summary(program: Program) -> ProgramSummary:
    return ProgramSummary(
        id=program.id,
        name_en=program.name_en,
        name_zh=program.name_zh,
        academic_year=program.academic_year,
        faculty=program.faculty,
        program_group_code=program.program_group_code,
        tuition_amount=float(program.tuition_amount) if program.tuition_amount else None,
        currency=program.currency.value if program.currency else None,
        study_options=program.study_options or [],
        deadlines=program.deadlines or [],
        requirements=[],
        requirement_version=None,
        source_url=program.source_url or (program.extra_metadata or {}).get("source_url"),
    )


def delete_program_snapshot(program_id: int) -> bool:
    """Delete one year-specific program snapshot by ID."""
    return DatabaseManager().delete_program_snapshot(program_id)


def patch_program_snapshot(
    program_id: int,
    patch_payload: dict[str, Any],
) -> Optional[ProgramSummary]:
    """Patch one year-specific program snapshot and return refreshed summary."""
    db = DatabaseManager()
    patched_program = db.patch_program_snapshot(program_id, patch_payload)
    if not patched_program:
        return None

    if patched_program.id is None or patched_program.university_id is None:
        return _program_to_summary(patched_program)

    with db.get_session() as session:
        university = session.get(University, patched_program.university_id)

    if not university:
        return _program_to_summary(patched_program)

    summaries = query_programs(
        univ_slug=university.slug,
        year=patched_program.academic_year,
    )
    for summary in summaries:
        if summary.id == patched_program.id:
            return summary
    return _program_to_summary(patched_program)


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
