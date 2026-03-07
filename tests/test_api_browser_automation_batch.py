from src.api.schemas import CrawlRequest
from src.services.crawler import crawl_url

import pytest


def test_crawl_request_accepts_detail_pages_batch() -> None:
    payload = {
        "url": "https://index.example",
        "univ_slug": "ucl",
        "year": 2026,
        "page_type_hint": "index",
        "browser_automation_enabled": True,
        "detail_pages_batch": [
            {"url": "https://detail.example/1", "html_content": "<html>...</html>"}
        ],
        "batch_index": 1,
        "batch_total": 10,
    }
    model = CrawlRequest.model_validate(payload)
    assert model.browser_automation_enabled is True
    assert model.detail_pages_batch is not None
    assert model.detail_pages_batch[0].url == "https://detail.example/1"
    assert model.batch_index == 1
    assert model.batch_total == 10


@pytest.mark.asyncio
async def test_crawl_url_plumbs_detail_pages_batch(monkeypatch) -> None:
    class DummyPipeline:
        async def run_new_job(self, **kwargs):
            assert kwargs["browser_automation_enabled"] is True
            assert kwargs["batch_index"] == 2
            assert kwargs["batch_total"] == 3
            assert kwargs["detail_pages_batch"] == [
                {
                    "url": "https://detail.example/1",
                    "html_content": "<html>detail</html>",
                    "selected_anchor_text": "MSc AI",
                }
            ]
            return {
                "job_uid": "job-browser-batch",
                "imported_count": 1,
                "univ_slug": "ucl",
                "year": 2026,
            }

    monkeypatch.setattr("src.services.crawler.IngestionPipeline", DummyPipeline)

    result = await crawl_url(
        url="https://index.example",
        univ_slug="ucl",
        year=2026,
        page_type_hint="index",
        browser_automation_enabled=True,
        detail_pages_batch=[
            {
                "url": "https://detail.example/1",
                "html_content": "<html>detail</html>",
                "selected_anchor_text": "MSc AI",
            }
        ],
        batch_index=2,
        batch_total=3,
    )

    assert result.ingestion_job_id == "job-browser-batch"
