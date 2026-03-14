"""PydanticAI runtime with low-confidence onhold orchestration and fallback."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from src.agent_runtime.policy import PolicyMergeResult, PolicyProfile

from src.agent_runtime.base import AgentRequest, AgentResponse
from src.agent_runtime.legacy_runtime import LegacyRuntime
from src.agent_runtime.policy import merge_policy
from src.agent_runtime.review_models import build_onhold_items
from src.services.crawler import analyze_url_candidates, crawl_url
from src.services.ingestion_pipeline import IngestionPipeline

logger = logging.getLogger(__name__)


@dataclass
class _CrawlPlan:
    """Internal plan object produced by ``_plan_crawl_request``."""

    request: AgentRequest
    payload: dict[str, Any]
    url: str
    univ_slug: str
    year: int
    page_type_hint: str
    profile: PolicyProfile
    merged_policy: PolicyMergeResult
    trace: list[dict[str, Any]]
    browser_provider_override: str | None = None


class PydanticAIRuntime:
    """Opt-in runtime using typed async orchestration with guarded fallback.

    The class name reflects the target evolution path rather than the current
    implementation.  Direct ``pydantic-ai`` agent integration is planned for
    Phase C (Selective Rollout).  Until then, orchestration uses plain async
    Python with typed bridge/skill contracts.
    """

    name = "pydanticai"

    def __init__(
        self,
        bridge: Any = None,
        model_adapter: Any = None,
        fallback_runtime: LegacyRuntime | None = None,
    ) -> None:
        self.bridge = bridge
        # Reserved for subsequent model-routing integration phases.
        self.model_adapter = model_adapter
        self.fallback_runtime = fallback_runtime or LegacyRuntime(
            bridge=bridge,
            model_adapter=model_adapter,
        )

    async def run(self, request: AgentRequest) -> AgentResponse:
        try:
            return await self._run_agent(request)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("pydanticai runtime failed, falling back: %s", exc)
            return await self.fallback_runtime.run(request)

    async def _run_agent(self, request: AgentRequest) -> AgentResponse:
        """Execute one request through orchestrated crawl + onhold review workflow."""
        logger.info("[Agent] Starting agent orchestration for task: %s", request.task)

        if str(request.task or "").strip().lower() != "crawl":
            logger.info("[Agent] Non-crawl task detected, skipping orchestration")
            return self._non_crawl_response(request)

        plan = self._plan_crawl_request(request)
        logger.info(
            "[Agent] Crawl plan created: url=%s, univ=%s, year=%s, page_type_hint=%s",
            plan.url,
            plan.univ_slug,
            plan.year,
            plan.page_type_hint,
        )

        missing_fields = self._missing_required_fields(plan)
        if missing_fields:
            logger.info("[Agent] Missing required fields: %s", missing_fields)
            return self._missing_fields_response(plan, missing_fields)

        detected_page_type, analysis_result = await self._detect_page_type(plan)
        logger.info("[Agent] Page type detected: %s", detected_page_type)

        has_analysis_links = bool(
            analysis_result
            and any(isinstance(item, dict) for item in (analysis_result.get("links") or []))
        )

        if detected_page_type == "index" and has_analysis_links:
            logger.info("[Agent] Executing index flow with %d analyzed links", len(analysis_result.get("links", [])))
            return await self._run_index_flow(plan, analysis_result)

        if detected_page_type == "detail":
            logger.info("[Agent] Executing detail flow for single page")
            return await self._run_detail_flow(plan, detected_page_type)

        # Analysis failed or returned no links — delegate to the full
        # ingestion pipeline which has its own robust HTML fetching,
        # page-type detection, and processing logic.
        logger.info(
            "[Agent] Analysis unavailable, delegating to pipeline flow (hint=%s)",
            detected_page_type,
        )
        return await self._run_pipeline_flow(plan, detected_page_type)

    def _non_crawl_response(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=[{"stage": "skip_non_crawl", "task": request.task}],
            output={"task": request.task, **dict(request.payload or {})},
        )

    def _plan_crawl_request(self, request: AgentRequest) -> _CrawlPlan:
        payload = dict(request.payload or {})
        request_policy = payload.get("policy_profile")
        context_policy = request.context.get("policy_profile") if isinstance(request.context, dict) else None

        merged_policy = merge_policy(
            request_overrides=request_policy if isinstance(request_policy, dict) else {},
            client_policy=context_policy if isinstance(context_policy, dict) else {},
            server_defaults={},
        )
        profile = merged_policy.profile

        return _CrawlPlan(
            request=request,
            payload=payload,
            url=str(payload.get("url") or "").strip(),
            univ_slug=str(payload.get("univ_slug") or "").strip().lower(),
            year=self._coerce_year(payload.get("year")),
            page_type_hint=str(payload.get("page_type_hint") or "auto").strip().lower() or "auto",
            profile=profile,
            merged_policy=merged_policy,
            trace=[
                {
                    "stage": "planning",
                    "task": request.task,
                    "policy": profile.model_dump(mode="json"),
                }
            ],
        )

    @staticmethod
    def _coerce_year(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _missing_required_fields(plan: _CrawlPlan) -> list[str]:
        missing_fields: list[str] = []
        if not plan.url:
            missing_fields.append("url")
        if not plan.univ_slug:
            missing_fields.append("univ_slug")
        if plan.year <= 0:
            missing_fields.append("year")
        return missing_fields

    def _missing_fields_response(self, plan: _CrawlPlan, missing_fields: list[str]) -> AgentResponse:
        plan.trace.append({"stage": "missing_required_fields", "missing_fields": missing_fields})
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=plan.trace,
            output={
                "task": plan.request.task,
                "mode": self.name,
                **plan.payload,
                "requires_user_input": True,
                "missing_fields": missing_fields,
                "prompt": "请补充必填字段后重试。",
            },
        )

    async def _detect_page_type(self, plan: _CrawlPlan) -> tuple[str, dict[str, Any]]:
        detected_page_type = plan.page_type_hint
        analysis_result: dict[str, Any] = {}

        if plan.page_type_hint == "detail":
            return detected_page_type, analysis_result

        try:
            analysis_result = await analyze_url_candidates(
                url=plan.url,
                page_type_hint=plan.page_type_hint,
                html_content=None,
                browser_provider=plan.profile.prefer_browser_provider,
                client_id=self._request_client_id(plan.request),
                strict_client=False,
                use_internal_llm=False,
            )
            if plan.page_type_hint == "auto":
                detected_page_type = str(analysis_result.get("page_type") or "detail").strip().lower()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "[Agent] Pre-analysis failed: %s — falling back to URL heuristic",
                exc,
            )
            # Mark client as failed so downstream flows skip the client
            # browser and avoid a second 45-second timeout.
            plan.browser_provider_override = "server"
            if plan.page_type_hint == "auto":
                detected_page_type = self._infer_page_type_from_url(plan.url)
                logger.info("[Agent] URL heuristic result: %s", detected_page_type)
            else:
                detected_page_type = plan.page_type_hint

        return detected_page_type, analysis_result

    # -- URL heuristic for fallback page-type detection -----------------------

    _INDEX_URL_KEYWORDS: tuple[str, ...] = (
        "programmes",
        "programs",
        "courses",
        "find-your",
        "find-a-",
        "all-courses",
        "all-programs",
        "all-programmes",
        "course-list",
        "program-list",
        "programme-list",
        "our-courses",
        "our-programmes",
        "browse",
        "catalog",
        "catalogue",
        "directory",
        "listing",
        "course-finder",
        "programme-finder",
        "program-finder",
        "masters-courses",
        "master-programs",
    )

    @classmethod
    def _infer_page_type_from_url(cls, url: str) -> str:
        """Heuristic page-type inference from URL path patterns.

        Returns ``"index"`` if the URL strongly looks like a program listing,
        ``"auto"`` otherwise (letting the pipeline decide).
        """
        url_lower = url.lower()
        if any(kw in url_lower for kw in cls._INDEX_URL_KEYWORDS):
            return "index"
        # When uncertain, return "auto" so the pipeline can decide —
        # never blindly default to "detail" for an unknown page.
        return "auto"

    @staticmethod
    def _request_client_id(request: AgentRequest) -> str | None:
        if not isinstance(request.context, dict):
            return None
        value = str(request.context.get("client_id") or "").strip()
        return value or None

    @staticmethod
    def _effective_browser_provider(plan: _CrawlPlan) -> str:
        """Return browser provider, honouring any override from failed client attempts."""
        return plan.browser_provider_override or plan.profile.prefer_browser_provider

    async def _run_detail_flow(self, plan: _CrawlPlan, detected_page_type: str) -> AgentResponse:
        logger.info("[Agent] Detail flow: crawling %s", plan.url)
        plan.trace.append({"stage": "executing_detail_flow", "task": plan.request.task})
        crawl_result = await crawl_url(
            url=plan.url,
            univ_slug=plan.univ_slug,
            year=plan.year,
            page_type_hint="detail" if detected_page_type not in {"index", "detail"} else detected_page_type,
            browser_provider=self._effective_browser_provider(plan),
            strict_client=False,
            candidate_taxonomy_filter_enabled=True,
            candidate_taxonomy_filter_threshold=plan.profile.taxonomy_keep_threshold,
            candidate_taxonomy_filter_top_k=plan.profile.auto_run_max_candidates,
        )
        logger.info(
            "[Agent] Detail flow completed: imported=%d programs",
            crawl_result.imported_count or 0,
        )
        output = crawl_result.model_dump(mode="json")
        output.update(
            {
                "detected_page_type": detected_page_type,
                "auto_processed_count": output.get("imported_count") or 0,
                "onhold_count": 0,
                "onhold_items": [],
                "discarded_count": 0,
                "policy_warnings": plan.merged_policy.warnings,
                "request_payload": self._request_payload(plan),
            }
        )
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=plan.trace,
            output=output,
        )

    async def _run_pipeline_flow(self, plan: _CrawlPlan, page_type_hint: str) -> AgentResponse:
        """Fallback flow delegating to the full ingestion pipeline.

        Used when pre-analysis failed (no HTML, browser timeout, etc.)
        so we have no link list for the agent's ranking logic.  The
        pipeline has its own robust HTML fetching (with retries) and
        page-type auto-detection, so it can recover where the
        lightweight ``analyze_url_candidates`` could not.
        """
        effective_hint = page_type_hint if page_type_hint in {"index", "detail"} else "auto"
        logger.info(
            "[Agent] Pipeline flow: calling crawl_url with hint=%s for %s",
            effective_hint,
            plan.url,
        )
        plan.trace.append({
            "stage": "pipeline_delegated_flow",
            "page_type_hint": effective_hint,
            "reason": "pre-analysis unavailable",
        })

        crawl_result = await crawl_url(
            url=plan.url,
            univ_slug=plan.univ_slug,
            year=plan.year,
            page_type_hint=effective_hint,
            browser_provider=self._effective_browser_provider(plan),
            strict_client=False,
            candidate_taxonomy_filter_enabled=True,
            candidate_taxonomy_filter_threshold=plan.profile.taxonomy_keep_threshold,
            candidate_taxonomy_filter_top_k=plan.profile.auto_run_max_candidates,
        )
        logger.info(
            "[Agent] Pipeline flow completed: imported=%d programs",
            crawl_result.imported_count or 0,
        )

        output = crawl_result.model_dump(mode="json")
        output.update(
            {
                "detected_page_type": effective_hint,
                "auto_processed_count": output.get("imported_count") or 0,
                "onhold_count": 0,
                "onhold_items": [],
                "discarded_count": 0,
                "policy_warnings": plan.merged_policy.warnings,
                "request_payload": self._request_payload(plan),
            }
        )
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=plan.trace,
            output=output,
        )

    @staticmethod
    def _is_autonomous(request: AgentRequest) -> bool:
        """Check whether the runtime should operate autonomously.

        When ``False`` (default for MCP callers), index flows return
        candidate lists for the external LLM to review instead of
        auto-crawling internally.
        """
        if not isinstance(request.context, dict):
            return False
        return bool(request.context.get("autonomous"))

    async def _run_index_flow(self, plan: _CrawlPlan, analysis_result: dict[str, Any]) -> AgentResponse:
        plan.trace.append({"stage": "executing_index_flow", "task": plan.request.task})
        links = [item for item in (analysis_result.get("links") or []) if isinstance(item, dict)]
        logger.info("[Agent] Index flow: found %d candidate links", len(links))

        pipeline = IngestionPipeline()
        ranked_candidates = pipeline.rank_index_candidates(
            links,
            keep_threshold=plan.profile.taxonomy_keep_threshold,
            auto_run_threshold=plan.profile.taxonomy_auto_threshold,
            top_k=plan.profile.auto_run_max_candidates,
        )
        logger.info(
            "[Agent] Candidate ranking complete: %d candidates (threshold=%.2f, auto_threshold=%.2f)",
            len(ranked_candidates),
            plan.profile.taxonomy_keep_threshold,
            plan.profile.taxonomy_auto_threshold,
        )

        autonomous = self._is_autonomous(plan.request)
        logger.info("[Agent] Autonomous mode: %s", autonomous)

        if not autonomous:
            # External-LLM-driven mode: return all candidates for the
            # calling LLM to review and selectively crawl via
            # crawl / crawl_detail_batch.
            logger.info(
                "[Agent] Non-autonomous mode: returning %d candidates for external review",
                len(ranked_candidates),
            )
            plan.trace.append({"stage": "returning_candidates_for_review", "candidate_count": len(ranked_candidates)})
            return AgentResponse(
                status="wait_user_selection",
                runtime_used=self.name,
                trace=plan.trace,
                output={
                    "request_payload": self._request_payload(plan),
                    "detected_page_type": "index",
                    "analysis": {
                        "total_found": int(analysis_result.get("total_found") or len(links)),
                        "candidate_count": len(links),
                    },
                    "candidates": ranked_candidates,
                    "auto_processed_count": 0,
                    "onhold_count": len(ranked_candidates),
                    "discarded_count": self._count_discarded_links(links, ranked_candidates),
                    "policy_warnings": plan.merged_policy.warnings,
                    "next_action_hint": (
                        "Review the candidates list, then call crawl or "
                        "crawl_detail_batch with the selected URLs to ingest."
                    ),
                },
            )

        # Autonomous mode: auto-crawl candidates above threshold
        auto_candidates = [item for item in ranked_candidates if bool(item.get("auto_run_eligible"))]
        onhold_items = build_onhold_items(self._build_onhold_rows(ranked_candidates))
        logger.info(
            "[Agent] Autonomous mode: %d auto-eligible candidates, %d onhold items",
            len(auto_candidates),
            len(onhold_items),
        )

        if auto_candidates:
            logger.info("[Agent] Starting auto-crawl of %d candidates...", len(auto_candidates))
        auto_result_payload = await self._run_auto_candidates(plan, auto_candidates)
        if auto_candidates:
            logger.info(
                "[Agent] Auto-crawl completed: imported=%d programs",
                auto_result_payload.get("imported_count", 0),
            )

        output = {
            "request_payload": self._request_payload(plan),
            "detected_page_type": "index",
            "analysis": {
                "total_found": int(analysis_result.get("total_found") or len(links)),
                "candidate_count": len(links),
            },
            "auto_processed_count": len(auto_candidates),
            "onhold_count": len(onhold_items),
            "discarded_count": self._count_discarded_links(links, ranked_candidates),
            "onhold_items": [item.model_dump(mode="json") for item in onhold_items],
            "policy_warnings": plan.merged_policy.warnings,
            "auto_crawl_result": auto_result_payload,
            "review_items": auto_result_payload.get("review_items") or [],
            "review_token": auto_result_payload.get("review_token"),
        }

        if onhold_items:
            logger.info("[Agent] Waiting for user selection on %d onhold items", len(onhold_items))
            plan.trace.append({"stage": "wait_user_selection", "onhold_count": len(onhold_items)})
            return AgentResponse(
                status="wait_user_selection",
                runtime_used=self.name,
                trace=plan.trace,
                output=output,
            )

        logger.info("[Agent] Index flow completed successfully")
        plan.trace.append({"stage": "finalizing", "onhold_count": 0})
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=plan.trace,
            output=output,
        )

    async def _run_auto_candidates(
        self,
        plan: _CrawlPlan,
        auto_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not auto_candidates:
            return {}

        selected_urls = [
            str(item.get("url") or "").strip()
            for item in auto_candidates
            if str(item.get("url") or "").strip()
        ]
        selected_link_texts = {
            str(item.get("url") or "").strip(): str(item.get("text") or "").strip()
            for item in auto_candidates
            if str(item.get("url") or "").strip() and str(item.get("text") or "").strip()
        }

        auto_result = await crawl_url(
            url=plan.url,
            univ_slug=plan.univ_slug,
            year=plan.year,
            page_type_hint="index",
            selected_urls=selected_urls,
            selected_link_texts=selected_link_texts,
            browser_provider=self._effective_browser_provider(plan),
            strict_client=False,
            candidate_taxonomy_filter_enabled=False,
        )
        return auto_result.model_dump(mode="json")

    @staticmethod
    def _build_onhold_rows(ranked_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for idx, item in enumerate(ranked_candidates):
            if bool(item.get("auto_run_eligible")):
                continue
            rows.append(
                {
                    "item_id": f"hold-{idx + 1}",
                    "url": str(item.get("url") or "").strip(),
                    "program_name_candidate": (
                        str(item.get("program_name_inferred") or "").strip() or None
                    ),
                    "confidence": float(item.get("taxonomy_score") or 0.0),
                    "hold_reason": "taxonomy_score_below_auto_threshold",
                }
            )
        return rows

    @staticmethod
    def _count_discarded_links(
        links: list[dict[str, Any]],
        ranked_candidates: list[dict[str, Any]],
    ) -> int:
        ranked_urls = {
            str(item.get("url") or "").strip()
            for item in ranked_candidates
            if str(item.get("url") or "").strip()
        }
        return sum(
            1
            for item in links
            if str(item.get("url") or "").strip()
            and str(item.get("url") or "").strip() not in ranked_urls
        )

    @staticmethod
    def _request_payload(plan: _CrawlPlan) -> dict[str, Any]:
        return {
            "url": plan.url,
            "univ_slug": plan.univ_slug,
            "year": plan.year,
            "page_type_hint": plan.page_type_hint,
            "policy_profile": plan.profile.model_dump(mode="json"),
        }
