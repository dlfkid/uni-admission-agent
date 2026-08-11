from __future__ import annotations

import importlib
from typing import Any

import anyio


def _reload_server_with_router_probe(
    monkeypatch,
    *,
    router_available: bool,
):
    if router_available:
        def _create_router_probe() -> Any:
            return object()

        monkeypatch.setattr(
            "src.agents.factory.create_router",
            _create_router_probe,
        )
    else:
        def _raise_unavailable() -> Any:
            raise RuntimeError("internal llm unavailable")

        monkeypatch.setattr("src.agents.factory.create_router", _raise_unavailable)

    import src.api.server as server_module

    return importlib.reload(server_module)


def _list_mcp_tool_names(server_module) -> set[str]:
    async def _collect() -> set[str]:
        tools = await server_module.mcp.list_tools()
        return {tool.name for tool in tools}

    return anyio.run(_collect)


_BASE_TOOL_NAMES = {
    "analyze",
    "crawl",
    "crawl_detail_batch",
    "ingest",
    "db_query",
    "runtime_status",
    "program_patch",
    "program_patch_batch",
    "help",
}

# The MCP server used to register a parallel "*_internal_llm" toolset,
# doubling the tool surface. 7 of 9 variants were byte-identical aliases
# with zero behavioral difference from their base counterpart; only
# analyze/crawl actually branched (server LLM vs. a deterministic
# heuristic — never actually "the caller's LLM does it", despite the
# naming). That whole toolset was removed: MCP tools now always use the
# server's configured LLM, with a single name per capability. These tests
# lock in that the alias tools are gone for good, regardless of whether an
# internal LLM happens to be configured (`internal_llm_available` is still
# a real, useful diagnostic field on `runtime_status` — it just no longer
# gates a second copy of every tool).
_REMOVED_INTERNAL_LLM_TOOL_NAMES = {
    "analyze_internal_llm",
    "crawl_internal_llm",
    "crawl_detail_batch_internal_llm",
    "ingest_internal_llm",
    "db_query_internal_llm",
    "runtime_status_internal_llm",
    "program_patch_internal_llm",
    "program_patch_batch_internal_llm",
    "help_internal_llm",
}


def test_base_tools_always_registered(monkeypatch) -> None:
    server_module = _reload_server_with_router_probe(
        monkeypatch,
        router_available=False,
    )
    tool_names = _list_mcp_tool_names(server_module)

    assert _BASE_TOOL_NAMES.issubset(tool_names)
    assert not _REMOVED_INTERNAL_LLM_TOOL_NAMES & tool_names


def test_no_internal_llm_variant_tools_exist_regardless_of_router_availability(
    monkeypatch,
) -> None:
    server_without_internal = _reload_server_with_router_probe(
        monkeypatch,
        router_available=False,
    )
    tool_names_without_internal = _list_mcp_tool_names(server_without_internal)
    assert not _REMOVED_INTERNAL_LLM_TOOL_NAMES & tool_names_without_internal
    assert _BASE_TOOL_NAMES.issubset(tool_names_without_internal)

    server_with_internal = _reload_server_with_router_probe(
        monkeypatch,
        router_available=True,
    )
    tool_names_with_internal = _list_mcp_tool_names(server_with_internal)
    assert not _REMOVED_INTERNAL_LLM_TOOL_NAMES & tool_names_with_internal
    assert _BASE_TOOL_NAMES.issubset(tool_names_with_internal)


def test_agent_tools_registered_only_when_agent_enabled(monkeypatch) -> None:
    # Agent defaults to enabled; explicitly disable to test the "off" branch.
    monkeypatch.setenv("AGENT_ENABLED", "false")
    server_without_agent = _reload_server_with_router_probe(
        monkeypatch,
        router_available=False,
    )
    tool_names_without_agent = _list_mcp_tool_names(server_without_agent)
    assert "agent_run" not in tool_names_without_agent
    assert "agent_review_confirm" not in tool_names_without_agent

    monkeypatch.setenv("AGENT_ENABLED", "true")
    server_with_agent = _reload_server_with_router_probe(
        monkeypatch,
        router_available=False,
    )
    tool_names_with_agent = _list_mcp_tool_names(server_with_agent)
    assert "agent_run" in tool_names_with_agent
    assert "agent_review_confirm" in tool_names_with_agent


def test_agent_tools_can_register_after_env_enabled_post_import(monkeypatch) -> None:
    # Agent defaults to enabled; explicitly disable so the "before enable" branch is testable.
    monkeypatch.setenv("AGENT_ENABLED", "false")
    server_module = _reload_server_with_router_probe(
        monkeypatch,
        router_available=False,
    )
    tool_names_before = _list_mcp_tool_names(server_module)
    assert "agent_run" not in tool_names_before
    assert "agent_review_confirm" not in tool_names_before

    monkeypatch.setenv("AGENT_ENABLED", "true")
    server_module._register_agent_mcp_tools_if_enabled()  # pylint: disable=protected-access

    tool_names_after = _list_mcp_tool_names(server_module)
    assert "agent_run" in tool_names_after
    assert "agent_review_confirm" in tool_names_after
