"""Typed bridge contracts for agent-runtime integration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LinkCandidate(BaseModel):
    """One candidate link extracted during page analysis."""

    url: str
    text: str = ""


class AnalyzeInput(BaseModel):
    """Input payload for serve-side analysis wrapper."""

    url: str
    page_type_hint: str = "index"
    html_content: str = ""


class AnalyzeOutput(BaseModel):
    """Normalized analysis output for agent orchestration."""

    page_type: str
    links: list[LinkCandidate] = Field(default_factory=list)
    total_found: int = 0


class BrowserFetchInput(BaseModel):
    """Input payload for browser automation fetch wrapper."""

    url: str
    page_type_hint: str = "index"
    client_id: str | None = None


class BrowserFetchOutput(BaseModel):
    """Normalized browser automation fetch payload."""

    html_content: str | None = None
    detail_pages_batch: list[dict[str, Any]] = Field(default_factory=list)
    selected_urls: list[str] = Field(default_factory=list)
    selected_link_texts: dict[str, str] = Field(default_factory=dict)
    resolved_browser_provider: str | None = None
    client_id_used: str | None = None
