from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.api.schemas import CrawlRequest
from src.services.crawler import crawl_url
from src.services.ingestion_pipeline import IngestionPipeline


def test_crawl_request_accepts_taxonomy_overrides() -> None:
    body = CrawlRequest(
        url="https://example.com",
        univ_slug="polyu",
        year=2026,
        taxonomy_enabled=True,
        taxonomy_low_threshold=0.8,
        taxonomy_high_threshold=0.92,
        taxonomy_hint_top_k=3,
        taxonomy_override_enabled=True,
    )
    assert body.taxonomy_hint_top_k == 3


def test_crawl_request_rejects_invalid_threshold_order() -> None:
    with pytest.raises(ValidationError):
        CrawlRequest(
            url="https://example.com",
            univ_slug="polyu",
            year=2026,
            taxonomy_low_threshold=0.95,
            taxonomy_high_threshold=0.9,
        )


@pytest.mark.asyncio
async def test_crawl_url_plumbs_taxonomy_overrides(monkeypatch) -> None:
    class DummyPipeline:
        async def run_new_job(self, **kwargs):
            assert kwargs["taxonomy_enabled"] is True
            assert kwargs["taxonomy_low_threshold"] == 0.81
            assert kwargs["taxonomy_high_threshold"] == 0.93
            assert kwargs["taxonomy_hint_top_k"] == 4
            assert kwargs["taxonomy_override_enabled"] is False
            return {
                "job_uid": "job-taxonomy",
                "imported_count": 1,
                "univ_slug": "polyu",
                "year": 2026,
            }

    monkeypatch.setattr("src.services.crawler.IngestionPipeline", DummyPipeline)

    result = await crawl_url(
        url="https://example.com",
        univ_slug="polyu",
        year=2026,
        taxonomy_enabled=True,
        taxonomy_low_threshold=0.81,
        taxonomy_high_threshold=0.93,
        taxonomy_hint_top_k=4,
        taxonomy_override_enabled=False,
    )

    assert result.ingestion_job_id == "job-taxonomy"


@pytest.mark.asyncio
async def test_ingestion_payload_contains_taxonomy_overrides(monkeypatch) -> None:
    pipeline = IngestionPipeline(db_manager=MagicMock())
    captured_payload = {}

    def fake_create_job(request_payload):
        captured_payload.update(request_payload)
        return "job-taxonomy"

    async def fake_run_job(**_kwargs):
        return {
            "job_uid": "job-taxonomy",
            "imported_count": 0,
            "univ_slug": "polyu",
            "year": 2026,
        }

    monkeypatch.setattr(pipeline, "_create_job", fake_create_job)
    monkeypatch.setattr(pipeline, "_run_job", fake_run_job)

    await pipeline.run_new_job(
        url="https://example.com",
        univ_slug="polyu",
        year=2026,
        taxonomy_enabled=True,
        taxonomy_low_threshold=0.8,
        taxonomy_high_threshold=0.92,
        taxonomy_hint_top_k=3,
        taxonomy_override_enabled=True,
    )

    assert captured_payload["taxonomy_enabled"] is True
    assert captured_payload["taxonomy_low_threshold"] == 0.8
    assert captured_payload["taxonomy_high_threshold"] == 0.92
    assert captured_payload["taxonomy_hint_top_k"] == 3
    assert captured_payload["taxonomy_override_enabled"] is True
