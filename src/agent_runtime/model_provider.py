"""Model provider adapter for internal/external agent execution modes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class AgentConfigError(RuntimeError):
    """Raised when requested agent mode is disallowed or unsupported."""


@dataclass
class ResolvedModelClient:
    """Resolved model client payload used by runtime orchestration."""

    mode: str
    client: Any
    context: dict[str, Any] = field(default_factory=dict)


class ModelProviderAdapter:
    """Resolve model clients for internal/external execution modes."""

    def __init__(
        self,
        *,
        allow_internal: bool,
        allow_external: bool,
        internal_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.allow_internal = bool(allow_internal)
        self.allow_external = bool(allow_external)
        self.internal_factory = internal_factory

    def resolve(
        self,
        *,
        mode: str,
        external_context: dict[str, Any] | None = None,
    ) -> ResolvedModelClient:
        """Resolve concrete model client for the requested mode."""
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode == "internal":
            if not self.allow_internal:
                raise AgentConfigError("internal model disabled")

            factory = self.internal_factory or self._default_internal_factory
            return ResolvedModelClient(mode="internal", client=factory(), context={})

        if normalized_mode == "external":
            if not self.allow_external:
                raise AgentConfigError("external model disabled")
            context = dict(external_context or {})
            return ResolvedModelClient(mode="external", client=context, context=context)

        raise AgentConfigError(f"unsupported model mode: {mode}")

    @staticmethod
    def _default_internal_factory() -> Any:
        """Create the default server-side router for internal mode."""
        from src.agents.factory import create_router

        return create_router()
