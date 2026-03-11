import pytest

from src.services import crawler
from src.services.crawler import CrawlResult


@pytest.mark.asyncio
async def test_confirm_onhold_processes_selected_indices_only(monkeypatch):
    captured: dict = {}

    async def _fake_crawl_url(**kwargs):
        captured.update(kwargs)
        return CrawlResult(
            imported_count=2,
            univ_slug="uom",
            year=2026,
            review_token="review-token-1",
            review_items=[{"program_id": 1}, {"program_id": 2}],
        )

    monkeypatch.setattr(crawler, "crawl_url", _fake_crawl_url)

    summary = await crawler.run_agent_review_confirmation(
        task_payload={"url": "https://x/index", "univ_slug": "uom", "year": 2026},
        onhold_items=[
            {
                "index": 1,
                "item_id": "hold-1",
                "source_url": "https://x/p1",
                "program_name_candidate": "Program One",
                "confidence": 0.85,
                "hold_reason": "low_confidence",
            },
            {
                "index": 2,
                "item_id": "hold-2",
                "source_url": "https://x/p2",
                "program_name_candidate": "Program Two",
                "confidence": 0.74,
                "hold_reason": "low_confidence",
            },
            {
                "index": 3,
                "item_id": "hold-3",
                "source_url": "https://x/p3",
                "program_name_candidate": "Program Three",
                "confidence": 0.61,
                "hold_reason": "low_confidence",
            },
        ],
        selected_indices=[3, 1],
    )

    assert summary["total_onhold"] == 3
    assert summary["selected_count"] == 2
    assert summary["discarded_count"] == 1
    assert summary["invalid_indices"] == []
    assert [item["index"] for item in summary["applied_items"]] == [1, 3]
    assert [item["index"] for item in summary["discarded_items"]] == [2]
    assert summary["applied_result"]["imported_count"] == 2

    assert captured["url"] == "https://x/index"
    assert captured["univ_slug"] == "uom"
    assert captured["year"] == 2026
    assert captured["page_type_hint"] == "index"
    assert captured["selected_urls"] == ["https://x/p1", "https://x/p3"]
    assert captured["selected_link_texts"] == {
        "https://x/p1": "Program One",
        "https://x/p3": "Program Three",
    }


@pytest.mark.asyncio
async def test_confirm_onhold_defaults_to_discard_when_no_valid_selection(monkeypatch):
    calls = {"crawl": 0}

    async def _fake_crawl_url(**kwargs):
        calls["crawl"] += 1
        del kwargs
        return CrawlResult(imported_count=999, univ_slug="uom", year=2026)

    monkeypatch.setattr(crawler, "crawl_url", _fake_crawl_url)

    summary = await crawler.run_agent_review_confirmation(
        task_payload={"url": "https://x/index", "univ_slug": "uom", "year": 2026},
        onhold_items=[
            {
                "index": 1,
                "item_id": "hold-1",
                "source_url": "https://x/p1",
                "confidence": 0.85,
                "hold_reason": "low_confidence",
            },
            {
                "index": 2,
                "item_id": "hold-2",
                "source_url": "https://x/p2",
                "confidence": 0.74,
                "hold_reason": "low_confidence",
            },
        ],
        selected_indices=[0, -2, 9],
    )

    assert summary["total_onhold"] == 2
    assert summary["selected_count"] == 0
    assert summary["discarded_count"] == 2
    assert summary["invalid_indices"] == [0, -2, 9]
    assert summary["applied_items"] == []
    assert [item["index"] for item in summary["discarded_items"]] == [1, 2]
    assert summary["applied_result"] == {}
    assert calls["crawl"] == 0
