"""Serve-side bridge wrappers used by agent runtime skills."""

from __future__ import annotations

from typing import Any, Callable

from src.agent_bridge.contracts import AnalyzeInput, AnalyzeOutput
from src.services.crawler import analyze_page_external

AnalyzeFn = Callable[[str, str, str], dict[str, Any]]


class ServeToolBridge:
    """Bridge adapter that wraps existing serve functions with typed contracts."""

    def __init__(self, analyze_fn: AnalyzeFn | None = None) -> None:
        self._analyze_fn = analyze_fn or analyze_page_external

    def analyze_page(self, payload: AnalyzeInput) -> AnalyzeOutput:
        raw = self._analyze_fn(payload.url, payload.html_content, payload.page_type_hint)
        return AnalyzeOutput.model_validate(raw)
