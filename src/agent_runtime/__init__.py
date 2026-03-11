"""Agent runtime package."""

from typing import TYPE_CHECKING

from src.agent_runtime.base import AgentRequest, AgentResponse, AgentRuntime
from src.agent_runtime.review_models import (
    OnholdApplySummary,
    OnholdItem,
    OnholdReviewSummary,
)

if TYPE_CHECKING:
    from src.agent_runtime.legacy_runtime import LegacyRuntime
    from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime
    from src.agent_runtime.runtime_factory import build_agent_runtime

__all__ = [
    "AgentRequest",
    "AgentResponse",
    "AgentRuntime",
    "LegacyRuntime",
    "PydanticAIRuntime",
    "OnholdItem",
    "OnholdReviewSummary",
    "OnholdApplySummary",
    "build_agent_runtime",
]


def __getattr__(name: str):
    """Lazy export heavy runtime modules to avoid package import cycles."""
    if name == "LegacyRuntime":
        from src.agent_runtime.legacy_runtime import LegacyRuntime

        return LegacyRuntime
    if name == "PydanticAIRuntime":
        from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime

        return PydanticAIRuntime
    if name == "build_agent_runtime":
        from src.agent_runtime.runtime_factory import build_agent_runtime

        return build_agent_runtime
    raise AttributeError(f"module 'src.agent_runtime' has no attribute {name!r}")
