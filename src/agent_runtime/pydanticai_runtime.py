"""PydanticAI runtime with LLM-driven agent loop and fallback."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agent_runtime.base import AgentEvent, AgentRequest, AgentResponse, EventSink
from src.agent_runtime.legacy_runtime import LegacyRuntime
from src.agent_runtime.loop import agent_loop, AgentPageTimeout, PAGE_TIMEOUT, SYSTEM_PROMPT
from src.agent_runtime.summary_stream import generate_summary_with_stream
from src.agent_runtime.skills.registry import build_skill_registry

logger = logging.getLogger(__name__)


def _emit_event(event_sink: EventSink | None, event: AgentEvent) -> None:
    """Safely emit a runtime lifecycle event."""
    if event_sink is not None:
        event_sink(dict(event))


class PydanticAIRuntime:
    """Opt-in runtime using an LLM-driven agent loop.

    When a request arrives the runtime:
    1. Builds a natural-language message from the ``AgentRequest`` payload.
    2. Hands it (together with the SkillRegistry tools) to the agent loop.
    3. The loop calls the LLM, which decides which tools to invoke and when
       to stop — there is **no** hardcoded if/else orchestration.
    4. On any failure the runtime falls back to ``LegacyRuntime``.
    """

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
        except AgentPageTimeout:
            raise  # Do NOT fall back — timeouts are not runtime failures
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("pydanticai runtime failed, falling back: %s", exc)
            return await self.fallback_runtime.run(request)

    # ------------------------------------------------------------------
    # Core: LLM-driven agent loop
    # ------------------------------------------------------------------

    def _resolve_summary_provider(self, request: AgentRequest) -> Any:
        """Resolve optional text-summary provider without affecting core crawl flow."""
        provider = request.context.get("summary_provider")
        if provider is not None:
            return provider
        if self.model_adapter is None:
            return None
        try:
            return self.model_adapter.resolve(mode="internal")
        except Exception as exc:  # pylint: disable=broad-except
            logger.info("Summary provider unavailable, using one-shot fallback: %s", exc)
            return None

    async def _run_agent(self, request: AgentRequest) -> AgentResponse:
        """Hand the request to the agent loop and let the LLM drive."""
        logger.info(
            "[Agent] Starting LLM-driven loop for task=%s", request.task
        )

        user_message = self._build_user_message(request)
        registry = build_skill_registry()
        hint = (request.payload or {}).get("page_type_hint")
        event_sink = request.context.get("event_sink")
        if not callable(event_sink):
            event_sink = None

        _emit_event(
            event_sink,
            {
                "type": "agent_started",
                "task": request.task,
                "page_type_hint": hint,
            },
        )

        system_prompt = SYSTEM_PROMPT
        if request.context.get("dry_run"):
            system_prompt += (
                "\n\nIMPORTANT: dry_run mode is enabled. "
                "Pass dry_run=true when calling persist_programs_skill "
                "so that no records are written to the database."
            )
        payload = dict(request.payload or {})

        try:
            result = await asyncio.wait_for(
                agent_loop(
                    user_message=user_message,
                    registry=registry,
                    system_prompt=system_prompt,
                    page_type_hint=hint,
                    event_sink=event_sink,
                    univ_slug=str(payload.get("univ_slug", "")),
                    year=int(payload.get("year", 0) or 0),
                    dry_run=bool(request.context.get("dry_run", False)),
                    auto_paginate=bool(payload.get("auto_paginate", False)),
                ),
                timeout=PAGE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise AgentPageTimeout(
                f"agent_loop exceeded {PAGE_TIMEOUT}s for task={request.task}"
            ) from None

        logger.info(
            "[Agent] Loop completed in %d iteration(s)",
            result.get("iterations", 0),
        )
        final_response = await generate_summary_with_stream(
            prompt=str(result.get("response", "") or ""),
            provider=self._resolve_summary_provider(request),
            fallback_text=str(result.get("response", "") or ""),
            event_sink=event_sink,
        )

        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=result.get("trace", []),
            output={
                "task": request.task,
                "agent_response": final_response,
                "iterations": result.get("iterations", 0),
                "parsed_programs": result.get("collected_programs", []),
                **dict(request.payload or {}),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_message(request: AgentRequest) -> str:
        """Convert an AgentRequest into a natural-language prompt for the LLM."""
        payload = dict(request.payload or {})
        task = str(request.task or "crawl").strip()

        if task == "crawl":
            url = payload.get("url", "")
            univ_slug = payload.get("univ_slug", "")
            year = payload.get("year", "")
            page_type_hint = payload.get("page_type_hint", "auto")

            parts = [
                f"Crawl admission programs from this URL: {url}",
                f"University slug: {univ_slug}",
                f"Academic year: {year}",
                f"Page type hint: {page_type_hint}",
            ]
            auto_paginate = payload.get("auto_paginate", False)
            if auto_paginate:
                max_pages = payload.get("max_pages")
                paginate_msg = "AUTO-PAGINATE REQUESTED: Use paginated_crawl_skill for this index page."
                if max_pages is not None:
                    paginate_msg += f" Set max_pages={max_pages}."
                parts.append(paginate_msg)
            return "\n".join(parts)

        if task == "chat":
            return str(payload.get("message", "")).strip()

        # Generic fallback for non-crawl tasks
        return f"Task: {task}\nPayload: {payload}"
