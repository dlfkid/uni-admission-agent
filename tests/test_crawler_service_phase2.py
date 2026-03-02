import pytest

from src.services.crawler import crawl_url, resume_crawl_job


@pytest.mark.asyncio
async def test_crawl_url_uses_phase2_pipeline_for_default_depth(monkeypatch) -> None:
    class DummyPipeline:
        async def run_new_job(self, **kwargs):
            assert kwargs["univ_slug"] == "hku"
            assert kwargs["year"] == 2026
            assert callable(kwargs["event_callback"])
            return {
                "job_uid": "job123",
                "imported_count": 3,
                "univ_slug": "hku",
                "year": 2026,
            }

    monkeypatch.setattr("src.services.crawler.IngestionPipeline", DummyPipeline)

    result = await crawl_url(
        url="https://example.com",
        univ_slug="hku",
        year=2026,
        continue_depth=0,
        progress_callback=lambda _evt, _payload: None,
    )

    assert result.imported_count == 3
    assert result.ingestion_job_id == "job123"


@pytest.mark.asyncio
async def test_crawl_url_uses_pipeline_when_continue_depth_enabled(monkeypatch) -> None:
    class DummyPipeline:
        async def run_new_job(self, **kwargs):
            assert kwargs["continue_depth"] == 1
            return {
                "job_uid": "job_depth",
                "imported_count": 2,
                "univ_slug": "hku",
                "year": 2026,
            }

    monkeypatch.setattr("src.services.crawler.IngestionPipeline", DummyPipeline)

    result = await crawl_url(
        url="https://example.com",
        univ_slug="hku",
        year=2026,
        continue_depth=1,
    )

    assert result.imported_count == 2
    assert result.ingestion_job_id == "job_depth"


@pytest.mark.asyncio
async def test_resume_crawl_job_parses_stage_enum(monkeypatch) -> None:
    class DummyPipeline:
        async def resume_job(self, **kwargs):
            assert kwargs["job_uid"] == "job123"
            assert kwargs["resume_from_stage"].value == "validate_rules"
            return {
                "job_uid": "job123",
                "imported_count": 1,
                "univ_slug": "hku",
                "year": 2026,
            }

    monkeypatch.setattr("src.services.crawler.IngestionPipeline", DummyPipeline)

    result = await resume_crawl_job(
        job_uid="job123",
        resume_from_stage="validate_rules",
    )

    assert result.imported_count == 1
    assert result.ingestion_job_id == "job123"
