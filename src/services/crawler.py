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
import importlib
import logging
import uuid
from types import SimpleNamespace
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
from src.models.scraper_models import PageType
from src.scrapers.engine import AdmissionScraper
from src.scrapers.link_parser import (
    detect_page_type,
    extract_links_with_text,
    filter_links_by_heuristic,
)
from src.services import browser_provider as browser_provider_service
from src.services.crawl_strategy.discovery import (
    DiscoveryResult,
    discover_with_default_adapters,
    resolve_crawl_range,
)
from src.services.ingestion_pipeline import IngestionPipeline
from src.services.subject_taxonomy import get_subject_taxonomy_service
from src.storage.db_manager import DatabaseManager, ProgramDeleteScope
from src.storage.exporter import ExcelExporter
from src.storage.importer import ExcelImporter
from src.agent_runtime.base import AgentRequest

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
    unresolved_urls: List[dict[str, Any]] = Field(
        default_factory=list,
        description="URLs skipped due to unresolved program names",
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


def _normalize_page_type_hint(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "auto"
    normalized = raw.lower()
    compact = normalized.replace(" ", "").replace("-", "").replace("_", "")
    alias_map = {
        "auto": "auto",
        "自动": "auto",
        "自动识别": "auto",
        "默认": "auto",
        "index": "index",
        "listing": "index",
        "list": "index",
        "索引": "index",
        "目录": "index",
        "列表": "index",
        "索引页": "index",
        "目录页": "index",
        "列表页": "index",
        "detail": "detail",
        "details": "detail",
        "详情": "detail",
        "细节": "detail",
        "详细": "detail",
        "详情页": "detail",
        "细节页": "detail",
        "详细页": "detail",
    }
    if compact in alias_map:
        return alias_map[compact]
    if normalized in alias_map:
        return alias_map[normalized]
    return "auto"


def _html_to_markdown_for_analyze(url: str, html_content: str) -> str:
    raw_markdown = str(html_content or "")
    if not raw_markdown:
        return ""

    try:
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        markdown_obj = DefaultMarkdownGenerator().generate_markdown(
            input_html=raw_markdown,
            base_url=url,
        )
        if markdown_obj and hasattr(markdown_obj, "raw_markdown"):
            generated = str(markdown_obj.raw_markdown or "").strip()
            if generated:
                return generated
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Analyze markdown conversion failed; using raw html text fallback: %s", exc)
    return raw_markdown


def analyze_page_external(
    url: str,
    html_content: str,
    page_type_hint: str = "auto",
) -> dict:
    """Analyze page by deterministic heuristics (no internal LLM calls)."""
    normalized_hint = _normalize_page_type_hint(page_type_hint)
    markdown = _html_to_markdown_for_analyze(url, html_content)
    link_pairs = extract_links_with_text(markdown, url)
    total_found = len(link_pairs)

    if normalized_hint == "index":
        page_type = "index"
    elif normalized_hint == "detail":
        page_type = "detail"
    else:
        detected = detect_page_type(markdown, total_found, page_url=url)
        page_type = "index" if detected == PageType.INDEX else "detail"

    if page_type == "detail":
        return {"page_type": "detail", "links": [], "total_found": 0}

    selected_urls = filter_links_by_heuristic(link_pairs, url)
    url_to_text = dict(link_pairs)
    links = [
        {"url": selected_url, "text": url_to_text.get(selected_url, "")}
        for selected_url in selected_urls
    ]
    return {"page_type": "index", "links": links, "total_found": total_found}


async def analyze_url_candidates(
    *,
    url: str,
    page_type_hint: str = "index",
    html_content: Optional[str] = None,
    browser_provider: str = "auto",
    client_id: Optional[str] = None,
    strict_client: bool = False,
) -> dict[str, Any]:
    """Analyze one URL and return detail-link candidates for interactive selection.

    If ``html_content`` is missing, this function resolves browser inputs via the
    configured browser provider (server/client/auto), then runs the normal
    ``analyze_page`` logic.

    Always uses the server's configured LLM (``analyze_page``, backed by
    ``RouterAgent``) — this is the MCP entrypoint's analysis, and MCP tool
    calls always get the server's real classification, not a cheaper
    heuristic stand-in. (``analyze_page_external`` still exists and is used
    elsewhere — the agent runtime's own internal ``analyze_page`` skill
    deliberately uses it to avoid a redundant nested LLM call inside a loop
    that is already LLM-driven — but that is a different caller with a
    different reason, not a mode MCP callers should be able to pick.)
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
    name_resolution_llm_enabled: Optional[bool] = None,
    name_resolution_low_threshold: Optional[float] = None,
    name_resolution_conflict_delta: Optional[float] = None,
    progress_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
    limit: Optional[int] = None,
    crawl_all: bool = False,
    discovery: Optional[DiscoveryResult] = None,
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
        page_type_hint: Page type ('index' or 'detail'). Must be concrete —
            the caller decides; there is no automatic detection.
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
        name_resolution_llm_enabled: Optional per-request LLM fallback toggle.
        name_resolution_low_threshold: Optional name-resolution low-confidence threshold.
        name_resolution_conflict_delta: Optional top-candidate conflict delta threshold.
        limit: Crawl only the first N programmes discovered on an index page.
        crawl_all: Crawl every programme discovered (safety-capped upstream).
        discovery: Precomputed DiscoveryResult (e.g. from the /agent/run
            short-circuit) — used as-is, never recomputed.

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
        limit=limit,
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

    # Strategy-first discovery: known/classifiable index pages get accurate
    # {detail_url: name} candidates from the crawl-strategy system; anything
    # else falls through to today's LLM-scout path untouched.
    if (
        discovery is None
        and page_type_hint == "index"
        and not selected_urls
        and not detail_pages_batch
        and html_content is None
    ):
        crawl_range = resolve_crawl_range(limit, crawl_all)
        discovery = await asyncio.to_thread(
            discover_with_default_adapters, url, crawl_range)
    discovery_sibling_urls = None
    if discovery is not None and discovery.matched:
        selected_urls = list(discovery.link_texts)
        selected_link_texts = dict(discovery.link_texts)
        discovery_sibling_urls = discovery.sibling_urls
        logger.info(
            "strategy discovery matched url=%s strategy=%s names=%d nameless=%d "
            "stopped=%s", url, discovery.strategy_used, len(selected_urls),
            discovery.nameless_count, discovery.stopped_reason)
        if progress_callback:
            progress_callback("discovery_matched", {
                "strategy_used": discovery.strategy_used,
                "names_count": len(selected_urls),
                "nameless_count": discovery.nameless_count,
                "stopped_reason": discovery.stopped_reason,
                "pages_fetched": discovery.pages_fetched,
            })

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
        max_detail_pages=(None if crawl_all else limit),
        browser_automation_enabled=browser_automation_enabled,
        detail_pages_batch=detail_pages_batch,
        batch_index=batch_index,
        batch_total=batch_total,
        supplement_url_re=(
            discovery.supplement_url_re if discovery is not None else None
        ),
        selected_sibling_urls=discovery_sibling_urls,
        candidate_taxonomy_filter_enabled=candidate_taxonomy_filter_enabled,
        candidate_taxonomy_filter_threshold=candidate_taxonomy_filter_threshold,
        candidate_taxonomy_filter_top_k=candidate_taxonomy_filter_top_k,
        taxonomy_enabled=taxonomy_enabled,
        taxonomy_low_threshold=taxonomy_low_threshold,
        taxonomy_high_threshold=taxonomy_high_threshold,
        taxonomy_hint_top_k=taxonomy_hint_top_k,
        taxonomy_override_enabled=taxonomy_override_enabled,
        name_resolution_llm_enabled=name_resolution_llm_enabled,
        name_resolution_low_threshold=name_resolution_low_threshold,
        name_resolution_conflict_delta=name_resolution_conflict_delta,
        event_callback=progress_callback,
    )
    imported = int(result.get("imported_count") or 0)
    persisted_program_ids = [
        int(item)
        for item in (result.get("persisted_program_ids") or [])
        if str(item).strip()
    ]
    try:
        review_items = _build_review_items(
            univ_slug=univ_slug,
            year=year,
            persisted_program_ids=persisted_program_ids,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "Failed building review items for crawl result (univ=%s year=%s): %s",
            univ_slug,
            year,
            exc,
        )
        review_items = []
    review_token = str(result.get("job_uid") or "").strip() or uuid.uuid4().hex
    logger.info(
        "Crawl complete (phase2 pipeline): %d programs imported, job=%s",
        imported,
        result.get("job_uid"),
    )
    unresolved_urls = list(result.get("unresolved_urls") or [])
    if unresolved_urls:
        logger.warning("Crawl completed with unresolved program names: %d", len(unresolved_urls))
    return CrawlResult(
        imported_count=imported,
        univ_slug=univ_slug,
        year=year,
        ingestion_job_id=result.get("job_uid"),
        resolved_browser_provider=resolved_browser_provider,
        client_id_used=client_id_used,
        review_token=review_token,
        review_items=review_items,
        unresolved_urls=unresolved_urls,
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


async def run_agent_crawl(
    *,
    url: str,
    univ_slug: str,
    year: int,
    page_type_hint: str = "auto",
    runtime_mode: Optional[str] = None,
    policy_profile: Optional[dict[str, Any]] = None,
    client_id: Optional[str] = None,
    autonomous: bool = False,
    dry_run: bool = False,
    event_sink: Any = None,
    auto_paginate: bool = False,
    max_pages: Optional[int] = None,
) -> dict[str, Any]:
    """Run crawl orchestration via configured agent runtime."""
    # Lazy import: runtime_factory → pydanticai_runtime → crawler forms a
    # circular dependency chain.  Breaking it here (the less-used direction)
    # keeps the hot path (direct crawler imports) free of lazy-import noise.
    runtime_factory = importlib.import_module("src.agent_runtime.runtime_factory")
    build_agent_runtime = getattr(runtime_factory, "build_agent_runtime")

    runtime_config = None
    if runtime_mode:
        runtime_config = SimpleNamespace(runtime=runtime_mode)

    runtime = build_agent_runtime(config=runtime_config, bridge=None, model_adapter=None)
    request_payload: dict[str, Any] = {
        "url": str(url or "").strip(),
        "univ_slug": str(univ_slug or "").strip().lower(),
        "year": int(year),
        "page_type_hint": str(page_type_hint or "auto").strip().lower() or "auto",
    }
    if policy_profile:
        request_payload["policy_profile"] = dict(policy_profile)
    if auto_paginate:
        request_payload["auto_paginate"] = True
    if max_pages is not None:
        request_payload["max_pages"] = int(max_pages)

    response = await runtime.run(
        AgentRequest(
            task="crawl",
            payload=request_payload,
            context={
                "entrypoint": "api",
                "client_id": str(client_id).strip() if client_id else None,
                "autonomous": bool(autonomous),
                "dry_run": bool(dry_run),
                "event_sink": event_sink,
            },
        )
    )
    return response.model_dump(mode="json")


async def run_agent_chat(
    *,
    message: str,
    context: Optional[dict[str, Any]] = None,
    event_sink: Any = None,
) -> dict[str, Any]:
    """Run a free-form chat request via the internal agent runtime.

    Uses the server-side LLM exclusively (``allow_external=False``).
    The agent loop receives the user message directly and responds using its
    tool-calling capabilities.
    """
    runtime_factory = importlib.import_module("src.agent_runtime.runtime_factory")
    build_agent_runtime = getattr(runtime_factory, "build_agent_runtime")

    from src.agent_runtime.model_provider import ModelProviderAdapter

    model_adapter = ModelProviderAdapter(
        allow_internal=True,
        allow_external=False,
    )

    runtime = build_agent_runtime(config=None, bridge=None, model_adapter=model_adapter)

    extra_context = dict(context or {})
    extra_context.update(
        {
            "entrypoint": "chat",
            "event_sink": event_sink,
        }
    )

    response = await runtime.run(
        AgentRequest(
            task="chat",
            payload={"message": str(message or "").strip()},
            context=extra_context,
        )
    )
    return response.model_dump(mode="json")


def ingest_program_records_external(
    *,
    univ_slug: str,
    year: int,
    programs: list[Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Persist caller-provided structured program records without internal LLM calls."""
    normalized_univ_slug = str(univ_slug or "").strip().lower()
    if not normalized_univ_slug:
        raise ValueError("univ_slug is required")

    try:
        normalized_year = int(year)
    except (TypeError, ValueError) as exc:
        raise ValueError("year must be a positive integer") from exc
    if normalized_year <= 0:
        raise ValueError("year must be a positive integer")

    submitted_items = list(programs or [])
    if not submitted_items:
        return {
            "imported_count": 0,
            "updated_count": 0,
            "total_submitted": 0,
            "failed_items": [],
            "review_token": uuid.uuid4().hex,
            "review_items": [],
            "univ_slug": normalized_univ_slug,
            "year": normalized_year,
            "summary": "No program records supplied.",
            "dry_run": dry_run,
            "parsed_programs": [],
        }

    db = None if dry_run else DatabaseManager()
    imported_count = 0
    updated_count = 0
    failed_items: list[dict[str, Any]] = []
    persisted_program_ids: list[int] = []
    parsed_programs: list[dict[str, Any]] = []

    for idx, raw_item in enumerate(submitted_items):
        if not isinstance(raw_item, dict):
            failed_items.append(
                {
                    "index": idx,
                    "error_code": "invalid_item_type",
                    "message": "Each program item must be an object.",
                }
            )
            continue

        payload = dict(raw_item)
        payload["academic_year"] = normalized_year
        if not str(payload.get("name_en") or "").strip():
            failed_items.append(
                {
                    "index": idx,
                    "error_code": "missing_name_en",
                    "message": "name_en is required for external ingest.",
                }
            )
            continue

        if dry_run:
            parsed_programs.append(payload)
            continue

        try:
            program, created = db.upsert_program(
                payload,
                normalized_univ_slug,
                enable_auto_translation=False,
            )
        except Exception as exc:  # pylint: disable=broad-except
            failed_items.append(
                {
                    "index": idx,
                    "error_code": "upsert_failed",
                    "message": str(exc),
                }
            )
            continue

        if getattr(program, "id", None) is not None:
            persisted_program_ids.append(int(program.id))
        if created:
            imported_count += 1
        else:
            updated_count += 1

    if dry_run:
        review_items = []
    else:
        try:
            review_items = _build_review_items(
                univ_slug=normalized_univ_slug,
                year=normalized_year,
                persisted_program_ids=persisted_program_ids,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Failed building review items for external ingest (univ=%s year=%s): %s",
                normalized_univ_slug,
                normalized_year,
                exc,
            )
            review_items = []

    upserted_count = imported_count + updated_count
    summary = (
        f"External ingest upserted {upserted_count}/{len(submitted_items)} items; "
        f"created={imported_count}, updated={updated_count}, failed={len(failed_items)}."
    )
    return {
        "imported_count": imported_count,
        "updated_count": updated_count,
        "total_submitted": len(submitted_items),
        "failed_items": failed_items,
        "review_token": uuid.uuid4().hex,
        "review_items": review_items,
        "persisted_program_ids": persisted_program_ids,
        "univ_slug": normalized_univ_slug,
        "year": normalized_year,
        "summary": summary,
        "dry_run": dry_run,
        "parsed_programs": parsed_programs,
    }


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
                raw_so = program.study_options or []
                study_options = raw_so if isinstance(raw_so, list) else []

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
                raw_dl = program.deadlines or []
                deadlines = raw_dl if isinstance(raw_dl, list) else []

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
        return []

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
    db = DatabaseManager()
    program_name = ""
    with db.get_session() as session:
        existing = session.get(Program, program_id)
        if existing and existing.name_en:
            program_name = str(existing.name_en).strip()

    deleted = db.delete_program_snapshot(program_id)
    if deleted and program_name:
        try:
            get_subject_taxonomy_service().prune_orphaned_learned_names([program_name])
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Failed pruning taxonomy after deleting program_id=%s name=%s: %s",
                program_id,
                program_name,
                exc,
            )

    return deleted


def count_programs_by_scope(
    university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Read-only preview of programs matching a university/year scope."""
    db = DatabaseManager()
    return db.count_programs_by_scope(university_slug, year)


def delete_programs_by_scope(
    university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Delete all programs matching a university/year scope.

    Prunes orphaned learned taxonomy names for the whole deleted batch in
    one call after the delete commits, mirroring delete_program_snapshot's
    per-row taxonomy prune. A prune failure is logged and swallowed — the
    delete has already committed and must be reported regardless.
    """
    db = DatabaseManager()
    result = db.delete_programs_by_scope(university_slug, year)
    if result.count > 0 and result.deleted_names:
        try:
            get_subject_taxonomy_service().prune_orphaned_learned_names(
                result.deleted_names
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Failed pruning taxonomy after batch-deleting university_slug=%s year=%s: %s",
                university_slug,
                year,
                exc,
            )
    return result


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
