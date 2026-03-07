from __future__ import annotations

import pytest

import src.api.server as server
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
    async def _fake_analyze_url_candidates(**_kwargs):
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
