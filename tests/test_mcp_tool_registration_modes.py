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
        monkeypatch.setattr(
            "src.agents.factory.create_router",
            lambda: object(),
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


def test_base_tools_registered_without_internal_llm(monkeypatch) -> None:
    server_module = _reload_server_with_router_probe(
        monkeypatch,
        router_available=False,
    )
    tool_names = _list_mcp_tool_names(server_module)

    assert {
        "analyze",
        "crawl",
        "crawl_detail_batch",
        "db_query",
        "runtime_status",
        "program_patch",
        "program_patch_batch",
        "help",
    }.issubset(tool_names)
    assert "analyze_internal_llm" not in tool_names
    assert "crawl_internal_llm" not in tool_names
    assert "crawl_detail_batch_internal_llm" not in tool_names


def test_internal_llm_tools_registered_only_when_available(monkeypatch) -> None:
    server_without_internal = _reload_server_with_router_probe(
        monkeypatch,
        router_available=False,
    )
    tool_names_without_internal = _list_mcp_tool_names(server_without_internal)
    assert "analyze_internal_llm" not in tool_names_without_internal
    assert "crawl_internal_llm" not in tool_names_without_internal
    assert "crawl_detail_batch_internal_llm" not in tool_names_without_internal

    server_with_internal = _reload_server_with_router_probe(
        monkeypatch,
        router_available=True,
    )
    tool_names_with_internal = _list_mcp_tool_names(server_with_internal)
    assert {
        "analyze_internal_llm",
        "crawl_internal_llm",
        "crawl_detail_batch_internal_llm",
    }.issubset(tool_names_with_internal)
