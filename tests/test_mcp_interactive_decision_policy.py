from __future__ import annotations

import pytest

from src.api import server
from src.services.crawler import CrawlResult


@pytest.mark.asyncio
async def test_crawl_requires_year_before_execution(monkeypatch) -> None:
    calls = {"crawl": 0}

    async def _fake_crawl_url(**_kwargs):
        calls["crawl"] += 1
        return CrawlResult(
            imported_count=0,
            univ_slug="uom",
            year=2026,
            ingestion_job_id="job-should-not-run",
        )

    monkeypatch.setattr("src.api.server.crawl_url", _fake_crawl_url)

    result = await server.mcp_crawl(
        url="https://example.edu/index",
        univ_slug="uom",
        year=None,
    )

    assert result["requires_user_input"] is True
    assert result["missing_fields"] == ["year"]
    assert "2026" in result["prompt"]
    assert calls["crawl"] == 0


@pytest.mark.asyncio
async def test_auto_ready_when_scores_high_and_count_le_10(monkeypatch) -> None:
    async def _fake_analyze_url_candidates(**_kwargs):
        return {
            "page_type": "index",
            "links": [
                {"url": "https://example.edu/p/1", "text": "Program 1"},
                {"url": "https://example.edu/p/2", "text": "Program 2"},
            ],
            "total_found": 2,
        }

    def _fake_rank_candidates(*, links, **_kwargs):  # pylint: disable=unused-argument
        return [
            {
                "url": links[0]["url"],
                "text": links[0]["text"],
                "taxonomy_score": 0.95,
                "program_name_inferred": "Program One",
            },
            {
                "url": links[1]["url"],
                "text": links[1]["text"],
                "taxonomy_score": 0.94,
                "program_name_inferred": "Program Two",
            },
        ]

    async def _fake_crawl_url(**_kwargs):
        return CrawlResult(
            imported_count=2,
            univ_slug="uom",
            year=2026,
            ingestion_job_id="job-auto-ready",
        )

    monkeypatch.setattr("src.api.server.analyze_url_candidates", _fake_analyze_url_candidates)
    monkeypatch.setattr(
        "src.api.server._rank_index_candidates_by_taxonomy",
        _fake_rank_candidates,
        raising=False,
    )
    monkeypatch.setattr("src.api.server.crawl_url", _fake_crawl_url)

    result = await server.mcp_crawl(
        url="https://example.edu/index",
        univ_slug="uom",
        year=2026,
        browser_provider="server",
    )

    assert result["auto_ready"] is True
    assert result["requires_user_review"] is False
    assert result["decision_reason"] == "auto_crawl_threshold_met"
    assert result["imported_count"] == 2


@pytest.mark.asyncio
async def test_review_required_when_count_gt_10_or_low_confidence(monkeypatch) -> None:
    async def _fake_analyze_url_candidates(**_kwargs):
        links = [
            {"url": f"https://example.edu/p/{idx}", "text": f"Program {idx}"}
            for idx in range(1, 12)
        ]
        return {"page_type": "index", "links": links, "total_found": len(links)}

    crawl_calls = {"count": 0}

    async def _fake_crawl_url(**_kwargs):
        crawl_calls["count"] += 1
        return CrawlResult(
            imported_count=11,
            univ_slug="uom",
            year=2026,
            ingestion_job_id="job-review-should-not-run",
        )

    monkeypatch.setattr("src.api.server.analyze_url_candidates", _fake_analyze_url_candidates)
    monkeypatch.setattr("src.api.server.crawl_url", _fake_crawl_url)

    def _rank_many_high(*, links, **_kwargs):  # pylint: disable=unused-argument
        return [
            {
                "url": item["url"],
                "text": item["text"],
                "taxonomy_score": 0.96,
                "program_name_inferred": item["text"],
            }
            for item in links
        ]

    monkeypatch.setattr(
        "src.api.server._rank_index_candidates_by_taxonomy",
        _rank_many_high,
        raising=False,
    )
    result_many = await server.mcp_crawl(
        url="https://example.edu/index",
        univ_slug="uom",
        year=2026,
    )
    assert result_many["auto_ready"] is False
    assert result_many["requires_user_review"] is True
    assert result_many["decision_reason"] == "candidate_count_exceeds_auto_limit"
    assert len(result_many["candidates"]) == 11
    assert crawl_calls["count"] == 0

    def _rank_low_confidence(*, links, **_kwargs):  # pylint: disable=unused-argument
        return [
            {
                "url": item["url"],
                "text": item["text"],
                "taxonomy_score": 0.83,
                "program_name_inferred": item["text"],
            }
            for item in links[:3]
        ]

    monkeypatch.setattr(
        "src.api.server._rank_index_candidates_by_taxonomy",
        _rank_low_confidence,
        raising=False,
    )
    result_low = await server.mcp_crawl(
        url="https://example.edu/index",
        univ_slug="uom",
        year=2026,
    )
    assert result_low["auto_ready"] is False
    assert result_low["requires_user_review"] is True
    assert result_low["decision_reason"] == "confidence_below_auto_threshold"
    assert len(result_low["candidates"]) == 3
    assert crawl_calls["count"] == 0

    def _rank_none(*, links, **_kwargs):  # pylint: disable=unused-argument
        _ = links
        return []

    monkeypatch.setattr(
        "src.api.server._rank_index_candidates_by_taxonomy",
        _rank_none,
        raising=False,
    )
    result_none = await server.mcp_crawl(
        url="https://example.edu/index",
        univ_slug="uom",
        year=2026,
    )
    assert result_none["auto_ready"] is False
    assert result_none["requires_user_review"] is True
    assert result_none["decision_reason"] == "no_candidates_above_keep_threshold"
    assert result_none["candidates"] == []
    assert result_none["page_type_hint_applied"] == "index"
    assert crawl_calls["count"] == 0


@pytest.mark.asyncio
async def test_crawl_respects_manual_page_type_hint_detail(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_crawl_url(**kwargs):
        captured.update(kwargs)
        return CrawlResult(
            imported_count=1,
            univ_slug="edinburgh",
            year=2026,
            ingestion_job_id="job-detail-hint",
        )

    monkeypatch.setattr("src.api.server.crawl_url", _fake_crawl_url)

    result = await server.mcp_crawl(
        url="https://example.edu/detail/program-1",
        univ_slug="edinburgh",
        year=2026,
        page_type_hint="detail",
    )

    assert captured["page_type_hint"] == "detail"
    assert result["page_type"] == "detail"
    assert result["imported_count"] == 1


@pytest.mark.asyncio
async def test_crawl_normalizes_multilingual_page_type_hint(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_crawl_url(**kwargs):
        captured.update(kwargs)
        return CrawlResult(
            imported_count=1,
            univ_slug="polyu",
            year=2026,
            ingestion_job_id="job-detail-zh",
        )

    monkeypatch.setattr("src.api.server.crawl_url", _fake_crawl_url)

    result = await server.mcp_crawl(
        url="https://example.edu/detail/program-1",
        univ_slug="polyu",
        year=2026,
        page_type_hint="细节",
    )

    assert captured["page_type_hint"] == "detail"
    assert result["page_type_hint_applied"] == "detail"
