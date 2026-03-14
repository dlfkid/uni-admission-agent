"""Agent runtime primitives and typed request/response contracts."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    """Generic request envelope consumed by runtimes."""

    task: str = Field(default="crawl")
    payload: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    """Generic response envelope produced by runtimes."""

    status: str = Field(default="done")
    runtime_used: str = Field(default="legacy")
    trace: list[dict[str, Any]] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)


class AgentRuntime(Protocol):
    """Runtime contract used by factory and orchestrators."""

    name: str

    async def run(self, request: AgentRequest) -> AgentResponse:
        """Execute one agent request."""
