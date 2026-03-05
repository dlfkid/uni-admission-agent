"""
FastAPI + MCP server for UniAdmission Agent.

Exposes:
    REST endpoints — ``/crawl``, ``/tasks/{id}``, ``/status``, ``/programs``, ``/config``, ``/cancel``
    MCP tools     — ``crawl``, ``db_query``

Start via CLI:
    uv run src/cmd/cli.py serve          # defaults to 0.0.0.0:8910
    uv run src/cmd/cli.py serve --port 9000

Or directly:
    uvicorn src.api.server:app --port 8910
"""

import asyncio
import logging
import shutil
import threading
from pathlib import Path
from typing import List, Optional, Dict
from io import StringIO

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from dotenv import find_dotenv, dotenv_values

from src.api.schemas import (
    CrawlRequest,
    CrawlResponse,
    ProgramResponse,
    StatusResponse,
    TaskStatusResponse,
    ConfigResponse,
    ConfigRequest,
    CancelResponse,
    StructuredConfig,
    UniversityResponse,
    ExportRequest,
    AnalyzeRequest,
    AnalyzeResponse,
    LinkCandidate,
    TestConnectionRequest,
    TestConnectionResponse,
    IngestionJobResponse,
    IngestionResumeRequest,
)
from src.api.task_manager import TaskManager, TaskState
from src.services.crawler import (
    CrawlResult,
    analyze_page,
    crawl_url,
    get_ingestion_job,
    get_db_status,
    list_ingestion_jobs,
    query_programs,
    resume_crawl_job,
)
from src.services.subject_taxonomy import bootstrap_subject_taxonomy
from src.storage.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

# Stage progress mapping for frontend progress bars
STAGE_PROGRESS_RANGES: dict[str, tuple[float, float]] = {
    "fetch_raw": (10.0, 45.0),
    "extract_structured": (45.0, 70.0),
    "validate_rules": (70.0, 88.0),
    "persist_versioned": (88.0, 98.0),
}


def _stage_progress_start(stage: str) -> float:
    start, _ = STAGE_PROGRESS_RANGES.get(stage, (10.0, 90.0))
    return start


def _stage_progress_end(stage: str) -> float:
    _, end = STAGE_PROGRESS_RANGES.get(stage, (10.0, 90.0))
    return end


def _stage_progress_interpolate(stage: str, ratio: float) -> float:
    start, end = STAGE_PROGRESS_RANGES.get(stage, (10.0, 90.0))
    bounded_ratio = max(0.0, min(1.0, float(ratio)))
    return start + ((end - start) * bounded_ratio)


# ---------------------------------------------------------------------------
#  Logging Utils
# ---------------------------------------------------------------------------


class TaskLogHandler(logging.Handler):
    """Captures log records and pushes them to the active task."""

    def __init__(self, task_manager: TaskManager, task_id: str):
        super().__init__()
        self.task_manager = task_manager
        self.task_id = task_id
        # Simple formatter
        self.formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.task_manager.add_log(self.task_id, msg)
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
#  Config Logic
# ---------------------------------------------------------------------------

PROVIDER_PREFIXES = {
    "volcengine": "VOLC_",
    "deepseek": "DEEPSEEK_",
    "gemini": "GEMINI_",
    "custom": "CUSTOM_LLM_",
}

def _get_env_path() -> Path:
    env_path = find_dotenv()
    if not env_path:
        return Path(".env")
    return Path(env_path)

def _parse_structured_config() -> StructuredConfig:
    env_path = _get_env_path()
    if not env_path.exists():
        return StructuredConfig(
            database_url="",
            llm_priority=[],
            providers={k: {} for k in PROVIDER_PREFIXES}
        )

    # Use python-dotenv to parse values (handles quotes, exports, etc.)
    config_dict = dotenv_values(env_path)
    
    # 1. Database
    db_url = config_dict.get("DATABASE_URL") or ""
    
    # 2. Priority List
    priority_raw = config_dict.get("LLM_PRIORITY_LIST") or ""
    priority_list = [p.strip() for p in priority_raw.split(",") if p.strip()]
    
    # 3. Providers
    providers: Dict[str, Dict[str, str]] = {}
    
    # Initialize known providers
    for name in PROVIDER_PREFIXES:
        providers[name] = {}
        
    for key, value in config_dict.items():
        # For custom provider, include keys even if empty (for UI editing)
        # For other providers, skip empty values
        is_custom_key = key.startswith("CUSTOM_LLM_")
        if not value and not is_custom_key:
            continue
        
        # Match against prefixes
        for name, prefix in PROVIDER_PREFIXES.items():
            if key.startswith(prefix):
                # Strip prefix? No, users expect full keys in env usually, 
                # but for UI it might be cleaner to show 'API_KEY'.
                # Let's keep full keys for robust mapping back to .env
                providers[name][key] = value or ""
                
    return StructuredConfig(
        database_url=db_url,
        llm_priority=priority_list,
        providers=providers,
    )

def _update_env_file_structured(config: StructuredConfig) -> None:
    env_path = _get_env_path()
    
    # Backup
    if env_path.exists():
        backup_path = env_path.with_suffix(".env.bak")
        shutil.copy(env_path, backup_path)
    
    # Read existing lines to preserve comments
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
        
    # Prepare map of new values
    new_values = {}
    new_values["DATABASE_URL"] = config.database_url
    new_values["LLM_PRIORITY_LIST"] = ", ".join(config.llm_priority)
    
    for provider_settings in config.providers.values():
        for k, v in provider_settings.items():
            new_values[k] = v
            
    # Reconstruct content
    output_lines = []
    seen_keys = set()
    
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            output_lines.append(line)
            continue
            
        # Parse key from line (simple split)
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in new_values:
                output_lines.append(f"{key}={new_values[key]}")
                seen_keys.add(key)
            else:
                # Key not in new config? Keep it (don't delete unspecified keys)
                output_lines.append(line)
        else:
            output_lines.append(line)
            
    # Append new keys that weren't in the file
    for key, value in new_values.items():
        if key not in seen_keys:
            output_lines.append(f"{key}={value}")
            
    # Write back
    env_path.write_text("\n".join(output_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
#  FastAPI application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for database initialization."""
    try:
        DatabaseManager().init_db()
        bootstrap_subject_taxonomy()
        logger.info("Database initialised")
    except Exception as e:
        logger.warning("Database init warning: %s", e)
    yield

app = FastAPI(
    title="UniAdmission Agent API",
    description=(
        "REST + MCP interface for crawling university admission pages, "
        "importing data, and querying program information."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

# CORS — allow Chrome extension and local dev tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

task_manager = TaskManager()


# ---------------------------------------------------------------------------
#  REST endpoints
# ---------------------------------------------------------------------------


@app.post("/analyze", response_model=AnalyzeResponse)
async def api_analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """Analyze a page to determine its type and extract candidate links.

    For **index** pages the response contains an LLM-filtered list of
    likely course-detail links so the client can present a selection UI.
    For **detail** pages the ``links`` list is empty.
    """
    result = await asyncio.to_thread(
        analyze_page, body.url, body.html_content, body.page_type_hint,
    )
    return AnalyzeResponse(
        page_type=result["page_type"],
        links=[LinkCandidate(**lk) for lk in result["links"]],
        total_found=result.get("total_found", 0),
    )


@app.post("/crawl", response_model=CrawlResponse)
async def api_crawl(body: CrawlRequest) -> CrawlResponse:
    """Submit a crawl job.

    Returns immediately with a ``task_id``. Poll ``GET /tasks/{task_id}``
    for progress and results.
    Enforces singleton execution (only one crawl at a time).
    """
    try:
        task_id = task_manager.create_task(params=body.model_dump(exclude={"html_content"}))
    except RuntimeError as e:
        # Task already running
        raise HTTPException(status_code=409, detail=str(e))

    async def _run_crawl() -> None:
        # Attach log handler
        log_handler = TaskLogHandler(task_manager, task_id)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        
        task_manager.update_task(
            task_id,
            state=TaskState.RUNNING,
            progress="Queued…",
            progress_percent=2.0,
            progress_meta={"event": "task_started"},
        )
        
        # Snapshot start tokens
        from src.core.token_tracker import tracker
        initial_tokens = sum(u.total_tokens for u in tracker._usage.values())

        try:
            def _on_ingestion_event(event_type: str, payload: dict) -> None:
                stage = payload.get("stage")
                if event_type == "stage_started" and stage:
                    task_manager.update_task(
                        task_id,
                        progress=f"{stage}…",
                        progress_percent=_stage_progress_start(str(stage)),
                        progress_meta={
                            "event": event_type,
                            "stage": stage,
                        },
                    )
                elif event_type == "stage_succeeded" and stage:
                    task_manager.update_task(
                        task_id,
                        progress=f"{stage} completed",
                        progress_percent=_stage_progress_end(str(stage)),
                        progress_meta={
                            "event": event_type,
                            "stage": stage,
                        },
                    )
                elif event_type == "stage_retry_scheduled" and stage:
                    backoff = payload.get("backoff_seconds")
                    task_manager.update_task(
                        task_id,
                        progress=f"{stage} retry in {backoff}s…",
                        progress_percent=_stage_progress_start(str(stage)),
                        progress_meta={
                            "event": event_type,
                            "stage": stage,
                            "backoff_seconds": backoff,
                        },
                    )
                elif event_type == "stage_poisoned" and stage:
                    task_manager.update_task(
                        task_id,
                        progress=f"{stage} poisoned",
                        progress_percent=_stage_progress_start(str(stage)),
                        progress_meta={
                            "event": event_type,
                            "stage": stage,
                        },
                    )
                elif event_type == "fetch_phase":
                    stage_name = str(payload.get("stage") or "fetch_raw")
                    message = str(payload.get("message") or "fetch_raw…")
                    task_manager.update_task(
                        task_id,
                        progress=message,
                        progress_percent=_stage_progress_start(stage_name),
                        progress_meta={
                            "event": event_type,
                            **payload,
                        },
                    )
                elif event_type == "fetch_candidates_identified":
                    total_candidates = int(payload.get("total_candidates") or 0)
                    source = str(payload.get("source") or "index")
                    task_manager.update_task(
                        task_id,
                        progress=(
                            f"fetch_raw: identified {total_candidates} detail links "
                            f"from {source}"
                        ),
                        progress_percent=_stage_progress_interpolate("fetch_raw", 0.25),
                        progress_meta={
                            "event": event_type,
                            **payload,
                        },
                    )
                elif event_type == "fetch_url_progress":
                    current = int(payload.get("current") or 0)
                    total = max(1, int(payload.get("total") or 1))
                    status = str(payload.get("status") or "started")
                    phase = str(payload.get("phase") or "detail")
                    is_finished_one = status in {"succeeded", "failed"}
                    ratio = current / total if is_finished_one else (max(current - 1, 0) / total)
                    task_manager.update_task(
                        task_id,
                        progress=f"fetch_raw ({phase}): {current}/{total}",
                        progress_percent=_stage_progress_interpolate("fetch_raw", max(0.3, ratio)),
                        progress_meta={
                            "event": event_type,
                            **payload,
                        },
                    )

            # We need to periodically update token usage? 
            # Or just update it at the end? 
            # User asked for "display tokens used from start to finish", implying dynamic updates would be nice.
            # But crawl_url is awaited. We can't easily poll during await unless we wrap it or use a separate task.
            # For now, let's update at the end (and maybe start?).
            # Actually, `crawl_url` might be long running. 
            # Ideally we'd have a background poller, but for "Repair", let's keep it simple: 
            # Update at start (0) and end. 
            # If user wants real-time, we need a poller. 
            # Let's add a simple poller task?
            
            stop_event = threading.Event()

            def _poll_tokens_thread() -> None:
                """Poll token usage in a background thread.

                Uses a real OS thread so it is NOT blocked by synchronous
                LLM HTTP calls that starve the asyncio event loop.
                """
                while not stop_event.is_set():
                    stop_event.wait(2)
                    if stop_event.is_set():
                        break
                    current = sum(u.total_tokens for u in tracker._usage.values())
                    used = current - initial_tokens
                    if used > 0:
                        logger.info("Task %s token usage: %d", task_id, used)
                    task_manager.update_task(task_id, tokens_used=used)

            poller_thread = threading.Thread(
                target=_poll_tokens_thread, daemon=True, name=f"token-poll-{task_id}"
            )
            poller_thread.start()

            try:
                result: CrawlResult = await crawl_url(
                    url=body.url,
                    univ_slug=body.univ_slug,
                    year=body.year,
                    continue_depth=body.continue_depth,
                    page_type_hint=body.page_type_hint,
                    export_md=body.export_md,
                    export_path=body.export_path,
                    html_content=body.html_content,
                    selected_urls=body.selected_urls,
                    taxonomy_enabled=body.taxonomy_enabled,
                    taxonomy_low_threshold=body.taxonomy_low_threshold,
                    taxonomy_high_threshold=body.taxonomy_high_threshold,
                    taxonomy_hint_top_k=body.taxonomy_hint_top_k,
                    taxonomy_override_enabled=body.taxonomy_override_enabled,
                    progress_callback=_on_ingestion_event,
                )
            finally:
                stop_event.set()
                poller_thread.join(timeout=3)
                
            # Final update
            final_tokens = sum(u.total_tokens for u in tracker._usage.values())
            task_manager.update_task(
                task_id,
                state=TaskState.DONE,
                progress="Complete",
                result=result.model_dump(),
                tokens_used=final_tokens - initial_tokens,
                progress_percent=100.0,
                progress_meta={"event": "job_succeeded"},
            )
        except asyncio.CancelledError:
            logger.info(f"Task {task_id} cancelled")
            # State update handled by cancel_task or here?
            # cancel_task updates state to FAILED, so we might just return
            return
        except Exception as exc:
            logger.exception("Crawl task %s failed", task_id)
            task_manager.update_task(
                task_id,
                state=TaskState.FAILED,
                error=str(exc),
                progress_percent=100.0,
                progress_meta={"event": "job_failed"},
            )
        finally:
            root_logger.removeHandler(log_handler)

    # Create task object and register it
    task_obj = asyncio.create_task(_run_crawl())
    task_manager.register_task_object(task_id, task_obj)
    
    return CrawlResponse(task_id=task_id)


@app.get("/tasks/active", response_model=Optional[TaskStatusResponse])
async def api_active_task() -> Optional[TaskStatusResponse]:
    """Return the currently active task, if any."""
    info = task_manager.get_active_task()
    if info:
        return TaskStatusResponse(**info.to_dict())
    return None


@app.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def api_task_status(task_id: str) -> TaskStatusResponse:
    """Check the status of a background task."""
    info = task_manager.get_task(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return TaskStatusResponse(**info.to_dict())


@app.get("/ingestion/jobs", response_model=List[IngestionJobResponse])
async def api_ingestion_jobs(
    limit: int = Query(20, ge=1, le=200, description="Number of jobs to return"),
) -> List[IngestionJobResponse]:
    """List recent Phase 2 ingestion jobs."""
    jobs = list_ingestion_jobs(limit=limit)
    return [IngestionJobResponse(**job) for job in jobs]


@app.get("/ingestion/jobs/{job_uid}", response_model=IngestionJobResponse)
async def api_ingestion_job(job_uid: str) -> IngestionJobResponse:
    """Get full stage/task state for one ingestion job."""
    job = get_ingestion_job(job_uid)
    if not job:
        raise HTTPException(status_code=404, detail=f"Ingestion job {job_uid} not found")
    return IngestionJobResponse(**job)


@app.post("/ingestion/jobs/{job_uid}/resume", response_model=CrawlResponse)
async def api_ingestion_resume(
    job_uid: str,
    body: Optional[IngestionResumeRequest] = Body(default=None),
) -> CrawlResponse:
    """Resume a failed/poisoned ingestion job from a specific stage."""
    try:
        task_id = task_manager.create_task(
            params={
                "job_uid": job_uid,
                "resume_from_stage": body.resume_from_stage if body else None,
            }
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    async def _run_resume() -> None:
        log_handler = TaskLogHandler(task_manager, task_id)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)

        task_manager.update_task(task_id, state=TaskState.RUNNING, progress="Resuming…")
        try:
            def _on_ingestion_event(event_type: str, payload: dict) -> None:
                stage = payload.get("stage")
                if event_type == "stage_started" and stage:
                    task_manager.update_task(task_id, progress=f"resume {stage}…")
                elif event_type == "stage_retry_scheduled" and stage:
                    backoff = payload.get("backoff_seconds")
                    task_manager.update_task(
                        task_id,
                        progress=f"resume {stage} retry in {backoff}s…",
                    )

            result = await resume_crawl_job(
                job_uid=job_uid,
                resume_from_stage=body.resume_from_stage if body else None,
                progress_callback=_on_ingestion_event,
            )
            task_manager.update_task(
                task_id,
                state=TaskState.DONE,
                progress="Complete",
                result=result.model_dump(),
            )
        except Exception as exc:
            logger.exception("Resume task %s failed", task_id)
            task_manager.update_task(
                task_id,
                state=TaskState.FAILED,
                error=str(exc),
            )
        finally:
            root_logger.removeHandler(log_handler)

    task_obj = asyncio.create_task(_run_resume())
    task_manager.register_task_object(task_id, task_obj)
    return CrawlResponse(task_id=task_id, message="Resume task submitted")


@app.post("/tasks/{task_id}/cancel", response_model=CancelResponse)
async def api_cancel_task(task_id: str) -> CancelResponse:
    """Cancel a running task."""
    cancelled = task_manager.cancel_task(task_id)
    msg = "Task cancelled" if cancelled else "Task not found or not running"
    if not cancelled:
        # Check if it was already done/failed
        info = task_manager.get_task(task_id)
        if info:
             msg = f"Task is already {info.state.value}"
    
    return CancelResponse(task_id=task_id, cancelled=cancelled, message=msg)


@app.get("/status", response_model=StatusResponse)
async def api_status() -> StatusResponse:
    """Return database statistics."""
    result = get_db_status()
    return StatusResponse(
        university_count=result.university_count,
        program_count=result.program_count,
        universities=[
            {
                "name": u.name,
                "slug": u.slug,
                "year_breakdown": u.year_breakdown,
            }
            for u in result.universities
        ],
    )


@app.get("/programs", response_model=List[ProgramResponse])
async def api_programs(
    univ_slug: str = Query(..., description="University slug"),
    year: Optional[int] = Query(None, description="Academic year filter"),
) -> List[ProgramResponse]:
    """Query programs for a university."""
    programs = query_programs(univ_slug=univ_slug, year=year)
    return [ProgramResponse(**p.model_dump()) for p in programs]


@app.get("/universities", response_model=List[UniversityResponse])
async def api_universities() -> List[UniversityResponse]:
    """Return all universities ordered by most recently updated first."""
    from src.models.admission import University
    from sqlmodel import select, col

    db = DatabaseManager()
    with db.get_session() as session:
        stmt = select(University).order_by(col(University.updated_at).desc())
        universities = session.exec(stmt).all()
        return [
            UniversityResponse(
                slug=u.slug,
                name=u.name,
                updated_at=u.updated_at.isoformat() if u.updated_at else "",
            )
            for u in universities
        ]


@app.post("/export")
async def api_export(body: ExportRequest):
    """Export programs for a university as a downloadable Excel file."""
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    from src.storage.exporter import ExcelExporter

    slug = body.univ_slug.strip().lower()
    year = body.year

    # Generate in-memory Excel
    buf = BytesIO()
    exporter = ExcelExporter(output_stream=buf)
    count = exporter.export_data(univ_slug=slug, year=year)

    if count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No programs found for '{slug}'" + (f" ({year})" if year else ""),
        )

    buf.seek(0)
    filename = f"{slug}_{year}.xlsx" if year else f"{slug}_all.xlsx"

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/config", response_model=ConfigResponse)
async def api_get_config() -> ConfigResponse:
    """Read .env configuration (Raw)."""
    env_path = _get_env_path()
    if not env_path.exists():
        return ConfigResponse(content="")
    
    try:
        content = env_path.read_text(encoding="utf-8")
        return ConfigResponse(content=content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read .env: {e}")


@app.post("/config", response_model=ConfigResponse)
async def api_update_config(body: ConfigRequest) -> ConfigResponse:
    """Update .env configuration (Raw) (with backup)."""
    env_path = _get_env_path()
    
    # Backup existing
    if env_path.exists():
        backup_path = env_path.with_suffix(".env.bak")
        shutil.copy(env_path, backup_path)
    
    try:
        env_path.write_text(body.content, encoding="utf-8")
        return ConfigResponse(content=body.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write .env: {e}")


@app.get("/config/structured", response_model=StructuredConfig)
async def api_get_structured_config() -> StructuredConfig:
    """Read .env configuration as structured JSON."""
    try:
        return _parse_structured_config()
    except Exception as e:
        logger.exception("Failed to parse config")
        raise HTTPException(status_code=500, detail=f"Failed to parse .env: {e}")


@app.post("/config/structured", response_model=StructuredConfig)
async def api_update_structured_config(body: StructuredConfig) -> StructuredConfig:
    """Update .env configuration from structured JSON."""
    try:
        _update_env_file_structured(body)
        return body
    except Exception as e:
        logger.exception("Failed to update config")
        raise HTTPException(status_code=500, detail=f"Failed to update .env: {e}")


@app.post("/config/test-connection", response_model=TestConnectionResponse)
async def api_test_connection(body: TestConnectionRequest) -> TestConnectionResponse:
    """Test connectivity to an OpenAI-compatible LLM endpoint."""
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=body.api_key or "no-key",
            base_url=body.base_url,
            timeout=15.0,
        )

        model = body.model_name or "gpt-4o-mini"
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say hello in one word."}],
            max_tokens=body.max_tokens if body.max_tokens <= 64 else 64,
            temperature=body.temperature,
        )

        text = ""
        if response.choices:
            text = (response.choices[0].message.content or "").strip()

        return TestConnectionResponse(
            success=True,
            message=f"OK — {model} replied: \"{text[:60]}\"",
        )

    except Exception as e:
        err_msg = str(e)
        # Truncate long error messages
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "…"
        return TestConnectionResponse(
            success=False,
            message=err_msg,
        )


# ---------------------------------------------------------------------------
#  MCP tools (via FastMCP)
# ---------------------------------------------------------------------------

try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("UniAdmission Agent")

    @mcp.tool()
    async def mcp_crawl(
        url: str,
        univ_slug: str,
        year: int,
        continue_depth: int = 0,
    ) -> dict:
        """Crawl a university admission page and import structured data.

        Fetches the page with stealth browsing, detects page type,
        extracts program details via LLM, and upserts to the database.

        Args:
            url: Starting URL to crawl (e.g. https://admissions.hku.hk/programmes).
            univ_slug: University identifier (e.g. "hku").
            year: Academic year (e.g. 2026).
            continue_depth: Extra depth levels for LLM-driven link scouting.

        Returns:
            Dict with imported_count, univ_slug, and year.
        """
        # MCP calls bypass task_manager for now to keep it simple, 
        # as MCP tools are usually synchronous-ish from the caller's perspective
        # or managed by the caller. 
        # To support logging/cancellation in MCP, we'd need to wrap this too.
        # For now, direct call is fine.
        result = await crawl_url(
            url=url,
            univ_slug=univ_slug,
            year=year,
            continue_depth=continue_depth,
        )
        return result.model_dump()

    @mcp.tool()
    def mcp_db_query(
        univ_slug: str,
        year: Optional[int] = None,
    ) -> list:
        """Query programs for a university from the database.

        Returns a list of program records with name, tuition, deadlines,
        subject requirements, and other structured fields.

        Args:
            univ_slug: University identifier (e.g. "hku").
            year: Optional academic year filter. If omitted, returns all years.

        Returns:
            List of program dicts.
        """
        programs = query_programs(univ_slug=univ_slug, year=year)
        return [p.model_dump() for p in programs]

    @mcp.tool()
    def mcp_help(
        verbose: bool = False,
    ) -> dict:
        """Show comprehensive help for all available CLI commands and usage examples.

        Provides detailed information about university data management, database operations,
        server controls, system maintenance, and usage examples.

        Args:
            verbose: Include detailed command options and parameters.

        Returns:
            Dict with help_text and available_commands.
        """
        # Import here to avoid circular imports
        from src.cmd.cli import get_help_text
        
        help_text = get_help_text()
        
        if verbose:
            help_text += "\n\n" + """
DETAILED COMMAND OPTIONS:

crawl:
    --name      University slug (a-z0-9-) 
    --year      Academic year (e.g., 2026)
    --url       Starting URL to crawl
    --continue  Extra depth for LLM scouting (default: 0)
    
import:
    --name      University slug
    --year      Academic year
    --file      Path to XLSX file
    --llm       Enable LLM analysis (optional)
    
export:
    --name      University slug
    --output    Output file path
    --year      Academic year (optional)
    
serve:
    --host      Host address (default: 0.0.0.0)
    --port      Port number (default: 8910)
    --verbose   Debug logging
    
upgrade:
    --check     Only check for updates, don't install
    --force     Force upgrade even if already latest
    --migrate   Run DB migration after backend update
    --verbose   Show detailed progress

repair:
    --auto      Run automatic rollback-safe repair
    --verbose   Show detailed progress
            """
        
        available_commands = [
            "crawl", "import", "export", "status", "check", 
            "serve", "serve-stop", "upgrade", "db-migrate", "db-version", "repair", "version",
            "browser-install", "ingestion-jobs", "ingestion-resume",
            "golden-collect", "quality-score", "help"
        ]
        
        return {
            "help_text": help_text,
            "available_commands": available_commands,
            "version": "UniAdmission Agent CLI",
            "description": "Automated university admission data scraper"
        }

    # Mount MCP as a sub-application at /mcp
    app.mount("/mcp", mcp.sse_app())
    logger.info("MCP tools registered: crawl, db_query, help")

except ImportError:
    logger.info("MCP SDK not installed — MCP tools disabled. Install with: uv add 'mcp[cli]'")
