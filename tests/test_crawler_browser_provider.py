import pytest

from src.services.crawler import (
    analyze_url_candidates,
    crawl_selected_detail_urls_via_client,
    crawl_url,
)


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


@pytest.mark.asyncio
async def test_analyze_url_candidates_uses_client_html_when_not_provided(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_resolve_browser_inputs(**kwargs):
        captured["resolve"] = kwargs
        return {"html_content": "<html>index</html>"}

    def _fake_analyze_page(url: str, html_content: str, page_type_hint: str = "index"):
        captured["analyze"] = {
            "url": url,
            "html_content": html_content,
            "page_type_hint": page_type_hint,
        }
        return {"page_type": "index", "links": [{"url": "https://example.edu/p/1", "text": "P1"}], "total_found": 8}

    monkeypatch.setattr(
        "src.services.browser_provider.resolve_browser_inputs",
        _fake_resolve_browser_inputs,
    )
    monkeypatch.setattr("src.services.crawler.analyze_page", _fake_analyze_page)

    result = await analyze_url_candidates(
        url="https://example.edu/list",
        page_type_hint="index",
        browser_provider="client",
    )

    assert result["page_type"] == "index"
    assert result["total_found"] == 8
    assert result["html_source"] == "client"
    assert captured["analyze"]["html_content"] == "<html>index</html>"
    assert captured["resolve"]["url"] == "https://example.edu/list"


@pytest.mark.asyncio
async def test_crawl_selected_detail_urls_via_client_batches_requests(monkeypatch) -> None:
    fetch_calls: list[dict] = []
    crawl_calls: list[dict] = []

    async def _fake_fetch_index_and_details_via_client(**kwargs):
        fetch_calls.append(kwargs)
        return {"html_content": f"<html>{kwargs['url']}</html>"}

    async def _fake_crawl_url(**kwargs):
        crawl_calls.append(kwargs)

        class _Result:
            imported_count = len(kwargs.get("detail_pages_batch") or [])
            ingestion_job_id = f"job-{kwargs.get('batch_index')}"

        return _Result()

    monkeypatch.setattr(
        "src.services.browser_provider.fetch_index_and_details_via_client",
        _fake_fetch_index_and_details_via_client,
    )
    monkeypatch.setattr("src.services.crawler.crawl_url", _fake_crawl_url)

    result = await crawl_selected_detail_urls_via_client(
        index_url="https://example.edu/list",
        selected_urls=[
            "https://example.edu/detail/1",
            "https://example.edu/detail/2",
            "https://example.edu/detail/3",
            "https://example.edu/detail/4",
            "https://example.edu/detail/5",
        ],
        univ_slug="uom",
        year=2026,
        batch_size=2,
        selected_link_texts={
            "https://example.edu/detail/1": "Course 1",
            "https://example.edu/detail/4": "Course 4",
        },
    )

    assert len(fetch_calls) == 5
    assert [call["page_type_hint"] for call in fetch_calls] == ["detail"] * 5
    assert len(crawl_calls) == 3
    assert [call["batch_index"] for call in crawl_calls] == [1, 2, 3]
    assert [len(call["detail_pages_batch"]) for call in crawl_calls] == [2, 2, 1]
    assert result["total_selected"] == 5
    assert result["batch_total"] == 3
    assert result["imported_count"] == 5
