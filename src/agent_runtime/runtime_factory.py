"""Runtime factory for opt-in agent orchestration modes."""

from __future__ import annotations

import os
from typing import Any

from src.agent_runtime.base import AgentRequest, AgentResponse


class LegacyRuntime:
    """Safe baseline runtime that preserves current behavior."""

    name = "legacy"

    def __init__(self, bridge: Any = None, model_adapter: Any = None) -> None:
        self.bridge = bridge
        self.model_adapter = model_adapter

    async def run(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=[],
            output={"task": request.task},
        )


class PydanticAIRuntime:
    """Placeholder runtime selected when AGENT_RUNTIME=pydanticai."""

    name = "pydanticai"

    def __init__(self, bridge: Any = None, model_adapter: Any = None) -> None:
        self.bridge = bridge
        self.model_adapter = model_adapter

    async def run(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            status="done",
            runtime_used=self.name,
            trace=[],
            output={"task": request.task},
        )


def _resolve_runtime_mode(config: Any = None) -> str:
    configured_runtime = getattr(config, "runtime", None) if config is not None else None
    if configured_runtime is None or str(configured_runtime).strip() == "":
        configured_runtime = os.getenv("AGENT_RUNTIME", "legacy")
    return str(configured_runtime).strip().lower()


def build_agent_runtime(config: Any = None, bridge: Any = None, model_adapter: Any = None):
    """Build the runtime instance from configuration and environment."""
    mode = _resolve_runtime_mode(config)
    if mode == "pydanticai":
        return PydanticAIRuntime(bridge=bridge, model_adapter=model_adapter)
    return LegacyRuntime(bridge=bridge, model_adapter=model_adapter)
