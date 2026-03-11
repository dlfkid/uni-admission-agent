"""PydanticAI runtime with low-confidence onhold orchestration and fallback."""

from __future__ import annotations

import logging
from typing import Any

from src.agent_runtime.base import AgentRequest, AgentResponse
from src.agent_runtime.legacy_runtime import LegacyRuntime
from src.agent_runtime.policy import merge_policy
from src.agent_runtime.review_models import build_onhold_items
from src.services.ingestion_pipeline import IngestionPipeline

logger = logging.getLogger(__name__)


async def analyze_url_candidates(**kwargs: Any) -> dict[str, Any]:
    """Lazy-import crawler helper to avoid runtime/crawler circular import."""
    from src.services import crawler as crawler_service

    return await crawler_service.analyze_url_candidates(**kwargs)


async def crawl_url(**kwargs: Any) -> Any:
    """Lazy-import crawl entrypoint to avoid runtime/crawler circular import."""
    from src.services import crawler as crawler_service

    return await crawler_service.crawl_url(**kwargs)


class PydanticAIRuntime:
    """Opt-in runtime using multi-step orchestration with guarded fallback."""

    name = "pydanticai"

    def __init__(
        self,
        bridge: Any = None,
        model_adapter: Any = None,
        fallback_runtime: LegacyRuntime | None = None,
    ) -> None:
        self.bridge = bridge
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
        if str(request.task or "").strip().lower() != "crawl":
            return AgentResponse(
                status="done",
                runtime_used=self.name,
                trace=[{"stage": "skip_non_crawl", "task": request.task}],
                output={"task": request.task, **dict(request.payload or {})},
            )

        payload = dict(request.payload or {})
        url = str(payload.get("url") or "").strip()
        univ_slug = str(payload.get("univ_slug") or "").strip().lower()
        page_type_hint = str(payload.get("page_type_hint") or "auto").strip().lower() or "auto"

        try:
            year = int(payload.get("year"))
        except (TypeError, ValueError):
            year = 0

        request_policy = payload.get("policy_profile")
        context_policy = request.context.get("policy_profile") if isinstance(request.context, dict) else None
        merged_policy = merge_policy(
            request_overrides=request_policy if isinstance(request_policy, dict) else {},
            client_policy=context_policy if isinstance(context_policy, dict) else {},
            server_defaults={},
        )
        profile = merged_policy.profile

        trace: list[dict[str, Any]] = [
            {
                "stage": "planning",
                "task": request.task,
                "policy": profile.model_dump(mode="json"),
            }
        ]

        missing_fields: list[str] = []
        if not url:
            missing_fields.append("url")
        if not univ_slug:
            missing_fields.append("univ_slug")
        if year <= 0:
            missing_fields.append("year")
        if missing_fields:
            trace.append({"stage": "missing_required_fields", "missing_fields": missing_fields})
            return AgentResponse(
                status="done",
                runtime_used=self.name,
                trace=trace,
                output={
                    "task": request.task,
                    "mode": self.name,
                    **payload,
                    "requires_user_input": True,
                    "missing_fields": missing_fields,
                    "prompt": "请补充必填字段后重试。",
                },
            )

        detected_page_type = page_type_hint
        analysis_result: dict[str, Any] = {}

        if page_type_hint != "detail":
            try:
                analysis_result = await analyze_url_candidates(
                    url=url,
                    page_type_hint=page_type_hint,
                    html_content=None,
                    browser_provider=profile.prefer_browser_provider,
                    client_id=(
                        str(request.context.get("client_id") or "").strip() or None
                        if isinstance(request.context, dict)
                        else None
                    ),
                    strict_client=False,
                    use_internal_llm=False,
                )
                if page_type_hint == "auto":
                    detected_page_type = str(analysis_result.get("page_type") or "detail").strip().lower()
            except Exception as exc:  # pylint: disable=broad-except
                logger.info("Agent pre-analysis unavailable, fallback to direct crawl path: %s", exc)
                detected_page_type = "detail" if page_type_hint == "auto" else page_type_hint

        if detected_page_type != "index":
            trace.append({"stage": "executing_detail_flow", "task": request.task})
            crawl_result = await crawl_url(
                url=url,
                univ_slug=univ_slug,
                year=year,
                page_type_hint="detail" if detected_page_type not in {"index", "detail"} else detected_page_type,
                browser_provider=profile.prefer_browser_provider,
                strict_client=False,
                candidate_taxonomy_filter_enabled=True,
                candidate_taxonomy_filter_threshold=profile.taxonomy_keep_threshold,
                candidate_taxonomy_filter_top_k=profile.auto_run_max_candidates,
            )
            output = crawl_result.model_dump(mode="json")
            output.update(
                {
                    "detected_page_type": detected_page_type,
                    "auto_processed_count": output.get("imported_count") or 0,
                    "onhold_count": 0,
                    "onhold_items": [],
                    "discarded_count": 0,
                    "policy_warnings": merged_policy.warnings,
                    "request_payload": {
                        "url": url,
                        "univ_slug": univ_slug,
                        "year": year,
                        "page_type_hint": page_type_hint,
                        "policy_profile": profile.model_dump(mode="json"),
                    },
                }
            )
            return AgentResponse(
                status="done",
                runtime_used=self.name,
                trace=trace,
                output=output,
            )

        trace.append({"stage": "executing_index_flow", "task": request.task})
        links = [item for item in (analysis_result.get("links") or []) if isinstance(item, dict)]
        pipeline = IngestionPipeline()
        ranked_candidates = pipeline.rank_index_candidates(
            links,
            keep_threshold=profile.taxonomy_keep_threshold,
            auto_run_threshold=profile.taxonomy_auto_threshold,
            top_k=profile.auto_run_max_candidates,
        )

        auto_candidates = [item for item in ranked_candidates if bool(item.get("auto_run_eligible"))]
        onhold_raw = [
            {
                "item_id": f"hold-{idx + 1}",
                "url": str(item.get("url") or "").strip(),
                "program_name_candidate": (
                    str(item.get("program_name_inferred") or "").strip() or None
                ),
                "confidence": float(item.get("taxonomy_score") or 0.0),
                "hold_reason": "taxonomy_score_below_auto_threshold",
            }
            for idx, item in enumerate(ranked_candidates)
            if not bool(item.get("auto_run_eligible"))
        ]
        onhold_items = build_onhold_items(onhold_raw)

        auto_result = None
        if auto_candidates:
            selected_urls = [str(item.get("url") or "").strip() for item in auto_candidates if str(item.get("url") or "").strip()]
            selected_link_texts = {
                str(item.get("url") or "").strip(): str(item.get("text") or "").strip()
                for item in auto_candidates
                if str(item.get("url") or "").strip() and str(item.get("text") or "").strip()
            }
            auto_result = await crawl_url(
                url=url,
                univ_slug=univ_slug,
                year=year,
                page_type_hint="index",
                selected_urls=selected_urls,
                selected_link_texts=selected_link_texts,
                browser_provider=profile.prefer_browser_provider,
                strict_client=False,
                candidate_taxonomy_filter_enabled=False,
            )

        ranked_urls = {
            str(item.get("url") or "").strip()
            for item in ranked_candidates
            if str(item.get("url") or "").strip()
        }
        discarded_count = sum(
            1
            for item in links
            if str(item.get("url") or "").strip()
            and str(item.get("url") or "").strip() not in ranked_urls
        )

        auto_result_payload = auto_result.model_dump(mode="json") if auto_result else {}
        output = {
            "request_payload": {
                "url": url,
                "univ_slug": univ_slug,
                "year": year,
                "page_type_hint": page_type_hint,
                "policy_profile": profile.model_dump(mode="json"),
            },
            "detected_page_type": "index",
            "analysis": {
                "total_found": int(analysis_result.get("total_found") or len(links)),
                "candidate_count": len(links),
            },
            "auto_processed_count": len(auto_candidates),
            "onhold_count": len(onhold_items),
            "discarded_count": discarded_count,
            "onhold_items": [item.model_dump(mode="json") for item in onhold_items],
            "policy_warnings": merged_policy.warnings,
            "auto_crawl_result": auto_result_payload,
            "review_items": auto_result_payload.get("review_items") or [],
            "review_token": auto_result_payload.get("review_token"),
        }
        if onhold_items:
            trace.append({"stage": "wait_user_selection", "onhold_count": len(onhold_items)})
            return AgentResponse(
                status="wait_user_selection",
                runtime_used=self.name,
                trace=trace,
                output=output,
            )

        trace.append({"stage": "finalizing", "onhold_count": 0})
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=trace,
            output=output,
        )
