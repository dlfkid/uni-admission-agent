"""LLM bootstrap prompt templates for adm-agent-client setup."""

from __future__ import annotations


def build_bootstrap_prompt(
    *,
    target: str,
    server_url: str,
) -> str:
    """Build one-shot setup prompt for a given LLM interaction environment."""
    normalized = str(target or "generic").strip().lower()
    if normalized not in {"codex", "claude", "openclaw", "generic"}:
        normalized = "generic"

    target_title = {
        "codex": "Codex CLI",
        "claude": "Claude Code",
        "openclaw": "OpenClaw",
        "generic": "generic LLM CLI/agent",
    }[normalized]

    style_hint = {
        "codex": "Use shell commands directly and verify outputs.",
        "claude": "Run commands step-by-step and summarize each result briefly.",
        "openclaw": "Assume user already interacts with OpenClaw via any interface.",
        "generic": "Use executable shell steps and show exact commands.",
    }[normalized]

    return (
        f"You are assisting a non-technical user using {target_title}. "
        "Set up adm-agent-client with minimal steps.\n\n"
        f"{style_hint}\n\n"
        "Do the following:\n"
        "1) Ensure the adm-agent-client binary is downloaded and executable.\n"
        "2) Run `adm-agent-client init` and provide:\n"
        f"   - Serve URL: {server_url}\n"
        "   - Client name: use a human-readable machine label\n"
        "3) Run `adm-agent-client status` and confirm config is loaded.\n"
        "4) Run `adm-agent-client start` and report connection status.\n"
        "5) If connection fails, diagnose host/port and local firewall first.\n"
        "6) For MCP crawl requests, ask user for only 4 fields:\n"
        "   - url (required)\n"
        "   - univ_slug (required, e.g. polyu)\n"
        "   - year (required, e.g. 2026)\n"
        "   - page_type (optional: auto/index/detail; default auto)\n"
        "7) Recognize multilingual page type expressions:\n"
        "   - index: index / list / listing / 索引 / 目录 / 列表\n"
        "   - detail: detail / details / 详情 / 细节 / 详细\n"
        "8) If user does not provide page_type, send `page_type_hint=\"auto\"`.\n"
    )
