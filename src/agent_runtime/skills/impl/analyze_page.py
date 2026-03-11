"""Analyze-page skill implementation."""

from __future__ import annotations

from src.agent_bridge.contracts import AnalyzeInput
from src.agent_bridge.serve_tool_bridge import ServeToolBridge
from src.agent_runtime.skills.contracts import AnalyzePageSkillInput


def analyze_page_skill_handler(payload: AnalyzePageSkillInput, bridge: ServeToolBridge) -> dict:
    """Run page analysis through the serve bridge."""
    output = bridge.analyze_page(
        AnalyzeInput(
            url=payload.url,
            page_type_hint=payload.page_type_hint,
            html_content=payload.html_content,
        )
    )
    return output.model_dump(mode="json")
