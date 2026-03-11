"""Runtime factory for opt-in agent orchestration modes."""

from __future__ import annotations

import os
from typing import Any


def _resolve_runtime_mode(config: Any = None) -> str:
    configured_runtime = getattr(config, "runtime", None) if config is not None else None
    if configured_runtime is None or str(configured_runtime).strip() == "":
        configured_runtime = os.getenv("AGENT_RUNTIME", "legacy")
    return str(configured_runtime).strip().lower()


def build_agent_runtime(config: Any = None, bridge: Any = None, model_adapter: Any = None):
    """Build the runtime instance from configuration and environment."""
    mode = _resolve_runtime_mode(config)
    if mode == "pydanticai":
        from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime

        return PydanticAIRuntime(bridge=bridge, model_adapter=model_adapter)
    from src.agent_runtime.legacy_runtime import LegacyRuntime

    return LegacyRuntime(bridge=bridge, model_adapter=model_adapter)
