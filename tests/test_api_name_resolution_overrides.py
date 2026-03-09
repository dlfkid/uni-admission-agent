from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.api.schemas import CrawlRequest
from src.services.crawler import crawl_url
from src.services.ingestion_pipeline import IngestionPipeline


def test_crawl_request_accepts_name_resolution_overrides() -> None:
    body = CrawlRequest(
        url="https://example.com",
        univ_slug="leeds",
        year=2026,
        name_resolution_llm_enabled=True,
        name_resolution_low_threshold=0.8,
        name_resolution_conflict_delta=0.05,
    )
    assert body.name_resolution_llm_enabled is True


def test_crawl_request_rejects_invalid_name_resolution_threshold() -> None:
    with pytest.raises(ValidationError):
        CrawlRequest(
            url="https://example.com",
            univ_slug="leeds",
            year=2026,
            name_resolution_low_threshold=1.2,
        )


@pytest.mark.asyncio
async def test_crawl_url_plumbs_name_resolution_overrides(monkeypatch) -> None:
    class DummyPipeline:
        async def run_new_job(self, **kwargs):
            assert kwargs["name_resolution_llm_enabled"] is True
            assert kwargs["name_resolution_low_threshold"] == 0.81
            assert kwargs["name_resolution_conflict_delta"] == 0.07
            return {
                "job_uid": "job-name-resolution",
                "imported_count": 1,
                "univ_slug": "leeds",
                "year": 2026,
            }

    monkeypatch.setattr("src.services.crawler.IngestionPipeline", DummyPipeline)

    result = await crawl_url(
        url="https://example.com",
        univ_slug="leeds",
        year=2026,
        name_resolution_llm_enabled=True,
        name_resolution_low_threshold=0.81,
        name_resolution_conflict_delta=0.07,
    )

    assert result.ingestion_job_id == "job-name-resolution"


@pytest.mark.asyncio
async def test_ingestion_payload_contains_name_resolution_overrides(monkeypatch) -> None:
    pipeline = IngestionPipeline(db_manager=MagicMock())
    captured_payload = {}

    def fake_create_job(request_payload):
        captured_payload.update(request_payload)
        return "job-name-resolution"

    async def fake_run_job(**_kwargs):
        return {
            "job_uid": "job-name-resolution",
            "imported_count": 0,
            "univ_slug": "leeds",
            "year": 2026,
        }

    monkeypatch.setattr(pipeline, "_create_job", fake_create_job)
    monkeypatch.setattr(pipeline, "_run_job", fake_run_job)

    await pipeline.run_new_job(
        url="https://example.com",
        univ_slug="leeds",
        year=2026,
        name_resolution_llm_enabled=True,
        name_resolution_low_threshold=0.8,
        name_resolution_conflict_delta=0.05,
    )

    assert captured_payload["name_resolution_llm_enabled"] is True
    assert captured_payload["name_resolution_low_threshold"] == 0.8
    assert captured_payload["name_resolution_conflict_delta"] == 0.05
