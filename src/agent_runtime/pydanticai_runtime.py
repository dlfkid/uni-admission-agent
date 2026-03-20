"""PydanticAI runtime with LLM-driven agent loop and fallback."""

from __future__ import annotations

import logging
from typing import Any

from src.agent_runtime.base import AgentRequest, AgentResponse
from src.agent_runtime.legacy_runtime import LegacyRuntime
from src.agent_runtime.loop import agent_loop, AgentPageTimeout, PAGE_TIMEOUT, SYSTEM_PROMPT
from src.agent_runtime.skills.registry import build_skill_registry

logger = logging.getLogger(__name__)


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
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("pydanticai runtime failed, falling back: %s", exc)
            return await self.fallback_runtime.run(request)

    # ------------------------------------------------------------------
    # Core: LLM-driven agent loop
    # ------------------------------------------------------------------

    async def _run_agent(self, request: AgentRequest) -> AgentResponse:
        """Hand the request to the agent loop and let the LLM drive."""
        logger.info(
            "[Agent] Starting LLM-driven loop for task=%s", request.task
        )

        user_message = self._build_user_message(request)
        registry = build_skill_registry()
        hint = (request.payload or {}).get("page_type_hint")

        # Build system prompt with dry-run instruction if needed
        system_prompt = SYSTEM_PROMPT
        if request.context.get("dry_run"):
            system_prompt += (
                "\n\nIMPORTANT: dry_run mode is active. "
                "When calling persist_programs_skill, set dry_run=true "
                "in the payload. Do NOT attempt database writes. "
                "Do NOT use legacy_crawl_batch_skill — it bypasses dry-run "
                "and writes directly to the database. Instead use "
                "browser_automation_skill to fetch HTML, then "
                "persist_programs_skill with dry_run=true."
            )

        result = await agent_loop(
            user_message=user_message,
            registry=registry,
            system_prompt=system_prompt,
            page_type_hint=hint,
        )

        logger.info(
            "[Agent] Loop completed in %d iteration(s)",
            result.get("iterations", 0),
        )

        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=result.get("trace", []),
            output={
                "task": request.task,
                "agent_response": result.get("response", ""),
                "iterations": result.get("iterations", 0),
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
            return "\n".join(parts)

        # Generic fallback for non-crawl tasks
        return f"Task: {task}\nPayload: {payload}"
