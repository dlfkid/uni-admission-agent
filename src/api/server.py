"""
FastAPI + MCP server for UniAdmission Agent.

Exposes:
    REST endpoints — ``/crawl``, ``/tasks/{id}``, ``/status``, ``/programs``, ``/config``, ``/cancel``
    MCP tools     — ``analyze``, ``crawl_detail_batch``, ``crawl``, ``db_query``

Start via CLI:
    uv run src/cmd/cli.py serve          # defaults to 0.0.0.0:8910
    uv run src/cmd/cli.py serve --port 9000

Or directly:
    uvicorn src.api.server:app --port 8910
"""

import asyncio
import copy
import json
import logging
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
from io import StringIO

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Body, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import find_dotenv, dotenv_values

from src.api.schemas import (
    CrawlRequest,
    CrawlResponse,
    AgentRunRequest,
    AgentRunResponse,
    AgentChatRequest,
    AgentChatResponse,
    AgentReviewConfirmRequest,
    AgentReviewConfirmResponse,
    ProgramResponse,
    ProgramPatchRequest,
    DeleteProgramResponse,
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
    ClientInfoResponse,
)
from src.api.task_manager import TaskManager, TaskState
from src.core.feature_flags import is_agent_enabled_env
from src.services import browser_provider as browser_provider_service
from src.services.client_bridge import ClientRegistry, ClientSession, ClientRpcBroker
from src.services.crawler import (
    CrawlResult,
    analyze_page,
    analyze_url_candidates,
    crawl_selected_detail_urls_via_client,
    crawl_url,
    ingest_program_records_external,
    get_ingestion_job,
    get_db_status,
    list_ingestion_jobs,
    delete_program_snapshot,
    patch_program_snapshot,
    query_programs,
    run_agent_crawl,
    run_agent_chat,
    resume_crawl_job,
)
from src.agent_runtime.review_selection import parse_selected_indices
from src.agent_runtime.review_service import run_agent_review_confirmation
from src.services.ingestion_pipeline import IngestionPipeline
from src.services.subject_taxonomy import bootstrap_subject_taxonomy
from src.storage.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


def get_db_manager() -> DatabaseManager:
    """Indirection so tests can monkeypatch the DB without touching globals."""
    return DatabaseManager()

# Stage progress mapping for frontend progress bars
STAGE_PROGRESS_RANGES: dict[str, tuple[float, float]] = {
    "fetch_raw": (10.0, 45.0),
    "extract_structured": (45.0, 70.0),
    "validate_rules": (70.0, 88.0),
    "persist_versioned": (88.0, 98.0),
}

def is_agent_enabled(explicit_flag: bool | None = None) -> bool:
    """Resolve whether agent runtime is enabled."""
    return is_agent_enabled_env(explicit_flag)


def _noop_register_agent_mcp_tools() -> None:
    """No-op placeholder used when MCP runtime is unavailable."""
    return


_register_agent_mcp_tools_if_enabled = _noop_register_agent_mcp_tools
_agent_mcp_tools_state = {"registered": False}


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
    global _main_loop  # pylint: disable=global-statement
    _main_loop = asyncio.get_running_loop()
    try:
        _register_agent_mcp_tools_if_enabled()
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
client_registry = ClientRegistry()
client_sockets: Dict[str, WebSocket] = {}
_main_loop: Optional[asyncio.AbstractEventLoop] = None  # set on startup
client_rpc_broker = ClientRpcBroker(timeout_seconds=120.0)


def _has_available_client(preferred_client_id: Optional[str]) -> bool:
    return client_registry.select_client_id(preferred_client_id) is not None


def _select_available_client_id(preferred_client_id: Optional[str]) -> Optional[str]:
    return client_registry.select_client_id(preferred_client_id)


async def _rpc_send_and_wait(
    websocket: WebSocket,
    request_id: str,
    url: str,
    page_type_hint: str,
) -> Dict[str, Any]:
    """Send RPC request and wait for response. Must run on the main event loop."""
    await websocket.send_json(
        {
            "type": "rpc_request",
            "request_id": request_id,
            "action": "fetch_browser_payload",
            "payload": {"url": url, "page_type_hint": page_type_hint},
        }
    )
    return dict(await client_rpc_broker.wait_for_response(request_id) or {})


async def _fetch_browser_payload_from_client(
    *,
    url: str,
    page_type_hint: str,
    client_id: Optional[str],
) -> Dict[str, Any]:
    """Dispatch a browser fetch RPC. Must be called on the main event loop."""
    target_client_id = client_registry.select_client_id(client_id)
    if not target_client_id:
        raise RuntimeError("No available client for browser automation")

    websocket = client_sockets.get(target_client_id)
    if websocket is None:
        raise RuntimeError(f"Client websocket unavailable: {target_client_id}")

    request_id, _future = client_rpc_broker.create_pending(target_client_id)
    logger.info(
        "[RPC] Dispatching fetch_browser_payload to client=%s request_id=%s url=%s",
        target_client_id, request_id, url,
    )
    payload = await _rpc_send_and_wait(websocket, request_id, url, page_type_hint)
    logger.info(
        "[RPC] Client %s responded to request_id=%s (payload keys: %s)",
        target_client_id, request_id, list(payload.keys()),
    )
    return payload


def _fetch_browser_payload_from_client_sync(
    *,
    url: str,
    page_type_hint: str,
    client_id: Optional[str],
) -> Dict[str, Any]:
    """Thread-safe sync wrapper: schedules RPC on the main event loop and blocks."""
    if _main_loop is None:
        raise RuntimeError("Server main event loop not initialized")
    future = asyncio.run_coroutine_threadsafe(
        _fetch_browser_payload_from_client(url=url, page_type_hint=page_type_hint, client_id=client_id),
        _main_loop,
    )
    return future.result(timeout=120)


browser_provider_service.configure_client_dispatchers(
    availability_fn=_has_available_client,
    fetch_fn=_fetch_browser_payload_from_client_sync,
    select_client_fn=_select_available_client_id,
)


# ---------------------------------------------------------------------------
#  REST endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def api_health() -> dict:
    """Lightweight liveness probe. Does not touch the database."""
    return {"status": "ok", "agent_enabled": is_agent_enabled()}


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
                    selected_link_texts=body.selected_link_texts,
                    browser_automation_enabled=body.browser_automation_enabled,
                    detail_pages_batch=(
                        [item.model_dump() for item in body.detail_pages_batch]
                        if body.detail_pages_batch
                        else None
                    ),
                    batch_index=body.batch_index,
                    batch_total=body.batch_total,
                    browser_provider=body.browser_provider,
                    client_id=body.client_id,
                    strict_client=body.strict_client,
                    candidate_taxonomy_filter_enabled=body.candidate_taxonomy_filter_enabled,
                    candidate_taxonomy_filter_threshold=body.candidate_taxonomy_filter_threshold,
                    candidate_taxonomy_filter_top_k=body.candidate_taxonomy_filter_top_k,
                    taxonomy_enabled=body.taxonomy_enabled,
                    taxonomy_low_threshold=body.taxonomy_low_threshold,
                    taxonomy_high_threshold=body.taxonomy_high_threshold,
                    taxonomy_hint_top_k=body.taxonomy_hint_top_k,
                    taxonomy_override_enabled=body.taxonomy_override_enabled,
                    name_resolution_llm_enabled=body.name_resolution_llm_enabled,
                    name_resolution_low_threshold=body.name_resolution_low_threshold,
                    name_resolution_conflict_delta=body.name_resolution_conflict_delta,
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


@app.post("/agent/run", response_model=AgentRunResponse)
async def api_agent_run(body: AgentRunRequest) -> AgentRunResponse:
    """Submit one agent orchestration job."""
    if not is_agent_enabled():
        raise HTTPException(
            status_code=409,
            detail=(
                "Agent runtime is disabled for this server process. "
                "Set AGENT_ENABLED=true to re-enable it."
            ),
        )

    try:
        task_id = task_manager.create_task(
            params={
                "mode": "agent",
                **body.model_dump(exclude_none=True),
            }
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    async def _run_agent_job() -> None:
        log_handler = TaskLogHandler(task_manager, task_id)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)

        accumulated_tokens = 0
        program_count = 0

        def _event_sink(event: dict[str, Any]) -> None:
            nonlocal accumulated_tokens, program_count
            task_manager.add_event(task_id, event)

            # Accumulate token usage from streaming LLM calls
            if event.get("type") == "token_usage":
                accumulated_tokens += event.get("total_tokens", 0)
                task_manager.update_task(task_id, tokens_used=accumulated_tokens)

            # Count persisted programs from tool calls
            if event.get("type") == "tool_call_finished" and event.get("tool") == "persist_programs_skill":
                program_count += 1
                task_manager.update_task(
                    task_id,
                    progress=f"Agent running… ({program_count} program(s) saved)",
                    progress_meta={"event": "program_persisted", "program_count": program_count},
                )

        task_manager.update_task(
            task_id,
            state=TaskState.RUNNING,
            progress="Agent running…",
            progress_percent=3.0,
            progress_meta={"event": "agent_task_started"},
        )
        try:
            result = await run_agent_crawl(
                url=body.url,
                univ_slug=body.univ_slug,
                year=body.year,
                page_type_hint=body.page_type_hint,
                runtime_mode=body.runtime,
                autonomous=body.autonomous,
                dry_run=body.dry_run,
                event_sink=_event_sink,
                policy_profile=(
                    body.policy_profile.model_dump(exclude_none=True)
                    if body.policy_profile
                    else None
                ),
                auto_paginate=body.auto_paginate,
                max_pages=body.max_pages,
            )
            if isinstance(result, dict):
                result["program_count"] = program_count
                result["tokens_used"] = accumulated_tokens
            task_manager.update_task(
                task_id,
                state=TaskState.DONE,
                progress="Complete",
                result=result,
                tokens_used=accumulated_tokens,
                progress_percent=100.0,
                progress_meta={"event": "agent_task_succeeded", "program_count": program_count},
            )
        except Exception as exc:
            logger.exception("Agent task %s failed", task_id)
            task_manager.update_task(
                task_id,
                state=TaskState.FAILED,
                error=str(exc),
                progress_percent=100.0,
                progress_meta={"event": "agent_task_failed"},
            )
        finally:
            root_logger.removeHandler(log_handler)

    task_obj = asyncio.create_task(_run_agent_job())
    task_manager.register_task_object(task_id, task_obj)
    return AgentRunResponse(task_id=task_id)


@app.post("/agent/chat", response_model=AgentChatResponse)
async def api_agent_chat(body: AgentChatRequest) -> AgentChatResponse:
    """Submit a free-form chat message to the agent using the server-side LLM.

    Returns a ``task_id`` immediately.  Subscribe to ``GET /tasks/{task_id}/events``
    for real-time streaming output (``summary_delta``, tool lifecycle events, etc.).
    """
    if not is_agent_enabled():
        raise HTTPException(
            status_code=409,
            detail=(
                "Agent runtime is disabled for this server process. "
                "Set AGENT_ENABLED=true to re-enable it."
            ),
        )

    try:
        task_id = task_manager.create_task(
            params={
                "mode": "chat",
                "message": body.message,
            }
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    async def _run_chat_job() -> None:
        log_handler = TaskLogHandler(task_manager, task_id)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)

        def _event_sink(event: dict[str, Any]) -> None:
            task_manager.add_event(task_id, event)

        task_manager.update_task(
            task_id,
            state=TaskState.RUNNING,
            progress="Agent thinking…",
            progress_percent=3.0,
            progress_meta={"event": "chat_task_started"},
        )
        try:
            result = await run_agent_chat(
                message=body.message,
                context=body.context,
                event_sink=_event_sink,
            )
            task_manager.update_task(
                task_id,
                state=TaskState.DONE,
                progress="Complete",
                result=result,
                progress_percent=100.0,
                progress_meta={"event": "chat_task_succeeded"},
            )
        except Exception as exc:
            logger.exception("Chat task %s failed", task_id)
            task_manager.update_task(
                task_id,
                state=TaskState.FAILED,
                error=str(exc),
                progress_percent=100.0,
                progress_meta={"event": "chat_task_failed"},
            )
        finally:
            root_logger.removeHandler(log_handler)

    task_obj = asyncio.create_task(_run_chat_job())
    task_manager.register_task_object(task_id, task_obj)
    return AgentChatResponse(task_id=task_id)


def _collect_review_selected_indices(
    *,
    selection_text: Optional[str],
    selected_indices: Optional[List[int]],
) -> tuple[list[int], list[str]]:
    selected: list[int] = []
    invalid_tokens: list[str] = []

    if selection_text is not None:
        parsed = parse_selected_indices(selection_text)
        selected.extend(parsed.selected)
        invalid_tokens.extend(parsed.invalid_tokens)

    if selected_indices:
        selected.extend(int(value) for value in selected_indices)

    return sorted(set(selected)), invalid_tokens


def _extract_onhold_indices(onhold_items: List[Dict[str, Any]]) -> set[int]:
    output: set[int] = set()
    for pos, item in enumerate(list(onhold_items or []), start=1):
        row = dict(item or {})
        try:
            value = int(row.get("index") or pos)
        except (TypeError, ValueError):
            value = pos
        if value > 0:
            output.add(value)
    return output


@app.post("/agent/review/confirm", response_model=AgentReviewConfirmResponse)
async def api_agent_review_confirm(body: AgentReviewConfirmRequest) -> AgentReviewConfirmResponse:
    """Confirm and apply selected low-confidence onhold indices for an agent task."""
    if not is_agent_enabled():
        raise HTTPException(
            status_code=409,
            detail=(
                "Agent runtime is disabled for this server process. "
                "Set AGENT_ENABLED=true to re-enable it."
            ),
        )

    info = task_manager.get_task(body.task_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Task {body.task_id} not found")
    if not isinstance(info.result, dict):
        raise HTTPException(status_code=409, detail="Task result is not ready for review confirmation")

    result_payload = copy.deepcopy(info.result)
    output_payload = result_payload.get("output")
    if not isinstance(output_payload, dict):
        raise HTTPException(status_code=409, detail="Task result has no output payload")

    raw_onhold_items = output_payload.get("onhold_items")
    if not isinstance(raw_onhold_items, list) or not raw_onhold_items:
        raise HTTPException(status_code=400, detail="Task has no onhold items to confirm")

    selected_indices, invalid_tokens = _collect_review_selected_indices(
        selection_text=body.selection_text,
        selected_indices=body.selected_indices,
    )
    if invalid_tokens:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_selection_text",
                "invalid_tokens": invalid_tokens,
            },
        )

    available_indices = _extract_onhold_indices(raw_onhold_items)
    invalid_indices = [idx for idx in selected_indices if idx not in available_indices or idx <= 0]
    if invalid_indices:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_indices",
                "invalid_indices": invalid_indices,
            },
        )

    request_payload = output_payload.get("request_payload")
    if not isinstance(request_payload, dict):
        request_payload = dict(info.params or {})

    confirmation = await run_agent_review_confirmation(
        task_payload=request_payload,
        onhold_items=raw_onhold_items,
        selected_indices=selected_indices,
    )

    confirmation_payload = {
        "selection_text": str(body.selection_text or ""),
        "selected_indices": selected_indices,
        "invalid_tokens": invalid_tokens,
        **confirmation,
    }
    output_payload["onhold_confirmation"] = confirmation_payload
    output_payload["onhold_items_pending"] = []
    output_payload["applied_onhold_items"] = confirmation.get("applied_items") or []
    output_payload["discarded_onhold_items"] = confirmation.get("discarded_items") or []
    output_payload["onhold_count"] = 0
    output_payload["onhold_items"] = []

    applied_result = confirmation.get("applied_result")
    if isinstance(applied_result, dict):
        if "review_items" in applied_result:
            output_payload["review_items"] = list(applied_result.get("review_items") or [])
        if str(applied_result.get("review_token") or "").strip():
            output_payload["review_token"] = str(applied_result.get("review_token") or "").strip()

    trace_payload = result_payload.get("trace")
    if isinstance(trace_payload, list):
        trace_payload.append(
            {
                "stage": "apply_selected_onhold",
                "selected_count": int(confirmation.get("selected_count") or 0),
                "discarded_count": int(confirmation.get("discarded_count") or 0),
            }
        )

    result_payload["status"] = "done"
    result_payload["output"] = output_payload
    task_manager.update_task(
        body.task_id,
        state=TaskState.DONE,
        progress="Agent review confirmed",
        result=result_payload,
        progress_percent=100.0,
        progress_meta={"event": "agent_review_confirmed"},
    )

    return AgentReviewConfirmResponse(
        task_id=body.task_id,
        selected_indices=selected_indices,
        invalid_indices=list(confirmation.get("invalid_indices") or []),
        invalid_tokens=invalid_tokens,
        selected_count=int(confirmation.get("selected_count") or 0),
        discarded_count=int(confirmation.get("discarded_count") or 0),
        total_onhold=int(confirmation.get("total_onhold") or 0),
    )


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


@app.get("/tasks/{task_id}/events")
async def api_task_events(task_id: str, request: Request) -> StreamingResponse:
    """Stream stored task events over SSE for agent progress UIs."""
    info = task_manager.get_task(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    async def event_stream():
        event_index = 0
        try:
            while True:
                current = task_manager.get_task(task_id)
                if current is None:
                    break

                while event_index < len(current.events):
                    event = current.events[event_index]
                    event_index += 1
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                if current.state in (TaskState.DONE, TaskState.FAILED):
                    break
                if await request.is_disconnected():
                    break

                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            return

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/clients", response_model=List[ClientInfoResponse])
async def api_clients() -> List[ClientInfoResponse]:
    """List connected browser-automation clients."""
    rows = client_registry.list_clients()
    return [ClientInfoResponse(**row) for row in rows]


@app.websocket("/clients/ws")
async def ws_clients(websocket: WebSocket) -> None:
    """Client bridge websocket for register/heartbeat/rpc payload relay."""
    await websocket.accept()
    registered_client_id: Optional[str] = None
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "message": "invalid payload"})
                continue

            msg_type = str(message.get("type") or "").strip().lower()
            if msg_type == "register":
                client_id = str(message.get("client_id") or "").strip()
                if not client_id:
                    await websocket.send_json(
                        {"type": "error", "message": "client_id is required"}
                    )
                    continue

                session = ClientSession(
                    client_id=client_id,
                    client_name=str(message.get("client_name") or client_id).strip(),
                    platform=str(message.get("platform") or "").strip(),
                    arch=str(message.get("arch") or "").strip(),
                    workdir=str(message.get("workdir") or "").strip(),
                    capabilities=dict(message.get("capabilities") or {}),
                )
                client_registry.register(session)
                client_sockets[client_id] = websocket
                registered_client_id = client_id
                await websocket.send_json({"type": "registered", "client_id": client_id})
                continue

            if msg_type == "heartbeat":
                target_client_id = str(
                    message.get("client_id") or registered_client_id or ""
                ).strip()
                if not target_client_id:
                    await websocket.send_json(
                        {"type": "error", "message": "unknown client for heartbeat"}
                    )
                    continue
                ok = client_registry.heartbeat(target_client_id)
                if not ok:
                    await websocket.send_json(
                        {"type": "error", "message": "client not registered"}
                    )
                    continue
                await websocket.send_json(
                    {"type": "heartbeat_ack", "client_id": target_client_id}
                )
                continue

            if msg_type == "rpc_result":
                request_id = str(message.get("request_id") or "").strip()
                payload = message.get("payload")
                payload_dict = payload if isinstance(payload, dict) else {}
                accepted = False
                if request_id:
                    accepted = client_rpc_broker.resolve(request_id, payload_dict)
                logger.info(
                    "[RPC] Received rpc_result from client=%s request_id=%s accepted=%s",
                    registered_client_id,
                    request_id,
                    accepted,
                )
                await websocket.send_json(
                    {
                        "type": "rpc_ack",
                        "request_id": request_id or None,
                        "accepted": accepted,
                    }
                )
                continue

            if msg_type == "rpc_error":
                request_id = str(message.get("request_id") or "").strip()
                error_msg = str(message.get("message") or "client rpc error")
                accepted = False
                if request_id:
                    accepted = client_rpc_broker.fail(request_id, error_msg)
                logger.warning(
                    "[RPC] Received rpc_error from client=%s request_id=%s error=%s",
                    registered_client_id,
                    request_id,
                    error_msg,
                )
                await websocket.send_json(
                    {
                        "type": "rpc_ack",
                        "request_id": request_id or None,
                        "accepted": accepted,
                    }
                )
                continue

            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"unsupported message type: {msg_type or '<empty>'}",
                }
            )
    except WebSocketDisconnect:
        logger.info("Client websocket disconnected: %s", registered_client_id or "unknown")
    finally:
        if registered_client_id:
            current = client_sockets.get(registered_client_id)
            if current is websocket:
                client_sockets.pop(registered_client_id, None)
            client_rpc_broker.fail_all_for_client(
                registered_client_id,
                "Client disconnected",
            )
            client_registry.remove(registered_client_id)


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
    """Return database statistics and connected client info."""
    result = get_db_status()
    
    # Fetch connected clients
    client_ids = [c["client_id"] for c in client_registry.list_clients()]
    
    return StatusResponse(
        university_count=result.university_count,
        program_count=result.program_count,
        client_count=len(client_ids),
        client_ids=client_ids,
        agent_enabled=is_agent_enabled(),
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


@app.delete("/programs/{program_id}", response_model=DeleteProgramResponse)
async def api_delete_program(program_id: int) -> DeleteProgramResponse:
    """Delete one program snapshot by ID."""
    deleted = delete_program_snapshot(program_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Program {program_id} not found.")
    return DeleteProgramResponse(
        program_id=program_id,
        deleted=True,
        message="Program snapshot deleted.",
    )


@app.patch("/programs/{program_id}", response_model=ProgramResponse)
async def api_patch_program(
    program_id: int,
    body: ProgramPatchRequest = Body(...),
) -> ProgramResponse:
    """Partially update one program snapshot by ID."""
    patch_payload = body.model_dump(exclude_unset=True)
    if not patch_payload:
        raise HTTPException(status_code=400, detail="Patch payload cannot be empty.")

    blocked_fields = {
        "id",
        "university_id",
        "program_catalog_id",
        "academic_year",
    }
    blocked = sorted(blocked_fields.intersection(set(patch_payload.keys())))
    if blocked:
        joined = ", ".join(blocked)
        raise HTTPException(
            status_code=400,
            detail=f"Forbidden fields in patch payload: {joined}",
        )

    try:
        updated = patch_program_snapshot(program_id, patch_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not updated:
        raise HTTPException(status_code=404, detail=f"Program {program_id} not found.")
    return ProgramResponse(**updated.model_dump())


@app.get("/quarantine")
async def api_quarantine_list(
    university: Optional[str] = None,
    year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """List quarantined program extractions (failed the quality gate).

    Filter by ``university`` slug and/or academic ``year``. Each entry
    includes the diagnostic ``quarantine_signals`` blob useful for
    deciding whether to manually re-run or repair the extraction.
    """
    db = get_db_manager()
    entries = db.list_quarantine(university_slug=university, year=year)
    return [
        {
            "id": entry.id,
            "university_slug": entry.university_slug,
            "academic_year": entry.academic_year,
            "source_url": entry.source_url,
            "extracted_name": entry.extracted_name,
            "quarantine_reason": entry.quarantine_reason,
            "quarantine_signals": entry.quarantine_signals,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }
        for entry in entries
    ]


@app.delete("/quarantine")
async def api_quarantine_clear(
    university: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, int]:
    """Delete quarantine entries for one university.

    ``university`` is required (no bulk-clear-all from REST).  If
    ``reason`` is provided, only entries with that quarantine_reason
    are deleted.
    """
    from src.services.quality_gate import QuarantineReason

    if not university:
        raise HTTPException(
            status_code=400, detail="university query param is required"
        )

    reason_enum = None
    if reason is not None:
        try:
            reason_enum = QuarantineReason(reason)
        except ValueError as exc:
            valid = ", ".join(r.value for r in QuarantineReason)
            raise HTTPException(
                status_code=400,
                detail=f"invalid reason {reason!r}; valid values: {valid}",
            ) from exc

    db = get_db_manager()
    deleted = db.clear_quarantine(
        university_slug=university, reason=reason_enum
    )
    return {"deleted": deleted}


@app.get("/audit")
async def api_audit_list(
    university: Optional[str] = None,
    year: Optional[int] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """List index→detail extraction funnel records (newest first).

    Each entry exposes the full funnel: raw links on the index page,
    LLM/heuristic-filtered subset, final candidate set, and how many
    became committed programs vs. quarantined.
    """
    db = get_db_manager()
    entries = db.list_extraction_audit(
        university_slug=university, year=year, limit=int(limit)
    )
    return [
        {
            "id": e.id,
            "university_slug": e.university_slug,
            "academic_year": e.academic_year,
            "index_url": e.index_url,
            "raw_link_count": e.raw_link_count,
            "llm_filtered_count": e.llm_filtered_count,
            "candidate_count": e.candidate_count,
            "extracted_count": e.extracted_count,
            "quarantined_count": e.quarantined_count,
            "job_uid": e.job_uid,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]


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


def _internal_llm_available() -> bool:
    """Best-effort probe for server-side internal LLM availability."""
    try:
        from src.agents.factory import create_router

        create_router()
    except Exception as exc:  # pylint: disable=broad-except
        logger.info("Internal LLM unavailable: %s", exc)
        return False
    return True


def _runtime_status_payload() -> dict[str, Any]:
    client_rows = client_registry.list_clients()
    client_ids = [
        str(row.get("client_id") or "").strip()
        for row in client_rows
        if (
            str(row.get("client_id") or "").strip()
            and bool((row.get("capabilities") or {}).get("browser_automation"))
        )
    ]
    client_available = client_registry.select_client_id(None) is not None
    return {
        "client_available": client_available,
        "client_count": len(client_ids),
        "client_ids": client_ids,
        "internal_llm_available": _internal_llm_available(),
        "default_browser_provider_resolved": "client" if client_available else "server",
    }


def _resolve_provider_metadata_for_response(
    *,
    browser_provider: str,
    client_id: Optional[str],
    strict_client: bool,
) -> dict[str, Any]:
    try:
        return browser_provider_service.resolve_provider_metadata(
            browser_provider=browser_provider,
            client_id=client_id,
            strict_client=strict_client,
        )
    except RuntimeError:
        raise
    except Exception:
        return {
            "resolved_browser_provider": "server",
            "client_id_used": None,
        }


def _rank_index_candidates_by_taxonomy(
    *,
    links: List[Dict[str, Any]],
    keep_threshold: float = 0.75,
    auto_run_threshold: float = 0.92,
    top_k: int = 30,
) -> List[Dict[str, Any]]:
    pipeline = IngestionPipeline()
    return pipeline.rank_index_candidates(
        links,
        keep_threshold=keep_threshold,
        auto_run_threshold=auto_run_threshold,
        top_k=top_k,
    )


def _to_valid_year(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_page_type_hint(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "auto"
    normalized = raw.lower()
    compact = normalized.replace(" ", "").replace("-", "").replace("_", "")

    alias_map = {
        # auto
        "auto": "auto",
        "自动": "auto",
        "自动识别": "auto",
        "默认": "auto",
        # index-like
        "index": "index",
        "listing": "index",
        "list": "index",
        "索引": "index",
        "目录": "index",
        "列表": "index",
        "索引页": "index",
        "目录页": "index",
        "列表页": "index",
        # detail-like
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


def _connected_browser_client_ids() -> List[str]:
    rows = client_registry.list_clients()
    return [
        str(row.get("client_id") or "").strip()
        for row in rows
        if (
            str(row.get("client_id") or "").strip()
            and bool((row.get("capabilities") or {}).get("browser_automation"))
        )
    ]


def _client_id_usage_metadata(
    *,
    client_id: Optional[str],
) -> Dict[str, Any]:
    requested = str(client_id or "").strip()
    payload: Dict[str, Any] = {
        "client_id_expected_source": (
            "client_id must come from runtime_status.client_ids; "
            "do not pass university slug (use univ_slug in crawl/crawl_detail_batch)."
        ),
    }
    if not requested:
        return payload

    payload["client_id_requested"] = requested
    online_ids = _connected_browser_client_ids()
    if requested not in online_ids:
        payload["client_id_warning"] = (
            f"'{requested}' is not an online browser client id. "
            "Use runtime_status.client_ids."
        )
    return payload


def _analyze_next_step_options(
    *,
    page_type: str,
) -> List[Dict[str, Any]]:
    normalized = str(page_type or "").strip().lower()
    if normalized == "detail":
        return [
            {
                "mode": "external_llm_or_server_pipeline",
                "tool": "crawl",
                "when": "Use direct detail-page crawl/import path.",
            },
            {
                "mode": "server_llm",
                "tool": "crawl_internal_llm",
                "when": "Use server-side LLM path for detail-page crawl/import.",
            },
        ]
    if normalized == "index":
        return [
            {
                "mode": "external_llm",
                "tool": "ingest",
                "when": "After selecting candidates and externally extracting structured programs[].",
            },
            {
                "mode": "external_or_hybrid",
                "tool": "crawl_detail_batch",
                "when": "Use selected candidate URLs for batched detail crawl/import.",
            },
            {
                "mode": "server_llm",
                "tool": "crawl_detail_batch_internal_llm",
                "when": "Use server-side LLM batch detail crawl/import path.",
            },
        ]
    return [
        {
            "mode": "entrypoint",
            "tool": "analyze",
            "when": "Re-run analyze with explicit page_type_hint=index|detail if detection is uncertain.",
        }
    ]


def _index_review_response(
    *,
    candidates: List[Dict[str, Any]],
    decision_reason: str,
    browser_provider: str,
    client_id: Optional[str],
    strict_client: bool,
) -> dict:
    try:
        metadata = _resolve_provider_metadata_for_response(
            browser_provider=browser_provider,
            client_id=client_id,
            strict_client=strict_client,
        )
    except RuntimeError:
        metadata = {
            "resolved_browser_provider": "server",
            "client_id_used": None,
        }

    payload = {
        "page_type": "index",
        "auto_ready": False,
        "requires_user_review": True,
        "decision_reason": decision_reason,
        "candidates": candidates,
        "selected_count": 0,
        "requires_user_input": False,
        "next_action_hint": "Review candidates and confirm which program links to crawl.",
    }
    payload.update(metadata)
    payload.update(_client_id_usage_metadata(client_id=client_id))
    return payload


async def _mcp_analyze_impl(
    *,
    url: str,
    page_type_hint: str = "auto",
    browser_provider: str = "auto",
    client_id: Optional[str] = None,
    strict_client: bool = False,
    html_content: Optional[str] = None,
    use_internal_llm: bool,
) -> dict:
    normalized_hint = _normalize_page_type_hint(page_type_hint)
    result = await analyze_url_candidates(
        url=url,
        page_type_hint=normalized_hint,
        html_content=html_content,
        browser_provider=browser_provider,
        client_id=client_id,
        strict_client=strict_client,
        use_internal_llm=use_internal_llm,
    )
    response = dict(result or {})
    if "resolved_browser_provider" not in response:
        metadata = _resolve_provider_metadata_for_response(
            browser_provider=browser_provider,
            client_id=client_id,
            strict_client=strict_client,
        )
        response.update(metadata)
    if "client_id_used" not in response:
        response["client_id_used"] = None
    response.setdefault("analysis_mode", "internal_llm" if use_internal_llm else "external_llm")
    response["page_type_hint_applied"] = normalized_hint
    detected_page_type = str(response.get("page_type") or "unknown").strip().lower()
    response["page_type_detected"] = detected_page_type if detected_page_type in {"index", "detail"} else "unknown"
    response["requires_user_confirmation"] = normalized_hint == "auto"
    if response["requires_user_confirmation"] and response["page_type_detected"] in {"index", "detail"}:
        response["confirmation_prompt"] = (
            f"检测为 {response['page_type_detected']}，是否按 {response['page_type_detected']} 流程继续？"
        )
    else:
        response["confirmation_prompt"] = ""
    response["next_step_options"] = _analyze_next_step_options(page_type=response["page_type_detected"])
    response["next_action_hint"] = (
        "Review next_step_options and choose one tool path."
    )
    response.update(_client_id_usage_metadata(client_id=client_id))
    return response


async def _mcp_crawl_impl(
    *,
    url: str,
    univ_slug: str,
    year: Optional[int] = None,
    continue_depth: int = 0,
    page_type_hint: str = "auto",
    browser_provider: str = "auto",
    client_id: Optional[str] = None,
    strict_client: bool = False,
    candidate_taxonomy_filter_enabled: bool = False,
    candidate_taxonomy_filter_threshold: float = 0.75,
    candidate_taxonomy_filter_top_k: int = 30,
    use_internal_llm: bool,
) -> dict:
    valid_year = _to_valid_year(year)
    if valid_year is None:
        payload = {
            "requires_user_input": True,
            "missing_fields": ["year"],
            "prompt": "请确认落库年份（如 2026）",
            "next_action_hint": "Provide year before running crawl.",
        }
        payload.update(_client_id_usage_metadata(client_id=client_id))
        return payload

    normalized_hint = _normalize_page_type_hint(page_type_hint)
    analysis_result: dict[str, Any] = {}
    page_type = normalized_hint if normalized_hint in {"index", "detail"} else "unknown"

    if normalized_hint != "detail":
        try:
            analysis_result = await analyze_url_candidates(
                url=url,
                page_type_hint=normalized_hint,
                browser_provider=browser_provider,
                client_id=client_id,
                strict_client=strict_client,
                use_internal_llm=use_internal_llm,
            )
            if normalized_hint == "auto":
                page_type = str(analysis_result.get("page_type") or "unknown").strip().lower()
        except Exception as exc:  # pylint: disable=broad-except
            logger.info("Pre-crawl analyze unavailable for %s: %s", url, exc)

    if page_type == "index":
        raw_links = [
            item
            for item in (analysis_result.get("links") or [])
            if isinstance(item, dict)
        ]
        ranked_candidates = _rank_index_candidates_by_taxonomy(
            links=raw_links,
            keep_threshold=max(0.0, min(1.0, float(candidate_taxonomy_filter_threshold))),
            auto_run_threshold=0.92,
            top_k=max(1, int(candidate_taxonomy_filter_top_k)),
        )
        if not ranked_candidates:
            response = _index_review_response(
                candidates=[],
                decision_reason="no_candidates_above_keep_threshold",
                browser_provider=browser_provider,
                client_id=client_id,
                strict_client=strict_client,
            )
            response["analysis_mode"] = "internal_llm" if use_internal_llm else "external_llm"
            response["page_type_hint_applied"] = normalized_hint
            return response

        if len(ranked_candidates) > 10:
            response = _index_review_response(
                candidates=ranked_candidates,
                decision_reason="candidate_count_exceeds_auto_limit",
                browser_provider=browser_provider,
                client_id=client_id,
                strict_client=strict_client,
            )
            response["analysis_mode"] = "internal_llm" if use_internal_llm else "external_llm"
            response["page_type_hint_applied"] = normalized_hint
            return response

        def _auto_run_eligible(row: Dict[str, Any]) -> bool:
            if "auto_run_eligible" in row:
                return bool(row.get("auto_run_eligible"))
            try:
                return float(row.get("taxonomy_score") or 0.0) >= 0.92
            except (TypeError, ValueError):
                return False

        if not all(_auto_run_eligible(row) for row in ranked_candidates):
            response = _index_review_response(
                candidates=ranked_candidates,
                decision_reason="confidence_below_auto_threshold",
                browser_provider=browser_provider,
                client_id=client_id,
                strict_client=strict_client,
            )
            response["analysis_mode"] = "internal_llm" if use_internal_llm else "external_llm"
            response["page_type_hint_applied"] = normalized_hint
            return response

        selected_urls = [str(row.get("url") or "").strip() for row in ranked_candidates]
        selected_urls = [item for item in selected_urls if item]
        selected_link_texts = {
            str(row.get("url") or "").strip(): str(row.get("text") or "").strip()
            for row in ranked_candidates
            if str(row.get("url") or "").strip() and str(row.get("text") or "").strip()
        }
        result = await crawl_url(
            url=url,
            univ_slug=univ_slug,
            year=valid_year,
            continue_depth=continue_depth,
            page_type_hint="index",
            selected_urls=selected_urls,
            selected_link_texts=selected_link_texts,
            browser_provider=browser_provider,
            client_id=client_id,
            strict_client=strict_client,
            candidate_taxonomy_filter_enabled=candidate_taxonomy_filter_enabled,
            candidate_taxonomy_filter_threshold=candidate_taxonomy_filter_threshold,
            candidate_taxonomy_filter_top_k=candidate_taxonomy_filter_top_k,
        )
        response = result.model_dump()
        response.update(
            {
                "page_type": "index",
                "auto_ready": True,
                "requires_user_review": False,
                "decision_reason": "auto_crawl_threshold_met",
                "candidates": ranked_candidates,
                "selected_count": len(selected_urls),
            }
        )
        if "client_id_used" not in response:
            response["client_id_used"] = None
        response["analysis_mode"] = "internal_llm" if use_internal_llm else "external_llm"
        response["page_type_hint_applied"] = normalized_hint
        response.update(_client_id_usage_metadata(client_id=client_id))
        return response

    result = await crawl_url(
        url=url,
        univ_slug=univ_slug,
        year=valid_year,
        continue_depth=continue_depth,
        page_type_hint=normalized_hint,
        browser_provider=browser_provider,
        client_id=client_id,
        strict_client=strict_client,
        candidate_taxonomy_filter_enabled=candidate_taxonomy_filter_enabled,
        candidate_taxonomy_filter_threshold=candidate_taxonomy_filter_threshold,
        candidate_taxonomy_filter_top_k=candidate_taxonomy_filter_top_k,
    )
    response = result.model_dump()
    if "resolved_browser_provider" not in response:
        metadata = _resolve_provider_metadata_for_response(
            browser_provider=browser_provider,
            client_id=client_id,
            strict_client=strict_client,
        )
        response.update(metadata)
    if "client_id_used" not in response:
        response["client_id_used"] = None
    response.setdefault("page_type", page_type if page_type in {"detail", "index"} else "detail")
    response.setdefault("auto_ready", True)
    response.setdefault("requires_user_review", False)
    response.setdefault("decision_reason", "direct_crawl")
    response["analysis_mode"] = "internal_llm" if use_internal_llm else "external_llm"
    response["page_type_hint_applied"] = normalized_hint
    response.update(_client_id_usage_metadata(client_id=client_id))
    return response


async def _mcp_ingest_impl(
    *,
    univ_slug: str,
    year: int,
    programs: List[Dict[str, Any]],
    use_internal_llm: bool,
) -> dict:
    try:
        payload = ingest_program_records_external(
            univ_slug=univ_slug,
            year=year,
            programs=list(programs or []),
        )
    except ValueError as exc:
        return {
            "imported_count": 0,
            "updated_count": 0,
            "total_submitted": len(programs or []),
            "failed_items": [
                {
                    "index": None,
                    "error_code": "invalid_input",
                    "message": str(exc),
                }
            ],
            "review_token": uuid.uuid4().hex,
            "review_items": [],
            "summary": str(exc),
            "analysis_mode": "internal_llm" if use_internal_llm else "external_llm",
            "ingest_mode": "internal_llm" if use_internal_llm else "external_llm",
        }

    response = dict(payload or {})
    response["analysis_mode"] = "internal_llm" if use_internal_llm else "external_llm"
    response["ingest_mode"] = "internal_llm" if use_internal_llm else "external_llm"
    response.setdefault(
        "next_action_hint",
        "Use db_query to inspect persisted rows, then apply program_patch if corrections are needed.",
    )
    return response


try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.server import TransportSecuritySettings

    # Allow external hosts (like Cloudflare Tunnel) by disabling DNS rebinding protection
    # since this service is explicitly intended to be exposed.
    mcp = FastMCP(
        "UniAdmission Agent",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool(name="analyze")
    async def mcp_analyze(
        url: str,
        page_type_hint: str = "auto",
        browser_provider: str = "auto",
        client_id: Optional[str] = None,
        strict_client: bool = False,
        html_content: Optional[str] = None,
    ) -> dict:
        """Analyze entry page and return candidate detail links for user selection.

        Designed for interactive MCP workflows:
        1) Detect whether entry is index/detail
        2) Return candidate links (if index)
        3) Let user decide which links to crawl next

        Notes:
        - This non-suffixed tool uses external-LLM friendly analysis path.
        - ``client_id`` is a browser client identifier (from ``runtime_status.client_ids``),
          not a university slug.
        """
        return await _mcp_analyze_impl(
            url=url,
            page_type_hint=page_type_hint,
            browser_provider=browser_provider,
            client_id=client_id,
            strict_client=strict_client,
            html_content=html_content,
            use_internal_llm=False,
        )

    @mcp.tool(name="crawl_detail_batch")
    async def mcp_crawl_detail_batch(
        index_url: str,
        selected_urls: List[str],
        univ_slug: str,
        year: int,
        batch_size: int = 4,
        client_id: Optional[str] = None,
        strict_client: bool = True,
        selected_link_texts: Optional[Dict[str, str]] = None,
    ) -> dict:
        """Fetch selected detail pages via client browser and crawl in batches.

        Typical usage:
        - Call ``mcp_analyze`` first to get ``links``
        - Ask user to choose subset (all/top N/bottom N/manual)
        - Call this tool with chosen URLs for batched detail crawling

        Notes:
        - ``client_id`` is an optional browser client id from ``runtime_status.client_ids``.
        - ``univ_slug`` is the university identifier (e.g. polyu/edinburgh).
        """
        result = await crawl_selected_detail_urls_via_client(
            index_url=index_url,
            selected_urls=selected_urls,
            univ_slug=univ_slug,
            year=year,
            batch_size=batch_size,
            client_id=client_id,
            strict_client=strict_client,
            selected_link_texts=selected_link_texts,
        )
        return dict(result)

    @mcp.tool(name="crawl")
    async def mcp_crawl(
        url: str,
        univ_slug: str,
        year: Optional[int] = None,
        continue_depth: int = 0,
        page_type_hint: str = "auto",
        browser_provider: str = "auto",
        client_id: Optional[str] = None,
        strict_client: bool = False,
        candidate_taxonomy_filter_enabled: bool = False,
        candidate_taxonomy_filter_threshold: float = 0.75,
        candidate_taxonomy_filter_top_k: int = 30,
    ) -> dict:
        """Crawl a university admission page and import structured data.

        Fetches and imports data for one URL.

        Args:
            url: Starting URL to crawl (e.g. https://admissions.hku.hk/programmes).
            univ_slug: University identifier (e.g. "hku").
            year: Academic year (e.g. 2026).
            continue_depth: Extra depth levels for LLM-driven link scouting.
            page_type_hint: Optional manual override: auto/index/detail.
            client_id: Browser client id from runtime_status.client_ids.

        Returns:
            Dict with imported_count, univ_slug, and year.
        """
        return await _mcp_crawl_impl(
            url=url,
            univ_slug=univ_slug,
            year=year,
            continue_depth=continue_depth,
            page_type_hint=page_type_hint,
            browser_provider=browser_provider,
            client_id=client_id,
            strict_client=strict_client,
            candidate_taxonomy_filter_enabled=candidate_taxonomy_filter_enabled,
            candidate_taxonomy_filter_threshold=candidate_taxonomy_filter_threshold,
            candidate_taxonomy_filter_top_k=candidate_taxonomy_filter_top_k,
            use_internal_llm=False,
        )

    @mcp.tool(name="ingest")
    async def mcp_ingest(
        univ_slug: str,
        year: int,
        programs: List[Dict[str, Any]],
    ) -> dict:
        """Persist externally structured program records (no internal LLM extraction).

        Intended for caller-side LLM workflows:
        1) Caller fetches/parses pages on its side
        2) Caller sends normalized `programs` JSON list
        3) Server validates + upserts and returns review metadata
        """
        return await _mcp_ingest_impl(
            univ_slug=univ_slug,
            year=year,
            programs=programs,
            use_internal_llm=False,
        )

    @mcp.tool(name="db_query")
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

    @mcp.tool(name="runtime_status")
    def mcp_runtime_status() -> dict:
        """Report runtime availability for clients and internal LLM."""
        return _runtime_status_payload()

    @mcp.tool(name="program_patch")
    def mcp_program_patch(program_id: int, patch: Dict[str, Any]) -> dict:
        """Patch a single program by ID."""
        normalized_program_id = int(program_id)
        patch_payload = dict(patch or {})
        if not patch_payload:
            return {
                "updated": False,
                "program_id": normalized_program_id,
                "error_code": "empty_patch",
                "next_action_hint": "Provide at least one field to update.",
            }

        blocked_fields = {"id", "university_id", "program_catalog_id", "academic_year"}
        blocked = sorted(blocked_fields.intersection(set(patch_payload.keys())))
        if blocked:
            return {
                "updated": False,
                "program_id": normalized_program_id,
                "error_code": "forbidden_fields",
                "failed_fields": blocked,
                "next_action_hint": "Remove immutable fields from patch payload.",
            }

        try:
            updated = patch_program_snapshot(normalized_program_id, patch_payload)
        except ValueError as exc:
            return {
                "updated": False,
                "program_id": normalized_program_id,
                "error_code": "validation_error",
                "message": str(exc),
                "next_action_hint": "Fix patch payload and retry.",
            }

        if not updated:
            return {
                "updated": False,
                "program_id": normalized_program_id,
                "error_code": "not_found",
                "next_action_hint": "Check program_id from review_items and retry.",
            }

        return {
            "updated": True,
            "program_id": normalized_program_id,
            "program": updated.model_dump(),
            "summary": "updated 1 record",
        }

    @mcp.tool(name="program_patch_batch")
    def mcp_program_patch_batch(items: List[Dict[str, Any]]) -> dict:
        """Patch multiple programs by ID; per-item failures do not abort the batch."""
        normalized_items = [item for item in (items or []) if isinstance(item, dict)]
        if not normalized_items:
            return {
                "updated_count": 0,
                "failed_items": [],
                "summary": "No patch items supplied.",
                "error_code": "empty_batch",
            }

        updated_count = 0
        failed_items: List[Dict[str, Any]] = []
        updated_program_ids: List[int] = []
        for idx, item in enumerate(normalized_items):
            raw_program_id = item.get("program_id")
            patch_payload = dict(item.get("patch") or {})

            try:
                program_id = int(raw_program_id)
            except (TypeError, ValueError):
                failed_items.append(
                    {
                        "index": idx,
                        "program_id": raw_program_id,
                        "error_code": "invalid_program_id",
                        "message": "program_id must be an integer",
                    }
                )
                continue

            if not patch_payload:
                failed_items.append(
                    {
                        "index": idx,
                        "program_id": program_id,
                        "error_code": "empty_patch",
                        "message": "patch payload is empty",
                    }
                )
                continue

            blocked_fields = {"id", "university_id", "program_catalog_id", "academic_year"}
            blocked = sorted(blocked_fields.intersection(set(patch_payload.keys())))
            if blocked:
                failed_items.append(
                    {
                        "index": idx,
                        "program_id": program_id,
                        "error_code": "forbidden_fields",
                        "failed_fields": blocked,
                    }
                )
                continue

            try:
                updated = patch_program_snapshot(program_id, patch_payload)
            except ValueError as exc:
                failed_items.append(
                    {
                        "index": idx,
                        "program_id": program_id,
                        "error_code": "validation_error",
                        "message": str(exc),
                    }
                )
                continue
            except Exception as exc:  # pylint: disable=broad-except
                failed_items.append(
                    {
                        "index": idx,
                        "program_id": program_id,
                        "error_code": "unexpected_error",
                        "message": str(exc),
                    }
                )
                continue

            if not updated:
                failed_items.append(
                    {
                        "index": idx,
                        "program_id": program_id,
                        "error_code": "not_found",
                    }
                )
                continue

            updated_count += 1
            updated_program_ids.append(program_id)

        summary = (
            f"Updated {updated_count}/{len(normalized_items)} items; "
            f"failed {len(failed_items)}."
        )
        return {
            "updated_count": updated_count,
            "updated_program_ids": updated_program_ids,
            "failed_items": failed_items,
            "summary": summary,
        }

    @mcp.tool(name="help")
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

    async def mcp_agent_run(
        url: str,
        univ_slug: str,
        year: int,
        page_type_hint: str = "auto",
        runtime: Optional[str] = None,
        policy_profile: Optional[Dict[str, Any]] = None,
        client_id: Optional[str] = None,
        autonomous: bool = False,
    ) -> dict:
        """Run one agent orchestration request when agent mode is enabled.

        By default (autonomous=False), orchestration is driven by the calling
        LLM: index pages return candidate lists for external review instead
        of auto-crawling.  Set autonomous=True to let the internal runtime
        make all decisions autonomously.

        A ``task_id`` is included in the response so callers can subscribe to
        ``GET /tasks/{task_id}/events`` for real-time streaming progress.
        """
        task_id = task_manager.create_task(
            params={
                "mode": "agent",
                "url": url,
                "univ_slug": univ_slug,
                "year": year,
                "page_type_hint": page_type_hint,
            }
        )

        def _event_sink(event: dict[str, Any]) -> None:
            task_manager.add_event(task_id, event)

        task_manager.update_task(
            task_id,
            state=TaskState.RUNNING,
            progress="Agent running…",
            progress_percent=3.0,
        )
        try:
            result = await run_agent_crawl(
                url=url,
                univ_slug=univ_slug,
                year=year,
                page_type_hint=page_type_hint,
                runtime_mode=runtime,
                policy_profile=policy_profile,
                client_id=client_id,
                autonomous=autonomous,
                event_sink=_event_sink,
            )
            task_manager.update_task(
                task_id,
                state=TaskState.DONE,
                progress="Complete",
                result=result,
                progress_percent=100.0,
            )
        except Exception as exc:
            task_manager.update_task(
                task_id,
                state=TaskState.FAILED,
                error=str(exc),
                progress_percent=100.0,
            )
            raise
        return {**result, "task_id": task_id}

    async def mcp_agent_review_confirm(
        task_id: str,
        selection_text: str = "",
        selected_indices: Optional[List[int]] = None,
    ) -> dict:
        """Confirm selected onhold indices for one agent task."""
        response = await api_agent_review_confirm(
            AgentReviewConfirmRequest(
                task_id=task_id,
                selection_text=selection_text,
                selected_indices=selected_indices,
            )
        )
        return response.model_dump(mode="json")

    def _register_agent_mcp_tools_impl() -> None:
        """Register agent MCP tools lazily based on current feature flag state."""
        if _agent_mcp_tools_state["registered"]:
            return
        if not is_agent_enabled():
            logger.info("MCP agent tools not registered (agent runtime disabled).")
            return

        mcp.tool(name="agent_run")(mcp_agent_run)
        mcp.tool(name="agent_review_confirm")(mcp_agent_review_confirm)
        _agent_mcp_tools_state["registered"] = True

    _register_agent_mcp_tools_if_enabled = _register_agent_mcp_tools_impl
    _register_agent_mcp_tools_if_enabled()

    if _internal_llm_available():
        @mcp.tool(name="analyze_internal_llm")
        async def mcp_analyze_internal_llm(
            url: str,
            page_type_hint: str = "auto",
            browser_provider: str = "auto",
            client_id: Optional[str] = None,
            strict_client: bool = False,
            html_content: Optional[str] = None,
        ) -> dict:
            """Analyze page using the explicit internal-LLM toolset path."""
            return await _mcp_analyze_impl(
                url=url,
                page_type_hint=page_type_hint,
                browser_provider=browser_provider,
                client_id=client_id,
                strict_client=strict_client,
                html_content=html_content,
                use_internal_llm=True,
            )

        @mcp.tool(name="crawl_detail_batch_internal_llm")
        async def mcp_crawl_detail_batch_internal_llm(
            index_url: str,
            selected_urls: List[str],
            univ_slug: str,
            year: int,
            batch_size: int = 4,
            client_id: Optional[str] = None,
            strict_client: bool = True,
            selected_link_texts: Optional[Dict[str, str]] = None,
        ) -> dict:
            """Batch detail crawl using explicit internal-LLM toolset path."""
            return await mcp_crawl_detail_batch(
                index_url=index_url,
                selected_urls=selected_urls,
                univ_slug=univ_slug,
                year=year,
                batch_size=batch_size,
                client_id=client_id,
                strict_client=strict_client,
                selected_link_texts=selected_link_texts,
            )

        @mcp.tool(name="crawl_internal_llm")
        async def mcp_crawl_internal_llm(
            url: str,
            univ_slug: str,
            year: Optional[int] = None,
            continue_depth: int = 0,
            page_type_hint: str = "auto",
            browser_provider: str = "auto",
            client_id: Optional[str] = None,
            strict_client: bool = False,
            candidate_taxonomy_filter_enabled: bool = False,
            candidate_taxonomy_filter_threshold: float = 0.75,
            candidate_taxonomy_filter_top_k: int = 30,
        ) -> dict:
            """Crawl using explicit internal-LLM toolset path."""
            return await _mcp_crawl_impl(
                url=url,
                univ_slug=univ_slug,
                year=year,
                continue_depth=continue_depth,
                page_type_hint=page_type_hint,
                browser_provider=browser_provider,
                client_id=client_id,
                strict_client=strict_client,
                candidate_taxonomy_filter_enabled=candidate_taxonomy_filter_enabled,
                candidate_taxonomy_filter_threshold=candidate_taxonomy_filter_threshold,
                candidate_taxonomy_filter_top_k=candidate_taxonomy_filter_top_k,
                use_internal_llm=True,
            )

        @mcp.tool(name="ingest_internal_llm")
        async def mcp_ingest_internal_llm(
            univ_slug: str,
            year: int,
            programs: List[Dict[str, Any]],
        ) -> dict:
            """Persist caller-provided structured records via internal-LLM namespaced tool."""
            return await _mcp_ingest_impl(
                univ_slug=univ_slug,
                year=year,
                programs=programs,
                use_internal_llm=True,
            )

        @mcp.tool(name="db_query_internal_llm")
        def mcp_db_query_internal_llm(
            univ_slug: str,
            year: Optional[int] = None,
        ) -> list:
            """Internal-LLM namespaced alias of db_query."""
            return mcp_db_query(univ_slug=univ_slug, year=year)

        @mcp.tool(name="runtime_status_internal_llm")
        def mcp_runtime_status_internal_llm() -> dict:
            """Internal-LLM namespaced alias of runtime_status."""
            return mcp_runtime_status()

        @mcp.tool(name="program_patch_internal_llm")
        def mcp_program_patch_internal_llm(program_id: int, patch: Dict[str, Any]) -> dict:
            """Internal-LLM namespaced alias of program_patch."""
            return mcp_program_patch(program_id=program_id, patch=patch)

        @mcp.tool(name="program_patch_batch_internal_llm")
        def mcp_program_patch_batch_internal_llm(items: List[Dict[str, Any]]) -> dict:
            """Internal-LLM namespaced alias of program_patch_batch."""
            return mcp_program_patch_batch(items=items)

        @mcp.tool(name="help_internal_llm")
        def mcp_help_internal_llm(verbose: bool = False) -> dict:
            """Internal-LLM namespaced alias of help."""
            return mcp_help(verbose=verbose)
    else:
        logger.info("MCP internal_llm tools not registered (internal LLM unavailable).")

    # Mount MCP as a sub-application at /mcp
    app.mount("/mcp", mcp.sse_app())
    logger.info(
        "MCP tools registered: analyze, crawl_detail_batch, crawl, ingest, db_query, "
        "runtime_status, program_patch, program_patch_batch, help"
    )

except ImportError:
    logger.info("MCP SDK not installed — MCP tools disabled. Install with: uv add 'mcp[cli]'")
