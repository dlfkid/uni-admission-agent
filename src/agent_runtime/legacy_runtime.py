"""Legacy runtime preserving existing deterministic crawl execution path."""

from __future__ import annotations

from typing import Any

from src.agent_runtime.base import AgentRequest, AgentResponse


class LegacyRuntime:
    """Safe baseline runtime that mirrors non-agent request/response contract.

    The legacy runtime intentionally echoes payload shape for compatibility
    with existing callers while the typed orchestration runtime evolves.
    """

    name = "legacy"

    def __init__(self, bridge: Any = None, model_adapter: Any = None) -> None:
        self.bridge = bridge
        self.model_adapter = model_adapter

    async def run(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=[
                {
                    "stage": "legacy_execute",
                    "task": request.task,
                }
            ],
            output={
                "task": request.task,
                **dict(request.payload or {}),
                "warning": (
                    "LegacyRuntime executed: no orchestration performed.  "
                    "This may indicate a fallback from PydanticAIRuntime."
                ),
            },
        )
