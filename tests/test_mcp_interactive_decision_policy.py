from __future__ import annotations

import pytest

import src.api.server as server
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
