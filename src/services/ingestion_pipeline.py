from __future__ import annotations
# pylint: disable=too-many-lines

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlmodel import col, select

from src.agents.cleaner_agent import LLMCleanerAgent
from src.models.ingestion import (
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    IngestionTask,
    IngestionTaskState,
)
from src.models.scraper_models import CrawlPageResult
from src.scrapers.helpers import build_url_name_signal, extract_program_name, is_noise_program_name
from src.scrapers.engine import AdmissionScraper
from src.scrapers.link_parser import extract_links_with_text, filter_links_by_llm
from src.scrapers.page_processor import extract_program_data_from_page
from src.scrapers.scout import run_scout
from src.services.program_name_resolution import resolve_program_name
from src.services.quality_gate import evaluate_extraction
from src.services.subject_taxonomy import get_subject_taxonomy_service, normalize_name as normalize_taxonomy_name
from src.storage.db_manager import DatabaseManager
from src.utils.text import generate_program_group_code

logger = logging.getLogger(__name__)
_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

STAGE_ORDER = [
    IngestionStage.FETCH_RAW,
    IngestionStage.EXTRACT_STRUCTURED,
    IngestionStage.VALIDATE_RULES,
    IngestionStage.PERSIST_VERSIONED,
]

DEFAULT_STAGE_MAX_RETRIES = 2
MAX_RETRY_BACKOFF_SECONDS = 30
IngestionEventCallback = Callable[[str, Dict[str, Any]], None]

STAGE_CONTEXT_KEYS = {
    IngestionStage.FETCH_RAW: (
        "raw_pages",
        "raw_page_count",
        "failed_urls",
        "scouted_links",
        "scout_call_count",
        "source_content_hash",
        "program_candidates",
        "extracted_count",
        "extract_errors",
        "unresolved_urls",
        "candidate_hash",
        "validated_programs",
        "validated_count",
        "rejected_programs",
        "validated_hash",
        "persisted_count",
        "created_count",
        "updated_count",
        "persisted_hash",
    ),
    IngestionStage.EXTRACT_STRUCTURED: (
        "program_candidates",
        "extracted_count",
        "extract_errors",
        "unresolved_urls",
        "candidate_hash",
        "validated_programs",
        "validated_count",
        "rejected_programs",
        "validated_hash",
        "persisted_count",
        "created_count",
        "updated_count",
        "persisted_hash",
    ),
    IngestionStage.VALIDATE_RULES: (
        "validated_programs",
        "validated_count",
        "rejected_programs",
        "validated_hash",
        "persisted_count",
        "created_count",
        "updated_count",
        "persisted_hash",
    ),
    IngestionStage.PERSIST_VERSIONED: (
        "persisted_count",
        "created_count",
        "updated_count",
        "persisted_hash",
    ),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _json_safe(payload: Any) -> Any:
    return json.loads(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=_json_default)
    )


def _hash_payload(payload: Any) -> str:
    blob = json.dumps(_json_safe(payload), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _stage_order_index(stage: IngestionStage) -> int:
    return STAGE_ORDER.index(stage)


def _extract_html_title(html_text: str) -> str:
    match = _HTML_TITLE_RE.search(str(html_text or ""))
    if not match:
        return ""
    normalized = re.sub(r"\s+", " ", str(match.group(1) or "")).strip()
    if not normalized:
        return ""
    return normalized.split("|", 1)[0].strip()


class StagePoisonedError(RuntimeError):
    """Raised when a stage exceeds retry budget and enters POISONED state."""


class StageExecutionError(RuntimeError):
    """Raised when a stage fails and no retry is possible."""


class ValidatedProgramPayload(BaseModel):
    academic_year: int
    name_en: str
    name_zh: Optional[str] = ""
    faculty: Optional[str] = None
    program_group_code: Optional[str] = None
    tuition_amount: Optional[float] = None
    currency: Optional[str] = None
    study_options: List[Dict[str, Any]] = Field(default_factory=list)
    deadlines: List[Dict[str, Any]] = Field(default_factory=list)
    requirements: List[Dict[str, Any]] = Field(default_factory=list)
    extra_metadata: Dict[str, Any] = Field(default_factory=dict)
    source_url: Optional[str] = None
    is_active: Optional[bool] = None
    is_discontinued: Optional[bool] = None

    @field_validator("name_en")
    @classmethod
    def _name_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("name_en is required")
        return text

    @field_validator("academic_year")
    @classmethod
    def _year_positive(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("academic_year must be positive")
        return int(value)

    @field_validator("study_options", "deadlines", "requirements", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return []


class IngestionPipeline:
    """Queue-like staged ingestion runner backed by DB state."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        stage_max_retries: int = DEFAULT_STAGE_MAX_RETRIES,
    ) -> None:
        self.db_manager = db_manager or DatabaseManager()
        self.stage_max_retries = max(0, int(stage_max_retries))
        self.taxonomy_service = get_subject_taxonomy_service()

    async def run_new_job(
        self,
        *,
        url: str,
        univ_slug: str,
        year: int,
        continue_depth: int = 0,
        page_type_hint: str = "auto",
        export_md: bool = False,
        export_path: Optional[str] = None,
        html_content: Optional[str] = None,
        selected_urls: Optional[List[str]] = None,
        selected_link_texts: Optional[Dict[str, str]] = None,
        browser_automation_enabled: bool = False,
        detail_pages_batch: Optional[List[Dict[str, Any]]] = None,
        batch_index: Optional[int] = None,
        batch_total: Optional[int] = None,
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
        event_callback: Optional[IngestionEventCallback] = None,
    ) -> Dict[str, Any]:
        request_payload = {
            "url": url,
            "univ_slug": univ_slug,
            "year": year,
            "continue_depth": continue_depth,
            "page_type_hint": page_type_hint,
            "export_md": export_md,
            "export_path": export_path,
            "html_content": html_content,
            "selected_urls": selected_urls or [],
            "selected_link_texts": dict(selected_link_texts or {}),
            "browser_automation_enabled": bool(browser_automation_enabled),
            "detail_pages_batch": list(detail_pages_batch or []),
            "batch_index": batch_index,
            "batch_total": batch_total,
            "candidate_taxonomy_filter_enabled": bool(candidate_taxonomy_filter_enabled),
            "candidate_taxonomy_filter_threshold": candidate_taxonomy_filter_threshold,
            "candidate_taxonomy_filter_top_k": candidate_taxonomy_filter_top_k,
            "taxonomy_enabled": taxonomy_enabled,
            "taxonomy_low_threshold": taxonomy_low_threshold,
            "taxonomy_high_threshold": taxonomy_high_threshold,
            "taxonomy_hint_top_k": taxonomy_hint_top_k,
            "taxonomy_override_enabled": taxonomy_override_enabled,
            "name_resolution_llm_enabled": name_resolution_llm_enabled,
            "name_resolution_low_threshold": name_resolution_low_threshold,
            "name_resolution_conflict_delta": name_resolution_conflict_delta,
        }
        job_uid = self._create_job(request_payload)
        return await self._run_job(
            job_uid=job_uid,
            resume_from_stage=None,
            event_callback=event_callback,
        )

    async def resume_job(
        self,
        job_uid: str,
        resume_from_stage: Optional[IngestionStage] = None,
        event_callback: Optional[IngestionEventCallback] = None,
    ) -> Dict[str, Any]:
        stage = resume_from_stage or self._infer_resume_stage(job_uid)
        if stage is None:
            job = self.get_job(job_uid)
            if not job:
                raise ValueError(f"Ingestion job not found: {job_uid}")
            context = job.get("context_payload") or {}
            return {
                "job_uid": job_uid,
                "imported_count": int(context.get("persisted_count") or 0),
                "univ_slug": job.get("univ_slug") or "",
                "year": int(job.get("academic_year") or 0),
                "stage_trace": context.get("stage_trace") or [],
            }

        self._reset_tasks_from_stage(job_uid, stage)
        return await self._run_job(
            job_uid=job_uid,
            resume_from_stage=stage,
            event_callback=event_callback,
        )

    def get_job(self, job_uid: str) -> Optional[Dict[str, Any]]:
        with self.db_manager.get_session() as session:
            job = session.exec(
                select(IngestionJob).where(IngestionJob.job_uid == job_uid)
            ).first()
            if not job:
                return None
            tasks = session.exec(
                select(IngestionTask)
                .where(IngestionTask.job_id == job.id)
                .order_by(col(IngestionTask.id))
            ).all()

            return {
                "job_uid": job.job_uid,
                "status": job.status.value,
                "univ_slug": job.univ_slug,
                "academic_year": job.academic_year,
                "source_url": job.source_url,
                "current_stage": job.current_stage.value if job.current_stage else None,
                "resume_from_stage": (
                    job.resume_from_stage.value if job.resume_from_stage else None
                ),
                "error_message": job.error_message,
                "request_payload": _json_safe(job.request_payload or {}),
                "context_payload": _json_safe(job.context_payload or {}),
                "tasks": [
                    {
                        "stage": t.stage.value,
                        "state": t.state.value,
                        "attempt_count": t.attempt_count,
                        "max_retries": t.max_retries,
                        "idempotency_key": t.idempotency_key,
                        "error_message": t.error_message,
                        "next_retry_at": (
                            t.next_retry_at.isoformat() if t.next_retry_at else None
                        ),
                    }
                    for t in tasks
                ],
            }

    def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        capped_limit = max(1, min(200, int(limit)))
        with self.db_manager.get_session() as session:
            jobs = session.exec(
                select(IngestionJob)
                .order_by(col(IngestionJob.created_at).desc())
                .limit(capped_limit)
            ).all()

            return [
                {
                    "job_uid": job.job_uid,
                    "status": job.status.value,
                    "univ_slug": job.univ_slug,
                    "academic_year": job.academic_year,
                    "source_url": job.source_url,
                    "current_stage": job.current_stage.value if job.current_stage else None,
                    "error_message": job.error_message,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                    "updated_at": job.updated_at.isoformat() if job.updated_at else None,
                }
                for job in jobs
            ]

    async def _run_job(
        self,
        *,
        job_uid: str,
        resume_from_stage: Optional[IngestionStage],
        event_callback: Optional[IngestionEventCallback],
    ) -> Dict[str, Any]:
        job = self._require_job(job_uid)
        request_payload = _json_safe(job.request_payload or {})
        context = _json_safe(job.context_payload or {})

        if resume_from_stage:
            context.setdefault("stage_trace", [])
        start_stage = resume_from_stage or self._infer_resume_stage(job_uid)

        if start_stage is None:
            return {
                "job_uid": job_uid,
                "imported_count": int(context.get("persisted_count") or 0),
                "univ_slug": request_payload.get("univ_slug") or "",
                "year": int(request_payload.get("year") or 0),
                "persisted_program_ids": context.get("persisted_program_ids") or [],
                "stage_trace": context.get("stage_trace") or [],
            }

        self._mark_job_running(job_uid, start_stage)
        self._emit_event(
            event_callback,
            "job_started",
            {
                "job_uid": job_uid,
                "resume_from_stage": start_stage.value,
            },
        )

        try:
            for stage in STAGE_ORDER:
                if _stage_order_index(stage) < _stage_order_index(start_stage):
                    continue
                self._emit_event(
                    event_callback,
                    "stage_started",
                    {
                        "job_uid": job_uid,
                        "stage": stage.value,
                    },
                )
                stage_output = await self._run_stage(
                    job_uid=job_uid,
                    stage=stage,
                    request_payload=request_payload,
                    context=context,
                    event_callback=event_callback,
                )
                context.update(stage_output)
                self._mark_job_stage_success(job_uid, stage, context)
                self._emit_event(
                    event_callback,
                    "stage_succeeded",
                    {
                        "job_uid": job_uid,
                        "stage": stage.value,
                    },
                )

            self._mark_job_succeeded(job_uid, context)
            self._emit_event(
                event_callback,
                "job_succeeded",
                {
                    "job_uid": job_uid,
                    "imported_count": int(context.get("persisted_count") or 0),
                },
            )
            return {
                "job_uid": job_uid,
                "imported_count": int(context.get("persisted_count") or 0),
                "univ_slug": request_payload.get("univ_slug") or "",
                "year": int(request_payload.get("year") or 0),
                "persisted_program_ids": context.get("persisted_program_ids") or [],
                "stage_trace": context.get("stage_trace") or [],
            }
        except StagePoisonedError as exc:
            self._mark_job_terminal_error(job_uid, str(exc), IngestionJobStatus.POISONED)
            self._emit_event(
                event_callback,
                "job_poisoned",
                {
                    "job_uid": job_uid,
                    "error": str(exc),
                },
            )
            raise
        except Exception as exc:
            self._mark_job_terminal_error(job_uid, str(exc), IngestionJobStatus.FAILED)
            self._emit_event(
                event_callback,
                "job_failed",
                {
                    "job_uid": job_uid,
                    "error": str(exc),
                },
            )
            raise

    async def _run_stage(
        self,
        *,
        job_uid: str,
        stage: IngestionStage,
        request_payload: Dict[str, Any],
        context: Dict[str, Any],
        event_callback: Optional[IngestionEventCallback],
    ) -> Dict[str, Any]:
        stage_input = self._build_stage_input(stage, request_payload, context)
        idempotency_key = self._build_idempotency_key(stage, request_payload, stage_input)

        while True:
            task, can_skip = self._prepare_stage_task(
                job_uid=job_uid,
                stage=stage,
                stage_input=stage_input,
                idempotency_key=idempotency_key,
            )
            if can_skip:
                logger.info("[Ingestion:%s] stage %s skipped via idempotency", job_uid, stage.value)
                self._emit_event(
                    event_callback,
                    "stage_skipped",
                    {
                        "job_uid": job_uid,
                        "stage": stage.value,
                    },
                )
                return _json_safe(task.output_payload or {})

            attempt_no = int(task.attempt_count or 0) + 1
            self._mark_task_running(task.id, attempt_no)
            logger.info(
                "[Ingestion:%s] stage %s attempt %d",
                job_uid,
                stage.value,
                attempt_no,
            )
            self._emit_event(
                event_callback,
                "stage_attempt_started",
                {
                    "job_uid": job_uid,
                    "stage": stage.value,
                    "attempt": attempt_no,
                },
            )

            try:
                stage_output = await self._execute_stage(
                    stage,
                    request_payload,
                    context,
                    event_callback=event_callback,
                )
                self._mark_task_success(task.id, stage_output)
                self._append_stage_trace(context, stage, "SUCCEEDED", attempt_no)
                return _json_safe(stage_output)
            except Exception as exc:
                failure = self._mark_task_failure(task.id, str(exc))
                self._append_stage_trace(
                    context,
                    stage,
                    "FAILED",
                    int(failure.get("attempt_count") or attempt_no),
                    str(exc),
                )
                if failure["state"] == IngestionTaskState.POISONED.value:
                    self._emit_event(
                        event_callback,
                        "stage_poisoned",
                        {
                            "job_uid": job_uid,
                            "stage": stage.value,
                            "attempt": int(failure.get("attempt_count") or attempt_no),
                            "error": str(exc),
                        },
                    )
                    raise StagePoisonedError(
                        f"Stage {stage.value} poisoned after retries: {exc}"
                    ) from exc

                backoff_seconds = int(failure.get("backoff_seconds") or 1)
                logger.warning(
                    "[Ingestion:%s] stage %s failed, retry in %ds: %s",
                    job_uid,
                    stage.value,
                    backoff_seconds,
                    exc,
                )
                self._emit_event(
                    event_callback,
                    "stage_retry_scheduled",
                    {
                        "job_uid": job_uid,
                        "stage": stage.value,
                        "attempt": int(failure.get("attempt_count") or attempt_no),
                        "backoff_seconds": backoff_seconds,
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(backoff_seconds)

    async def _execute_stage(
        self,
        stage: IngestionStage,
        request_payload: Dict[str, Any],
        context: Dict[str, Any],
        event_callback: Optional[IngestionEventCallback] = None,
    ) -> Dict[str, Any]:
        if stage == IngestionStage.FETCH_RAW:
            return await self._stage_fetch_raw(
                request_payload,
                event_callback=event_callback,
            )
        if stage == IngestionStage.EXTRACT_STRUCTURED:
            return await asyncio.to_thread(
                self._stage_extract_structured,
                request_payload,
                context,
            )
        if stage == IngestionStage.VALIDATE_RULES:
            return await asyncio.to_thread(
                self._stage_validate_rules,
                request_payload,
                context,
            )
        if stage == IngestionStage.PERSIST_VERSIONED:
            return await asyncio.to_thread(
                self._stage_persist_versioned,
                request_payload,
                context,
            )
        raise StageExecutionError(f"Unsupported ingestion stage: {stage}")

    async def _stage_fetch_raw(
        self,
        request_payload: Dict[str, Any],
        event_callback: Optional[IngestionEventCallback] = None,
    ) -> Dict[str, Any]:
        scraper = AdmissionScraper()
        scraper._reset_session_state()

        export_md = bool(request_payload.get("export_md"))
        export_path = request_payload.get("export_path")
        scraper._export_md = export_md
        scraper._export_path = export_path

        url = str(request_payload.get("url") or "").strip()
        if not url:
            raise ValueError("url is required for fetch_raw stage")

        continue_depth = max(0, int(request_payload.get("continue_depth") or 0))
        selected_urls = [u for u in (request_payload.get("selected_urls") or []) if u]
        detail_pages_batch = [
            item
            for item in (request_payload.get("detail_pages_batch") or [])
            if isinstance(item, dict)
        ]
        selected_link_texts = {
            str(url).strip(): str(text).strip()
            for url, text in dict(request_payload.get("selected_link_texts") or {}).items()
            if str(url).strip() and str(text).strip()
        }
        html_content = request_payload.get("html_content")
        page_type_hint = str(request_payload.get("page_type_hint") or "auto")
        raw_batch_index = request_payload.get("batch_index")
        raw_batch_total = request_payload.get("batch_total")

        try:
            batch_index = int(raw_batch_index) if raw_batch_index is not None else None
        except (TypeError, ValueError):
            batch_index = None
        try:
            batch_total = int(raw_batch_total) if raw_batch_total is not None else None
        except (TypeError, ValueError):
            batch_total = None
        candidate_taxonomy_filter_enabled = self._coerce_bool(
            request_payload.get("candidate_taxonomy_filter_enabled"),
            default=False,
        )
        candidate_taxonomy_filter_threshold = self._coerce_float(
            request_payload.get("candidate_taxonomy_filter_threshold"),
            default=0.75,
        )
        candidate_taxonomy_filter_threshold = max(
            0.0,
            min(1.0, float(candidate_taxonomy_filter_threshold)),
        )
        candidate_taxonomy_filter_top_k = self._coerce_int(
            request_payload.get("candidate_taxonomy_filter_top_k"),
            default=30,
            minimum=1,
            maximum=200,
        )

        fetched_pages: List[Dict[str, Any]] = []
        failed_urls: List[str] = []
        visited_urls: set[str] = set()
        scout_call_count = 0
        all_scouted_links = []

        scout_candidates: List[CrawlPageResult] = []
        scout_candidate_depth = 0

        def _with_batch_meta(
            payload: Dict[str, Any],
            *,
            source: Optional[str] = None,
        ) -> Dict[str, Any]:
            event_payload = dict(payload)
            if source and not event_payload.get("source"):
                event_payload["source"] = source
            if batch_index is not None:
                event_payload["batch_index"] = batch_index
            if batch_total is not None:
                event_payload["batch_total"] = batch_total
            return event_payload

        def _emit_fetch_event(
            event_type: str,
            payload: Dict[str, Any],
            *,
            source: Optional[str] = None,
        ) -> None:
            self._emit_event(
                event_callback,
                event_type,
                _with_batch_meta(payload, source=source),
            )

        def _build_fetch_result() -> Dict[str, Any]:
            source_content_hash = _hash_payload(
                [
                    {
                        "url": row.get("url"),
                        "markdown_hash": _hash_payload(row.get("markdown") or ""),
                        "html_hash": _hash_payload(row.get("html") or ""),
                    }
                    for row in fetched_pages
                ]
            )
            serialized_scout_links = [
                {
                    "url": str(getattr(link, "url", "") or ""),
                    "reason": str(getattr(link, "reason", "") or ""),
                    "confidence": str(getattr(link, "confidence", "") or ""),
                }
                for link in all_scouted_links
            ]

            return {
                "raw_pages": fetched_pages,
                "raw_page_count": len(fetched_pages),
                "failed_urls": sorted(set(failed_urls)),
                "scouted_links": serialized_scout_links,
                "scout_call_count": scout_call_count,
                "source_content_hash": source_content_hash,
            }

        _emit_fetch_event(
            "fetch_phase",
            {
                "stage": IngestionStage.FETCH_RAW.value,
                "message": "fetch_raw: preparing crawl inputs",
            },
            source="browser_automation" if detail_pages_batch else None,
        )

        def _append_pages(
            pages: List[CrawlPageResult],
            depth: int,
            from_browser: bool,
        ) -> None:
            if not pages:
                return
            fetched_pages.extend(
                self._serialize_pages(
                    pages=pages,
                    depth=depth,
                    from_browser=from_browser,
                    selected_link_texts=selected_link_texts,
                )
            )
            for page in pages:
                visited_urls.add(page.url)

        if detail_pages_batch:
            _emit_fetch_event(
                "fetch_candidates_identified",
                {
                    "stage": IngestionStage.FETCH_RAW.value,
                    "total_candidates": len(detail_pages_batch),
                },
                source="browser_automation",
            )
            browser_pages: List[CrawlPageResult] = []
            total = len(detail_pages_batch)
            for idx, item in enumerate(detail_pages_batch, start=1):
                detail_url = str(item.get("url") or "").strip()
                html_value = item.get("html_content")
                html_text = str(html_value or "")
                anchor_text = str(item.get("selected_anchor_text") or "").strip()
                if detail_url and anchor_text:
                    selected_link_texts[detail_url] = anchor_text

                _emit_fetch_event(
                    "fetch_url_progress",
                    {
                        "stage": IngestionStage.FETCH_RAW.value,
                        "phase": "browser_automation_batch",
                        "status": "started",
                        "current": idx,
                        "total": total,
                        "url": detail_url,
                    },
                    source="browser_automation",
                )

                if not detail_url or not html_text:
                    if detail_url:
                        failed_urls.append(detail_url)
                    _emit_fetch_event(
                        "fetch_url_progress",
                        {
                            "stage": IngestionStage.FETCH_RAW.value,
                            "phase": "browser_automation_batch",
                            "status": "failed",
                            "current": idx,
                            "total": total,
                            "url": detail_url,
                        },
                        source="browser_automation",
                    )
                    continue

                try:
                    browser_page = scraper._create_result_from_browser_html(detail_url, html_text)
                except Exception:
                    logger.exception(
                        "Failed creating browser HTML result for %s",
                        detail_url,
                    )
                    failed_urls.append(detail_url)
                    _emit_fetch_event(
                        "fetch_url_progress",
                        {
                            "stage": IngestionStage.FETCH_RAW.value,
                            "phase": "browser_automation_batch",
                            "status": "failed",
                            "current": idx,
                            "total": total,
                            "url": detail_url,
                        },
                        source="browser_automation",
                    )
                    continue

                browser_pages.append(browser_page)
                _emit_fetch_event(
                    "fetch_url_progress",
                    {
                        "stage": IngestionStage.FETCH_RAW.value,
                        "phase": "browser_automation_batch",
                        "status": "succeeded",
                        "current": idx,
                        "total": total,
                        "url": detail_url,
                    },
                    source="browser_automation",
                )

            _append_pages(browser_pages, depth=0, from_browser=True)
            return _build_fetch_result()

        if selected_urls:
            crawl_urls = self._dedupe_urls(selected_urls, visited_urls)
            _emit_fetch_event(
                "fetch_candidates_identified",
                {
                    "stage": IngestionStage.FETCH_RAW.value,
                    "total_candidates": len(crawl_urls),
                },
                source="selected_urls",
            )
            pages, batch_failed = await self._crawl_urls_with_failures(
                scraper,
                crawl_urls,
                event_callback=event_callback,
                phase="selected_urls",
                source="selected_urls",
                batch_index=batch_index,
                batch_total=batch_total,
            )
            _append_pages(pages, depth=0, from_browser=False)
            failed_urls.extend(batch_failed)
            scout_candidates = pages
            scout_candidate_depth = 0
        elif html_content:
            _emit_fetch_event(
                "fetch_phase",
                {
                    "stage": IngestionStage.FETCH_RAW.value,
                    "message": "fetch_raw: analyzing browser-provided HTML",
                },
                source="browser_html",
            )
            probe = scraper._create_result_from_browser_html(url, html_content)
            visited_urls.add(probe.url)
            is_index = scraper._determine_page_type(probe, page_type_hint)
            if not is_index:
                _append_pages([probe], depth=0, from_browser=True)
                scout_candidates = [probe]
                scout_candidate_depth = 0
            else:
                _emit_fetch_event(
                    "fetch_phase",
                    {
                        "stage": IngestionStage.FETCH_RAW.value,
                        "message": "fetch_raw: selecting detail links from index page",
                    },
                    source="index_probe",
                )
                detail_urls, detail_link_texts = await self._select_detail_urls(
                    scraper,
                    probe,
                    candidate_taxonomy_filter_enabled=candidate_taxonomy_filter_enabled,
                    candidate_taxonomy_filter_threshold=candidate_taxonomy_filter_threshold,
                    candidate_taxonomy_filter_top_k=candidate_taxonomy_filter_top_k,
                )
                selected_link_texts.update(detail_link_texts)
                crawl_urls = self._dedupe_urls(detail_urls, visited_urls)
                _emit_fetch_event(
                    "fetch_candidates_identified",
                    {
                        "stage": IngestionStage.FETCH_RAW.value,
                        "total_candidates": len(crawl_urls),
                    },
                    source="index_probe",
                )
                if not crawl_urls:
                    _append_pages([probe], depth=0, from_browser=True)
                    scout_candidates = [probe]
                    scout_candidate_depth = 0
                else:
                    pages, batch_failed = await self._crawl_urls_with_failures(
                        scraper,
                        crawl_urls,
                        event_callback=event_callback,
                        phase="index_detail_links",
                        source="index_probe",
                        batch_index=batch_index,
                        batch_total=batch_total,
                    )
                    _append_pages(pages, depth=1, from_browser=False)
                    failed_urls.extend(batch_failed)
                    scout_candidates = pages
                    scout_candidate_depth = 1
        else:
            _emit_fetch_event(
                "fetch_phase",
                {
                    "stage": IngestionStage.FETCH_RAW.value,
                    "message": "fetch_raw: crawling entry page",
                },
                source="entry_page",
            )
            seed_page = await scraper.crawl_page(url)
            visited_urls.add(seed_page.url)
            is_index = page_type_hint == "index"
            if page_type_hint == "auto":
                is_index = scraper._determine_page_type(seed_page, "auto")

            if is_index:
                _emit_fetch_event(
                    "fetch_phase",
                    {
                        "stage": IngestionStage.FETCH_RAW.value,
                        "message": "fetch_raw: selecting detail links from entry index",
                    },
                    source="entry_index",
                )
                detail_urls, detail_link_texts = await self._select_detail_urls(
                    scraper,
                    seed_page,
                    candidate_taxonomy_filter_enabled=candidate_taxonomy_filter_enabled,
                    candidate_taxonomy_filter_threshold=candidate_taxonomy_filter_threshold,
                    candidate_taxonomy_filter_top_k=candidate_taxonomy_filter_top_k,
                )
                selected_link_texts.update(detail_link_texts)
                crawl_urls = self._dedupe_urls(detail_urls, visited_urls)
                _emit_fetch_event(
                    "fetch_candidates_identified",
                    {
                        "stage": IngestionStage.FETCH_RAW.value,
                        "total_candidates": len(crawl_urls),
                    },
                    source="entry_index",
                )
                if crawl_urls:
                    pages, batch_failed = await self._crawl_urls_with_failures(
                        scraper,
                        crawl_urls,
                        event_callback=event_callback,
                        phase="index_detail_links",
                        source="entry_index",
                        batch_index=batch_index,
                        batch_total=batch_total,
                    )
                    _append_pages(pages, depth=1, from_browser=False)
                    failed_urls.extend(batch_failed)
                    scout_candidates = pages
                    scout_candidate_depth = 1
                else:
                    _append_pages([seed_page], depth=0, from_browser=False)
                    scout_candidates = [seed_page]
                    scout_candidate_depth = 0
            else:
                _append_pages([seed_page], depth=0, from_browser=False)
                scout_candidates = [seed_page]
                scout_candidate_depth = 0

        for _ in range(continue_depth):
            candidates = [page for page in scout_candidates if page.markdown]
            if not candidates:
                break

            deeper_urls, scout_call_count, all_scouted_links = await asyncio.to_thread(
                run_scout,
                scraper.router,
                candidates,
                visited_urls,
                scout_call_count,
                all_scouted_links,
            )
            crawl_urls = self._dedupe_urls(deeper_urls, visited_urls)
            if not crawl_urls:
                break

            next_depth = scout_candidate_depth + 1
            _emit_fetch_event(
                "fetch_candidates_identified",
                {
                    "stage": IngestionStage.FETCH_RAW.value,
                    "total_candidates": len(crawl_urls),
                },
                source=f"continue_depth_{next_depth}",
            )
            pages, batch_failed = await self._crawl_urls_with_failures(
                scraper,
                crawl_urls,
                event_callback=event_callback,
                phase=f"continue_depth_{next_depth}",
                source=f"continue_depth_{next_depth}",
                batch_index=batch_index,
                batch_total=batch_total,
            )
            _append_pages(pages, depth=next_depth, from_browser=False)
            failed_urls.extend(batch_failed)
            scout_candidates = pages
            scout_candidate_depth = next_depth

        return _build_fetch_result()

    def _stage_extract_structured(
        self,
        request_payload: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        raw_pages = context.get("raw_pages") or []
        cleaner = LLMCleanerAgent()
        univ_slug = str(request_payload.get("univ_slug") or "")
        page_type_hint = str(request_payload.get("page_type_hint") or "auto").strip().lower()
        selected_urls_count = len(request_payload.get("selected_urls") or [])
        is_index_mode_request = page_type_hint == "index" or selected_urls_count > 0

        taxonomy_enabled = self._coerce_bool(
            request_payload.get("taxonomy_enabled"),
            default=True,
        )
        taxonomy_low_threshold = self._coerce_float(
            request_payload.get("taxonomy_low_threshold"),
            default=0.8,
        )
        taxonomy_high_threshold = self._coerce_float(
            request_payload.get("taxonomy_high_threshold"),
            default=0.92,
        )
        taxonomy_high_threshold = max(taxonomy_high_threshold, taxonomy_low_threshold)
        taxonomy_hint_top_k = self._coerce_int(
            request_payload.get("taxonomy_hint_top_k"),
            default=3,
            minimum=1,
            maximum=5,
        )
        taxonomy_override_enabled = self._coerce_bool(
            request_payload.get("taxonomy_override_enabled"),
            default=True,
        )
        name_resolution_llm_enabled = self._coerce_bool(
            request_payload.get("name_resolution_llm_enabled"),
            default=True,
        )
        name_resolution_low_threshold = self._coerce_float(
            request_payload.get("name_resolution_low_threshold"),
            default=0.8,
        )
        name_resolution_conflict_delta = self._coerce_float(
            request_payload.get("name_resolution_conflict_delta"),
            default=0.05,
        )

        candidates: List[Dict[str, Any]] = []
        extract_errors: List[Dict[str, str]] = []
        unresolved_urls: List[Dict[str, Any]] = []

        for row in raw_pages:
            row_markdown = str(row.get("markdown") or "")
            row_html_raw = row.get("html")
            row_html = str(row_html_raw or "")
            markdown_length = len(row_markdown)
            html_length = len(row_html)
            from_browser = bool(row.get("from_browser"))
            markdown_insufficient = (
                html_length > 1000
                and markdown_length < (html_length * 0.05)
            )

            page = CrawlPageResult(
                url=str(row.get("url") or ""),
                markdown=row_markdown,
                char_count=int(row.get("char_count") or 0),
                links=list(row.get("links") or []),
                status_code=(
                    int(row.get("status_code"))
                    if str(row.get("status_code") or "").isdigit()
                    else None
                ),
                html=row_html_raw,
            )

            if is_index_mode_request and not from_browser and markdown_insufficient:
                warning_msg = (
                    "Anti-crawl suspected: index mode cannot reliably parse this detail URL "
                    "(markdown too short). Skipping URL; use detail mode with browser HTML."
                )
                logger.warning(
                    "%s url=%s md_len=%d html_len=%d",
                    warning_msg,
                    page.url,
                    markdown_length,
                    html_length,
                )
                extract_errors.append(
                    {
                        "url": page.url,
                        "error": warning_msg,
                    }
                )
                continue

            taxonomy_signals = self._build_taxonomy_signals(
                page_url=page.url,
                markdown=page.markdown,
                selected_anchor_text=row.get("selected_anchor_text"),
            )

            taxonomy_matches: list[dict] = []
            best_match: Optional[dict] = None
            best_score = 0.0
            name_hints: list[str] = []
            if taxonomy_enabled and taxonomy_signals:
                taxonomy_matches = self.taxonomy_service.match_signals(
                    taxonomy_signals,
                    top_k=taxonomy_hint_top_k,
                )
                if taxonomy_matches:
                    best_match = dict(taxonomy_matches[0])
                    best_score = float(best_match.get("score") or 0.0)
                if best_score >= taxonomy_low_threshold:
                    name_hints = self._build_name_hints(
                        taxonomy_matches,
                        top_k=taxonomy_hint_top_k,
                    )

            program_data, error = extract_program_data_from_page(
                page=page,
                cleaner=cleaner,
                univ_slug=univ_slug,
                year=int(request_payload.get("year") or 0),
                current_depth=int(row.get("crawl_depth") or 0),
                from_browser=from_browser,
                name_hints=name_hints,
                selected_anchor_text=str(row.get("selected_anchor_text") or "").strip() or None,
            )
            if program_data:
                if is_index_mode_request:
                    resolution = resolve_program_name(
                        markdown_name=str(program_data.get("name_en") or ""),
                        selected_anchor_text=str(row.get("selected_anchor_text") or ""),
                        detail_url=page.url,
                        html_title=_extract_html_title(row_html),
                        is_index_mode=is_index_mode_request,
                        taxonomy_matches=taxonomy_matches,
                        router=getattr(cleaner, "router", None),
                        llm_fallback_enabled=name_resolution_llm_enabled,
                        low_threshold=name_resolution_low_threshold,
                        conflict_delta=name_resolution_conflict_delta,
                    )
                    if resolution.status != "resolved":
                        unresolved_urls.append(
                            {
                                "url": page.url,
                                "reason": resolution.reason,
                            }
                        )
                        extract_errors.append(
                            {
                                "url": page.url,
                                "error": f"Program name unresolved: {resolution.reason}",
                            }
                        )
                        logger.warning(
                            "program-name unresolved, skipped url=%s reason=%s",
                            page.url,
                            resolution.reason,
                        )
                        continue
                    program_data["name_en"] = resolution.name
                self._attach_taxonomy_trace(
                    program_data=program_data,
                    univ_slug=univ_slug,
                    taxonomy_enabled=taxonomy_enabled,
                    taxonomy_signals=taxonomy_signals,
                    taxonomy_matches=taxonomy_matches,
                    best_match=best_match,
                    best_score=best_score,
                    taxonomy_low_threshold=taxonomy_low_threshold,
                    taxonomy_high_threshold=taxonomy_high_threshold,
                    taxonomy_hint_top_k=taxonomy_hint_top_k,
                    taxonomy_override_enabled=taxonomy_override_enabled,
                    hints_injected=bool(name_hints),
                )
                candidates.append(_json_safe(program_data))
            else:
                extract_errors.append(
                    {
                        "url": page.url,
                        "error": str(error or "No structured data extracted"),
                    }
                )

        return {
            "program_candidates": candidates,
            "extracted_count": len(candidates),
            "extract_errors": extract_errors,
            "unresolved_urls": unresolved_urls,
            "candidate_hash": _hash_payload(candidates),
        }

    def _attach_taxonomy_trace(
        self,
        *,
        program_data: Dict[str, Any],
        univ_slug: str,
        taxonomy_enabled: bool,
        taxonomy_signals: list[str],
        taxonomy_matches: list[dict],
        best_match: Optional[dict],
        best_score: float,
        taxonomy_low_threshold: float,
        taxonomy_high_threshold: float,
        taxonomy_hint_top_k: int,
        taxonomy_override_enabled: bool,
        hints_injected: bool,
    ) -> None:
        metadata = program_data.setdefault("extra_metadata", {})
        if not isinstance(metadata, dict):
            return

        taxonomy_trace: dict[str, Any] = {
            "enabled": taxonomy_enabled,
            "signals": taxonomy_signals,
            "best_score": round(best_score, 4),
            "low_threshold": taxonomy_low_threshold,
            "high_threshold": taxonomy_high_threshold,
            "hint_top_k": taxonomy_hint_top_k,
            "hints_injected": hints_injected,
            "override_enabled": taxonomy_override_enabled,
            "override_applied": False,
            "matches": [
                {
                    "name_en": str(item.get("name_en") or ""),
                    "score": float(item.get("score") or 0.0),
                    "normalized_name": item.get("normalized_name"),
                }
                for item in taxonomy_matches[:taxonomy_hint_top_k]
            ],
        }

        should_override = (
            taxonomy_enabled
            and taxonomy_override_enabled
            and bool(best_match)
            and best_score >= taxonomy_high_threshold
        )
        if should_override and best_match:
            canonical_name = str(best_match.get("name_en") or "").strip()
            current_name = str(program_data.get("name_en") or "").strip()
            if canonical_name and (
                is_noise_program_name(current_name)
                or normalize_taxonomy_name(current_name)
                != normalize_taxonomy_name(canonical_name)
            ):
                program_data["name_en"] = canonical_name
                if univ_slug:
                    program_data["program_group_code"] = generate_program_group_code(
                        univ_slug,
                        canonical_name,
                    )
                taxonomy_trace["override_applied"] = True
                taxonomy_trace["override_name"] = canonical_name
                taxonomy_trace["override_reason"] = "high_confidence_match"

        metadata["taxonomy_match"] = taxonomy_trace

    def _stage_validate_rules(
        self,
        request_payload: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        candidates = context.get("program_candidates") or []
        validated: List[Dict[str, Any]] = []
        rejected: List[Dict[str, str]] = []

        for idx, item in enumerate(candidates):
            payload = dict(item or {})
            payload.setdefault("academic_year", int(request_payload.get("year") or 0))
            payload.setdefault("study_options", [])
            payload.setdefault("deadlines", [])
            payload.setdefault("requirements", [])
            payload.setdefault("extra_metadata", {})

            try:
                model = ValidatedProgramPayload.model_validate(payload)
                validated.append(model.model_dump(mode="json"))
            except ValidationError as exc:
                rejected.append(
                    {
                        "index": str(idx),
                        "name_en": str(payload.get("name_en") or ""),
                        "error": str(exc),
                    }
                )

        return {
            "validated_programs": validated,
            "validated_count": len(validated),
            "rejected_programs": rejected,
            "validated_hash": _hash_payload(validated),
        }

    def _stage_persist_versioned(
        self,
        request_payload: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        validated_programs = context.get("validated_programs") or []
        univ_slug = str(request_payload.get("univ_slug") or "")
        if not univ_slug:
            raise ValueError("univ_slug is required for persist_versioned")

        persisted_count = 0
        created_count = 0
        updated_count = 0
        quarantined_count = 0
        persisted_program_ids: List[int] = []
        failed_records: List[Dict[str, str]] = []
        taxonomy_learn_records: List[Dict[str, Any]] = []

        for item in validated_programs:
            item_dict = dict(item)
            verdict = evaluate_extraction(item_dict)
            if not verdict.passed:
                reason_value = verdict.reason.value if verdict.reason else "unknown"
                logger.warning(
                    "Quality gate rejected %s (reason=%s, signals=%s)",
                    item_dict.get("source_url") or item_dict.get("name_en"),
                    reason_value,
                    verdict.signals,
                )
                try:
                    self.db_manager.upsert_quarantine(
                        university_slug=univ_slug,
                        program_data=item_dict,
                        reason=verdict.reason,
                        signals=verdict.signals,
                    )
                    quarantined_count += 1
                except Exception:  # pylint: disable=broad-except
                    logger.exception("Failed to record quarantine for ingestion item")
                continue

            try:
                program, created = self.db_manager.upsert_program(item_dict, univ_slug)
                persisted_count += 1
                if program.id is not None:
                    persisted_program_ids.append(int(program.id))
                if created:
                    created_count += 1
                else:
                    updated_count += 1

                extra_metadata = item.get("extra_metadata")
                taxonomy_trace = (
                    extra_metadata.get("taxonomy_match")
                    if isinstance(extra_metadata, dict)
                    else None
                )
                confidence_raw = (
                    taxonomy_trace.get("best_score")
                    if isinstance(taxonomy_trace, dict)
                    else None
                )
                try:
                    confidence = float(confidence_raw)
                except (TypeError, ValueError):
                    confidence = 1.0
                confidence = max(0.0, min(1.0, confidence))

                persisted_name = str(item.get("name_en") or getattr(program, "name_en", "")).strip()
                persisted_source_url = str(
                    item.get("source_url")
                    or getattr(program, "source_url", "")
                    or ""
                ).strip()
                taxonomy_learn_records.append(
                    {
                        "name_en": persisted_name,
                        "source_url": persisted_source_url,
                        "confidence": confidence,
                    }
                )
            except Exception as exc:
                failed_records.append(
                    {
                        "name_en": str(item.get("name_en") or ""),
                        "error": str(exc),
                    }
                )

        if failed_records:
            message = (
                "persist_versioned stage failed for "
                f"{len(failed_records)} record(s): {failed_records[0]['error']}"
            )
            raise StageExecutionError(message)

        taxonomy_learning = {
            "enabled": True,
            "prepared": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
        }
        if taxonomy_learn_records:
            try:
                taxonomy_learning = self.taxonomy_service.learn_persisted_names(
                    taxonomy_learn_records,
                    enabled=True,
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("taxonomy learning skipped after persist_versioned: %s", exc)

        return {
            "persisted_count": persisted_count,
            "created_count": created_count,
            "updated_count": updated_count,
            "quarantined_count": quarantined_count,
            "persisted_program_ids": persisted_program_ids,
            "persisted_hash": _hash_payload(
                {
                    "count": persisted_count,
                    "created": created_count,
                    "updated": updated_count,
                    "quarantined": quarantined_count,
                    "program_ids": persisted_program_ids,
                    "validated_hash": context.get("validated_hash"),
                }
            ),
            "taxonomy_learning": taxonomy_learning,
        }

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        if value is None:
            return float(default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _coerce_int(
        value: Any,
        default: int,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        if value is None:
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _build_name_hints(matches: list[dict], top_k: int) -> list[str]:
        hints: list[str] = []
        for item in matches[: max(1, int(top_k))]:
            name = str(item.get("name_en") or "").strip()
            if not name:
                continue
            score = float(item.get("score") or 0.0)
            hints.append(f"{name}|{score:.2f}")
        return hints

    @staticmethod
    def _build_taxonomy_signals(
        *,
        page_url: str,
        markdown: str,
        selected_anchor_text: Any = None,
    ) -> list[str]:
        signals: list[str] = []

        anchor_text = str(selected_anchor_text or "").strip()
        if anchor_text:
            signals.append(anchor_text)

        url_signal = build_url_name_signal(page_url)
        if url_signal and url_signal not in signals:
            signals.append(url_signal)

        heading_signal = extract_program_name(markdown)
        if heading_signal and heading_signal not in signals:
            signals.append(heading_signal)

        return signals

    def rank_index_candidates(
        self,
        links: List[Dict[str, Any]],
        *,
        keep_threshold: float = 0.75,
        auto_run_threshold: float = 0.92,
        top_k: int = 30,
    ) -> List[Dict[str, Any]]:
        """Rank index-page link candidates with taxonomy scores.

        Returns only links scoring above ``keep_threshold`` sorted by score desc.
        """
        bounded_keep = max(0.0, min(1.0, float(keep_threshold)))
        bounded_auto = max(bounded_keep, min(1.0, float(auto_run_threshold)))
        bounded_top_k = max(1, int(top_k))

        ranked: List[Dict[str, Any]] = []
        for item in links or []:
            if not isinstance(item, dict):
                continue
            detail_url = str(item.get("url") or "").strip()
            anchor_text = str(item.get("text") or "").strip()
            if not detail_url:
                continue

            signals: list[str] = []
            if anchor_text:
                signals.append(anchor_text)
            url_signal = build_url_name_signal(detail_url)
            if url_signal and url_signal not in signals:
                signals.append(url_signal)

            score = 0.0
            inferred_name: Optional[str] = None
            if signals:
                matches = self.taxonomy_service.match_signals(signals, top_k=1)
                if matches:
                    best_match = dict(matches[0] or {})
                    score = float(best_match.get("score") or 0.0)
                    inferred_name_text = str(best_match.get("name_en") or "").strip()
                    if inferred_name_text:
                        inferred_name = inferred_name_text

            if score < bounded_keep:
                continue

            ranked.append(
                {
                    "url": detail_url,
                    "text": anchor_text,
                    "taxonomy_score": round(score, 4),
                    "program_name_inferred": inferred_name,
                    "auto_run_eligible": score >= bounded_auto,
                }
            )

        ranked.sort(key=lambda row: float(row.get("taxonomy_score") or 0.0), reverse=True)
        return ranked[:bounded_top_k]

    async def _select_detail_urls(
        self,
        scraper: AdmissionScraper,
        page: CrawlPageResult,
        *,
        candidate_taxonomy_filter_enabled: bool = False,
        candidate_taxonomy_filter_threshold: float = 0.75,
        candidate_taxonomy_filter_top_k: int = 30,
    ) -> tuple[List[str], Dict[str, str]]:
        if not page.markdown:
            return [], {}

        link_pairs = extract_links_with_text(page.markdown, page.url)
        if not link_pairs:
            return [], {}

        detail_urls = await asyncio.to_thread(
            filter_links_by_llm,
            scraper.router,
            link_pairs,
            page.url,
        )
        if not detail_urls:
            detail_urls = [u for u, _ in link_pairs]

        seen: set[str] = set()
        deduped: List[str] = []
        for item in detail_urls:
            url = str(item or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            deduped.append(url)

        url_to_text = {
            str(url).strip(): str(text).strip()
            for url, text in link_pairs
            if str(url).strip()
        }

        if candidate_taxonomy_filter_enabled and deduped:
            ranked: List[tuple[float, str]] = []
            for detail_url in deduped:
                signals: list[str] = []
                anchor_text = str(url_to_text.get(detail_url) or "").strip()
                if anchor_text:
                    signals.append(anchor_text)
                url_signal = build_url_name_signal(detail_url)
                if url_signal:
                    signals.append(url_signal)
                if not signals:
                    continue
                matches = self.taxonomy_service.match_signals(signals, top_k=1)
                if not matches:
                    continue
                score = float(matches[0].get("score") or 0.0)
                if score >= candidate_taxonomy_filter_threshold:
                    ranked.append((score, detail_url))

            if ranked:
                ranked.sort(key=lambda item: item[0], reverse=True)
                filtered_urls = [
                    url
                    for _score, url in ranked[: max(1, int(candidate_taxonomy_filter_top_k))]
                ]
                logger.info(
                    "Candidate taxonomy filter retained %d/%d links "
                    "(threshold=%.2f, top_k=%d)",
                    len(filtered_urls),
                    len(deduped),
                    candidate_taxonomy_filter_threshold,
                    candidate_taxonomy_filter_top_k,
                )
                deduped = filtered_urls
                seen = set(filtered_urls)
            else:
                logger.warning(
                    "Candidate taxonomy filter retained 0/%d links; "
                    "falling back to unfiltered candidates",
                    len(deduped),
                )

        text_map = {
            str(url).strip(): str(text).strip()
            for url, text in link_pairs
            if str(url).strip() and str(text).strip() and str(url).strip() in seen
        }
        return deduped, text_map

    async def _crawl_urls_with_failures(
        self,
        scraper: AdmissionScraper,
        urls: List[str],
        *,
        event_callback: Optional[IngestionEventCallback] = None,
        phase: str = "detail_links",
        source: Optional[str] = None,
        batch_index: Optional[int] = None,
        batch_total: Optional[int] = None,
    ) -> tuple[List[CrawlPageResult], List[str]]:
        """Crawl URLs and infer failures from missing success rows."""
        if not urls:
            return [], []
        total = len(urls)
        pages: List[CrawlPageResult] = []
        failed_urls: List[str] = []

        def _emit_progress(status: str, current: int, url: str) -> None:
            payload: Dict[str, Any] = {
                "stage": IngestionStage.FETCH_RAW.value,
                "phase": phase,
                "status": status,
                "current": current,
                "total": total,
                "url": url,
            }
            if source:
                payload["source"] = source
            if batch_index is not None:
                payload["batch_index"] = batch_index
            if batch_total is not None:
                payload["batch_total"] = batch_total
            self._emit_event(
                event_callback,
                "fetch_url_progress",
                payload,
            )

        for idx, url in enumerate(urls, start=1):
            _emit_progress("started", idx, url)
            logger.info("[FetchRaw:%s] Crawling %d/%d: %s", phase, idx, total, url)

            crawled = await scraper._crawl_urls([url])
            if crawled:
                pages.extend(crawled)
                _emit_progress("succeeded", idx, url)
            else:
                failed_urls.append(url)
                _emit_progress("failed", idx, url)

        return pages, failed_urls

    @staticmethod
    def _dedupe_urls(urls: List[str], visited_urls: set[str]) -> List[str]:
        """Normalize/trim URLs and remove duplicates or already visited ones."""
        out: List[str] = []
        seen: set[str] = set()
        for item in urls:
            url = str(item or "").strip()
            if not url or url in seen or url in visited_urls:
                continue
            seen.add(url)
            out.append(url)
        return out

    @staticmethod
    def _serialize_pages(
        pages: List[CrawlPageResult],
        *,
        depth: int,
        from_browser: bool,
        selected_link_texts: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        selected_link_texts = selected_link_texts or {}
        out: List[Dict[str, Any]] = []
        for page in pages:
            page_url = str(page.url or "").strip()
            out.append(
                {
                    "url": page.url,
                    "markdown": page.markdown,
                    "char_count": page.char_count,
                    "links": page.links,
                    "status_code": page.status_code,
                    "html": page.html,
                    "crawl_depth": depth,
                    "from_browser": from_browser,
                    "selected_anchor_text": selected_link_texts.get(page_url),
                }
            )
        return out

    def _create_job(self, request_payload: Dict[str, Any]) -> str:
        now = _utc_now()
        job_uid = uuid.uuid4().hex[:16]
        with self.db_manager.get_session() as session:
            job = IngestionJob(
                job_uid=job_uid,
                univ_slug=str(request_payload.get("univ_slug") or ""),
                academic_year=int(request_payload.get("year") or 0),
                source_url=str(request_payload.get("url") or ""),
                continue_depth=int(request_payload.get("continue_depth") or 0),
                page_type_hint=str(request_payload.get("page_type_hint") or "auto"),
                status=IngestionJobStatus.PENDING,
                request_payload=_json_safe(request_payload),
                context_payload={"stage_trace": []},
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            session.commit()
        return job_uid

    def _require_job(self, job_uid: str) -> IngestionJob:
        with self.db_manager.get_session() as session:
            job = session.exec(
                select(IngestionJob).where(IngestionJob.job_uid == job_uid)
            ).first()
            if job is None:
                raise ValueError(f"Ingestion job not found: {job_uid}")
            session.expunge(job)
            return job

    def _infer_resume_stage(self, job_uid: str) -> Optional[IngestionStage]:
        with self.db_manager.get_session() as session:
            job = session.exec(
                select(IngestionJob).where(IngestionJob.job_uid == job_uid)
            ).first()
            if not job:
                return None

            tasks = session.exec(
                select(IngestionTask).where(IngestionTask.job_id == job.id)
            ).all()
            stage_to_task = {task.stage: task for task in tasks}

            for stage in STAGE_ORDER:
                task = stage_to_task.get(stage)
                if task is None:
                    return stage
                if task.state != IngestionTaskState.SUCCEEDED:
                    return stage

        return None

    def _reset_tasks_from_stage(self, job_uid: str, stage: IngestionStage) -> None:
        start_idx = _stage_order_index(stage)
        now = _utc_now()
        with self.db_manager.get_session() as session:
            job = session.exec(
                select(IngestionJob).where(IngestionJob.job_uid == job_uid)
            ).first()
            if not job:
                raise ValueError(f"Ingestion job not found: {job_uid}")

            tasks = session.exec(
                select(IngestionTask).where(IngestionTask.job_id == job.id)
            ).all()
            for task in tasks:
                if _stage_order_index(task.stage) < start_idx:
                    continue
                task.state = IngestionTaskState.PENDING
                task.error_message = None
                task.attempt_count = 0
                task.backoff_seconds = 0
                task.next_retry_at = None
                task.started_at = None
                task.finished_at = None
                task.idempotency_key = None
                task.input_payload = {}
                task.output_payload = {}
                task.updated_at = now
                session.add(task)

            context = _json_safe(job.context_payload or {})
            self._prune_context_from_stage(context, stage)
            self._append_stage_trace(
                context=context,
                stage=stage,
                state="RESET",
                attempt=0,
                error=None,
            )

            job.status = IngestionJobStatus.PENDING
            job.current_stage = stage
            job.resume_from_stage = stage
            job.error_message = None
            job.context_payload = _json_safe(context)
            job.updated_at = now
            job.finished_at = None
            session.add(job)
            session.commit()

    def _build_stage_input(
        self,
        stage: IngestionStage,
        request_payload: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if stage == IngestionStage.FETCH_RAW:
            return {
                "url": request_payload.get("url"),
                "page_type_hint": request_payload.get("page_type_hint"),
                "selected_urls_count": len(request_payload.get("selected_urls") or []),
                "has_html_content": bool(request_payload.get("html_content")),
                "continue_depth": int(request_payload.get("continue_depth") or 0),
            }
        if stage == IngestionStage.EXTRACT_STRUCTURED:
            raw_pages = context.get("raw_pages") or []
            return {
                "raw_page_count": len(raw_pages),
                "source_content_hash": context.get("source_content_hash"),
            }
        if stage == IngestionStage.VALIDATE_RULES:
            candidates = context.get("program_candidates") or []
            return {
                "candidate_count": len(candidates),
                "candidate_hash": context.get("candidate_hash") or _hash_payload(candidates),
            }
        if stage == IngestionStage.PERSIST_VERSIONED:
            validated = context.get("validated_programs") or []
            return {
                "validated_count": len(validated),
                "validated_hash": context.get("validated_hash") or _hash_payload(validated),
            }
        return {}

    @staticmethod
    def _build_idempotency_key(
        stage: IngestionStage,
        request_payload: Dict[str, Any],
        stage_input: Dict[str, Any],
    ) -> str:
        payload = {
            "stage": stage.value,
            "univ_slug": request_payload.get("univ_slug"),
            "year": request_payload.get("year"),
            "source_url": request_payload.get("url"),
            "stage_input": stage_input,
        }
        return _hash_payload(payload)

    def _prepare_stage_task(
        self,
        *,
        job_uid: str,
        stage: IngestionStage,
        stage_input: Dict[str, Any],
        idempotency_key: str,
    ) -> tuple[IngestionTask, bool]:
        now = _utc_now()
        with self.db_manager.get_session() as session:
            job = session.exec(
                select(IngestionJob).where(IngestionJob.job_uid == job_uid)
            ).first()
            if not job:
                raise ValueError(f"Ingestion job not found: {job_uid}")

            task = session.exec(
                select(IngestionTask).where(
                    IngestionTask.job_id == job.id,
                    IngestionTask.stage == stage,
                )
            ).first()
            if task is None:
                task = IngestionTask(
                    job_id=job.id,
                    stage=stage,
                    state=IngestionTaskState.PENDING,
                    max_retries=self.stage_max_retries,
                    created_at=now,
                    updated_at=now,
                )
                session.add(task)
                session.flush()

            can_skip = (
                task.state == IngestionTaskState.SUCCEEDED
                and task.idempotency_key == idempotency_key
            )
            if not can_skip:
                if task.idempotency_key != idempotency_key:
                    task.state = IngestionTaskState.PENDING
                    task.attempt_count = 0
                    task.error_message = None
                    task.next_retry_at = None
                    task.backoff_seconds = 0
                    task.started_at = None
                    task.finished_at = None
                    task.output_payload = {}
                task.input_payload = _json_safe(stage_input)
                task.idempotency_key = idempotency_key
                task.updated_at = now
                session.add(task)

            session.commit()
            session.refresh(task)
            session.expunge(task)
            return task, can_skip

    def _mark_task_running(self, task_id: Optional[int], attempt_no: int) -> None:
        if task_id is None:
            return
        now = _utc_now()
        with self.db_manager.get_session() as session:
            task = session.get(IngestionTask, task_id)
            if not task:
                return
            task.state = IngestionTaskState.RUNNING
            task.attempt_count = attempt_no
            task.error_message = None
            task.backoff_seconds = 0
            task.next_retry_at = None
            task.started_at = now
            task.updated_at = now
            session.add(task)
            session.commit()

    def _mark_task_success(self, task_id: Optional[int], output_payload: Dict[str, Any]) -> None:
        if task_id is None:
            return
        now = _utc_now()
        with self.db_manager.get_session() as session:
            task = session.get(IngestionTask, task_id)
            if not task:
                return
            task.state = IngestionTaskState.SUCCEEDED
            task.output_payload = _json_safe(output_payload)
            task.error_message = None
            task.backoff_seconds = 0
            task.next_retry_at = None
            task.finished_at = now
            task.updated_at = now
            session.add(task)
            session.commit()

    def _mark_task_failure(self, task_id: Optional[int], error_message: str) -> Dict[str, Any]:
        if task_id is None:
            return {
                "state": IngestionTaskState.FAILED.value,
                "attempt_count": 0,
                "backoff_seconds": 0,
            }

        now = _utc_now()
        with self.db_manager.get_session() as session:
            task = session.get(IngestionTask, task_id)
            if not task:
                return {
                    "state": IngestionTaskState.FAILED.value,
                    "attempt_count": 0,
                    "backoff_seconds": 0,
                }

            attempt_count = int(task.attempt_count or 0)
            max_retries = int(task.max_retries or 0)

            if attempt_count > max_retries:
                task.state = IngestionTaskState.POISONED
                task.error_message = error_message
                task.finished_at = now
                task.updated_at = now
                session.add(task)
                session.commit()
                return {
                    "state": IngestionTaskState.POISONED.value,
                    "attempt_count": attempt_count,
                    "backoff_seconds": 0,
                }

            backoff_seconds = min(
                2 ** max(0, attempt_count - 1),
                MAX_RETRY_BACKOFF_SECONDS,
            )
            task.state = IngestionTaskState.RETRY_SCHEDULED
            task.error_message = error_message
            task.backoff_seconds = backoff_seconds
            task.next_retry_at = now + timedelta(seconds=backoff_seconds)
            task.updated_at = now
            session.add(task)
            session.commit()
            return {
                "state": IngestionTaskState.RETRY_SCHEDULED.value,
                "attempt_count": attempt_count,
                "backoff_seconds": backoff_seconds,
            }

    def _append_stage_trace(
        self,
        context: Dict[str, Any],
        stage: IngestionStage,
        state: str,
        attempt: int,
        error: Optional[str] = None,
    ) -> None:
        trace = list(context.get("stage_trace") or [])
        trace_seq = int(context.get("trace_seq") or 0) + 1
        trace.append(
            {
                "seq": trace_seq,
                "stage": stage.value,
                "state": state,
                "attempt": attempt,
                "timestamp": _utc_now().isoformat(),
                "error": error,
            }
        )
        context["stage_trace"] = trace[-200:]
        context["trace_seq"] = trace_seq

    def _prune_context_from_stage(
        self,
        context: Dict[str, Any],
        stage: IngestionStage,
    ) -> None:
        for key in STAGE_CONTEXT_KEYS.get(stage, ()):
            context.pop(key, None)

    @staticmethod
    def _emit_event(
        callback: Optional[IngestionEventCallback],
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        if callback is None:
            return
        try:
            callback(event_type, _json_safe(payload))
        except Exception as exc:  # pragma: no cover - defensive callback isolation
            logger.warning("Ingestion event callback failed: %s", exc)

    def _mark_job_running(self, job_uid: str, stage: IngestionStage) -> None:
        now = _utc_now()
        with self.db_manager.get_session() as session:
            job = session.exec(
                select(IngestionJob).where(IngestionJob.job_uid == job_uid)
            ).first()
            if not job:
                return
            job.status = IngestionJobStatus.RUNNING
            job.current_stage = stage
            if job.started_at is None:
                job.started_at = now
            job.updated_at = now
            session.add(job)
            session.commit()

    def _mark_job_stage_success(
        self,
        job_uid: str,
        stage: IngestionStage,
        context: Dict[str, Any],
    ) -> None:
        now = _utc_now()
        with self.db_manager.get_session() as session:
            job = session.exec(
                select(IngestionJob).where(IngestionJob.job_uid == job_uid)
            ).first()
            if not job:
                return
            job.status = IngestionJobStatus.RUNNING
            job.current_stage = stage
            job.context_payload = _json_safe(context)
            job.updated_at = now
            session.add(job)
            session.commit()

    def _mark_job_succeeded(self, job_uid: str, context: Dict[str, Any]) -> None:
        now = _utc_now()
        with self.db_manager.get_session() as session:
            job = session.exec(
                select(IngestionJob).where(IngestionJob.job_uid == job_uid)
            ).first()
            if not job:
                return
            job.status = IngestionJobStatus.SUCCEEDED
            job.current_stage = IngestionStage.PERSIST_VERSIONED
            job.resume_from_stage = None
            job.error_message = None
            job.context_payload = _json_safe(context)
            job.updated_at = now
            job.finished_at = now
            session.add(job)
            session.commit()

    def _mark_job_terminal_error(
        self,
        job_uid: str,
        error_message: str,
        status: IngestionJobStatus,
    ) -> None:
        now = _utc_now()
        with self.db_manager.get_session() as session:
            job = session.exec(
                select(IngestionJob).where(IngestionJob.job_uid == job_uid)
            ).first()
            if not job:
                return
            job.status = status
            job.error_message = error_message
            job.updated_at = now
            job.finished_at = now
            session.add(job)
            session.commit()
