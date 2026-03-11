"""PydanticAI runtime skeleton with automatic legacy fallback."""

from __future__ import annotations

import logging
from typing import Any

from src.agent_runtime.base import AgentRequest, AgentResponse
from src.agent_runtime.legacy_runtime import LegacyRuntime

logger = logging.getLogger(__name__)


class PydanticAIRuntime:
    """Opt-in runtime using skill orchestration with guarded fallback."""

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
        """Execute one request through the pydanticai orchestration skeleton."""
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=[
                {
                    "stage": "planning",
                    "task": request.task,
                },
                {
                    "stage": "executing",
                    "task": request.task,
                },
            ],
            output={
                "task": request.task,
                "mode": self.name,
                **dict(request.payload or {}),
            },
        )
