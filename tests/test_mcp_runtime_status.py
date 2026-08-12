from __future__ import annotations

import pytest

from src.api import server
from src.services.crawler import CrawlResult


def test_runtime_status_reports_client_and_internal_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.api.server.client_registry.list_clients",
        lambda: [
            {
                "client_id": "client-without-browser",
                "capabilities": {"browser_automation": False},
            }
        ],
    )
    monkeypatch.setattr("src.api.server._internal_llm_available", lambda: True)

    payload = server.mcp_runtime_status()
    assert payload["client_available"] is False
    assert payload["client_count"] == 0
    assert payload["client_ids"] == []
    assert payload["internal_llm_available"] is True
    assert payload["default_browser_provider_resolved"] == "server"


@pytest.mark.asyncio
async def test_mcp_tools_return_resolved_browser_provider(monkeypatch) -> None:
    analyze_calls: list[dict] = []

    async def _fake_analyze_url_candidates(**_kwargs):
        analyze_calls.append(dict(_kwargs))
        return {
            "page_type": "index",
            "links": [{"url": "https://example.edu/p/1", "text": "P1"}],
            "total_found": 1,
        }

    async def _fake_crawl_url(**_kwargs):
        return CrawlResult(
            imported_count=1,
            univ_slug="uom",
            year=2026,
            ingestion_job_id="job-runtime-meta",
        )

    monkeypatch.setattr("src.api.server.analyze_url_candidates", _fake_analyze_url_candidates)
    monkeypatch.setattr("src.api.server.crawl_url", _fake_crawl_url)
    monkeypatch.setattr("src.api.server._has_available_client", lambda *_args, **_kwargs: False)

    analyze_result = await server.mcp_analyze(
        url="https://example.edu/index",
        browser_provider="auto",
    )
    crawl_result = await server.mcp_crawl(
        url="https://example.edu/index",
        univ_slug="uom",
        year=2026,
        browser_provider="auto",
    )

    assert analyze_result["resolved_browser_provider"] == "server"
    assert analyze_result["client_id_used"] is None
    assert crawl_result["resolved_browser_provider"] == "server"
    assert crawl_result["client_id_used"] is None
    assert len(analyze_calls) >= 1
    # No use_internal_llm knob anymore — MCP tools always use the server's
    # configured LLM; there's only one calling convention to verify.
    assert "use_internal_llm" not in analyze_calls[0]


@pytest.mark.asyncio
async def test_mcp_analyze_returns_client_id_guidance(monkeypatch) -> None:
    async def _fake_analyze_url_candidates(**_kwargs):
        return {
            "page_type": "index",
            "links": [],
            "total_found": 0,
        }

    monkeypatch.setattr("src.api.server.analyze_url_candidates", _fake_analyze_url_candidates)
    monkeypatch.setattr(
        "src.api.server.client_registry.list_clients",
        lambda: [
            {
                "client_id": "client-123",
                "capabilities": {"browser_automation": True},
            }
        ],
    )

    result = await server.mcp_analyze(
        url="https://example.edu/index",
        client_id="edinburgh",
    )

    assert "runtime_status.client_ids" in result["client_id_expected_source"]
    assert "not an online browser client id" in result["client_id_warning"]


@pytest.mark.asyncio
async def test_mcp_analyze_normalizes_multilingual_page_type_hint(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_analyze_url_candidates(**kwargs):
        captured.update(kwargs)
        return {
            "page_type": "index",
            "links": [],
            "total_found": 0,
        }

    monkeypatch.setattr("src.api.server.analyze_url_candidates", _fake_analyze_url_candidates)

    result = await server.mcp_analyze(
        url="https://example.edu/index",
        page_type_hint="目录",
    )

    assert captured["page_type_hint"] == "index"
    assert result["page_type_hint_applied"] == "index"


@pytest.mark.asyncio
async def test_mcp_analyze_auto_requires_confirmation_and_next_steps(monkeypatch) -> None:
    async def _fake_analyze_url_candidates(**_kwargs):
        return {
            "page_type": "index",
            "links": [{"url": "https://example.edu/p/1", "text": "Program 1"}],
            "total_found": 1,
        }

    monkeypatch.setattr("src.api.server.analyze_url_candidates", _fake_analyze_url_candidates)

    result = await server.mcp_analyze(
        url="https://example.edu/list",
        page_type_hint="auto",
    )

    assert result["requires_user_confirmation"] is True
    assert "是否按 index 流程继续" in result["confirmation_prompt"]
    assert any(item["tool"] == "ingest" for item in result["next_step_options"])
    assert any(item["tool"] == "crawl_detail_batch" for item in result["next_step_options"])


@pytest.mark.asyncio
async def test_mcp_analyze_detail_next_steps_include_crawl(monkeypatch) -> None:
    async def _fake_analyze_url_candidates(**_kwargs):
        return {"page_type": "detail", "links": [], "total_found": 0}

    monkeypatch.setattr("src.api.server.analyze_url_candidates", _fake_analyze_url_candidates)

    result = await server.mcp_analyze(
        url="https://example.edu/detail/p1",
        page_type_hint="detail",
    )

    tools = {item["tool"] for item in result["next_step_options"]}
    assert tools == {"crawl"}  # single path now — no _internal_llm variant to choose
    assert result["requires_user_confirmation"] is False


@pytest.mark.asyncio
async def test_mcp_analyze_index_next_steps_include_ingest_and_crawl_detail_batch(monkeypatch) -> None:
    async def _fake_analyze_url_candidates(**_kwargs):
        return {
            "page_type": "index",
            "links": [{"url": "https://example.edu/p/1", "text": "Program 1"}],
            "total_found": 1,
        }

    monkeypatch.setattr("src.api.server.analyze_url_candidates", _fake_analyze_url_candidates)

    result = await server.mcp_analyze(
        url="https://example.edu/list",
        page_type_hint="index",
    )

    tools = {item["tool"] for item in result["next_step_options"]}
    assert tools == {"ingest", "crawl_detail_batch"}  # no _internal_llm variant to choose
    assert result["requires_user_confirmation"] is False
