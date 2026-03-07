import pytest

from src.services.crawler import crawl_url


@pytest.mark.asyncio
async def test_crawl_url_auto_uses_client_when_available(monkeypatch) -> None:
    class DummyPipeline:
        async def run_new_job(self, **kwargs):
            assert kwargs["detail_pages_batch"] == [
                {
                    "url": "https://example.edu/detail-1",
                    "html_content": "<html>detail-1</html>",
                    "selected_anchor_text": "MSc AI",
                }
            ]
            return {
                "job_uid": "job-1",
                "imported_count": 1,
                "univ_slug": "uom",
                "year": 2026,
            }

    async def _fake_fetch_client(**_kwargs):
        return {
            "detail_pages_batch": [
                {
                    "url": "https://example.edu/detail-1",
                    "html_content": "<html>detail-1</html>",
                    "selected_anchor_text": "MSc AI",
                }
            ]
        }

    monkeypatch.setattr("src.services.crawler.IngestionPipeline", DummyPipeline)
    monkeypatch.setattr("src.services.browser_provider.has_available_client", lambda *_: True)
    monkeypatch.setattr(
        "src.services.browser_provider.fetch_index_and_details_via_client",
        _fake_fetch_client,
    )

    result = await crawl_url(
        url="https://example.edu/programmes",
        univ_slug="uom",
        year=2026,
        browser_provider="auto",
    )
    assert result.ingestion_job_id == "job-1"


@pytest.mark.asyncio
async def test_crawl_url_strict_client_raises_when_client_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("src.services.browser_provider.has_available_client", lambda *_: False)

    with pytest.raises(RuntimeError, match="No available client"):
        await crawl_url(
            url="https://example.edu/programmes",
            univ_slug="uom",
            year=2026,
            browser_provider="client",
            strict_client=True,
        )

