"""Tests for src/scrapers/engine.py – AdmissionScraper.

All external calls (crawl4ai, LLM, DB) are mocked.
"""

import asyncio
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.models.scraper_models import CrawlPageResult, PageType
from src.scrapers.engine import AdmissionScraper, _unwrap_retry_error
from src.core.environment import ScraperError
from tenacity import RetryError, Future


# ── _unwrap_retry_error ─────────────────────────────────────────────


def test_unwrap_retry_error_with_cause() -> None:
    cause = ValueError("root cause")
    fut = Future(1)
    fut.set_exception(cause)
    retry_err = RetryError(fut)
    assert _unwrap_retry_error(retry_err) is cause


def test_unwrap_retry_error_no_cause() -> None:
    fut = Future(1)
    fut.set_result(None)
    retry_err = RetryError(fut)
    result = _unwrap_retry_error(retry_err)
    assert result is retry_err


# ── AdmissionScraper.__init__ ───────────────────────────────────────


def test_scraper_init_with_router() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    assert scraper.router is router
    assert len(scraper._visited_urls) == 0


def test_scraper_init_default_configs() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    assert scraper.browser_config.headless is True
    assert scraper._scout_call_count == 0


# ── _reset_session_state ────────────────────────────────────────────


def test_reset_session_state() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    scraper._visited_urls = {"a", "b"}
    scraper._scout_call_count = 3
    scraper._failed_urls = ["c"]
    scraper._all_scouted_links = [MagicMock()]

    scraper._reset_session_state()

    assert scraper._visited_urls == set()
    assert scraper._scout_call_count == 0
    assert scraper._failed_urls == []
    assert scraper._all_scouted_links == []


# ── _determine_page_type ────────────────────────────────────────────


def test_determine_page_type_manual_index() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    result = scraper._determine_page_type(None, "index")
    assert result is True


def test_determine_page_type_manual_detail() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    result = scraper._determine_page_type(None, "detail")
    assert result is False


def test_determine_page_type_auto_detail_signals() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    page = CrawlPageResult(
        url="https://example.com",
        markdown="# MSc Finance\n\n## Tuition Fee\nHK$ 350,000",
        char_count=100,
        links=[],
    )
    result = scraper._determine_page_type(page, "auto")
    assert result is False  # DETAIL


def test_determine_page_type_auto_index_many_links() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    page = CrawlPageResult(
        url="https://example.com",
        markdown="# Our Programmes\n\nBrowse available courses.",
        char_count=50,
        links=[f"https://example.com/prog{i}" for i in range(20)],
    )
    result = scraper._determine_page_type(page, "auto")
    assert result is True  # INDEX


def test_determine_page_type_no_probe() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    result = scraper._determine_page_type(None, "auto")
    assert result is False  # Default to DETAIL


# ── _extract_page_links ─────────────────────────────────────────────


def test_extract_page_links_with_links() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    result = MagicMock()
    result.links = {
        "external": [{"href": "https://ext.com/a"}, {"href": "https://ext.com/b"}],
        "internal": [{"href": "/page1"}, {"href": ""}],
    }
    links = scraper._extract_page_links(result)
    assert "https://ext.com/a" in links
    assert "https://ext.com/b" in links
    assert "/page1" in links
    assert "" not in links


def test_extract_page_links_none() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    result = MagicMock()
    result.links = None
    links = scraper._extract_page_links(result)
    assert links == []


# ── _debug_crawl_result ─────────────────────────────────────────────


def test_debug_crawl_result_poor_markdown(tmp_path) -> None:
    """Should log warning when HTML is large but markdown is tiny."""
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    scraper._export_md = True
    scraper._export_path = str(tmp_path)

    result = MagicMock()
    result.html = "x" * 5000
    result.markdown = MagicMock()
    result.markdown.raw_markdown = "tiny"

    with patch("src.scrapers.engine.save_html_debug") as mock_save:
        scraper._debug_crawl_result(result, "https://example.com/test")
        mock_save.assert_called_once()


def test_debug_crawl_result_good_markdown() -> None:
    """Should not trigger warning when markdown is reasonable."""
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    scraper._export_md = False

    result = MagicMock()
    result.html = "x" * 2000
    result.markdown = MagicMock()
    result.markdown.raw_markdown = "x" * 500

    with patch("src.scrapers.engine.save_html_debug") as mock_save:
        scraper._debug_crawl_result(result, "https://example.com/test")
        mock_save.assert_not_called()


# ── _create_result_from_browser_html ────────────────────────────────


def test_create_result_from_browser_html() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    scraper._export_md = False

    html = "<html><body><h1>Test Program</h1><p>Content here</p></body></html>"

    with patch("crawl4ai.markdown_generation_strategy.DefaultMarkdownGenerator") as MockGen:
        mock_md = MagicMock()
        mock_md.raw_markdown = "# Test Program\n\nContent here"
        MockGen.return_value.generate_markdown.return_value = mock_md

        result = scraper._create_result_from_browser_html("https://example.com", html)

    assert result.url == "https://example.com"
    assert result.markdown == "# Test Program\n\nContent here"
    assert result.status_code == 200
    assert result.html == html


def test_create_result_from_browser_html_conversion_fails() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    scraper._export_md = False

    html = "<html>broken</html>"

    with patch("crawl4ai.markdown_generation_strategy.DefaultMarkdownGenerator") as MockGen:
        MockGen.return_value.generate_markdown.side_effect = RuntimeError("convert error")
        result = scraper._create_result_from_browser_html("https://example.com", html)

    # Falls back to raw HTML as markdown
    assert result.markdown == html


# ── _prepare_probe_result ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_prepare_probe_from_html() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    scraper._export_md = False

    with patch.object(scraper, "_create_result_from_browser_html") as mock_create:
        mock_create.return_value = CrawlPageResult(
            url="u", markdown="md", char_count=2,
        )
        result = await scraper._prepare_probe_result("u", "<html>test</html>")

    assert result is not None
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_prepare_probe_no_html() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    result = await scraper._prepare_probe_result("u", None)
    assert result is None


# ── analyze_page_links ──────────────────────────────────────────────


def test_analyze_page_links_detail_page() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    html = "<html><h1>MSc Finance</h1><p>Tuition Fee: $350,000</p></html>"

    with patch.object(scraper, "_create_result_from_browser_html") as mock_create:
        mock_create.return_value = CrawlPageResult(
            url="https://example.com",
            markdown="# MSc Finance\n\n## Tuition Fee\n$350,000",
            char_count=100,
            links=[],
        )
        result = scraper.analyze_page_links("https://example.com", html, "detail")

    assert result["page_type"] == "detail"
    assert result["links"] == []


def test_analyze_page_links_index_page() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    md = "# Programmes\n[Prog A](https://example.com/a)\n[Prog B](https://example.com/b)"

    with patch.object(scraper, "_create_result_from_browser_html") as mock_create:
        mock_create.return_value = CrawlPageResult(
            url="https://example.com",
            markdown=md,
            char_count=len(md),
            links=["https://example.com/a", "https://example.com/b"],
        )
        with patch("src.scrapers.engine.filter_links_by_llm") as mock_filter:
            mock_filter.return_value = ["https://example.com/a"]

            result = scraper.analyze_page_links("https://example.com", "<html/>", "index")

    assert result["page_type"] == "index"
    assert len(result["links"]) == 1
    assert result["links"][0]["url"] == "https://example.com/a"


# ── crawl_and_clean ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawl_and_clean_empty_url() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    result = await scraper.crawl_and_clean(
        url="", univ_slug="hku", year=2025,
    )
    assert result == 0


@pytest.mark.asyncio
async def test_crawl_and_clean_no_probe() -> None:
    """When no html_content and _prepare_probe_result returns None."""
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    with patch.object(scraper, "_prepare_probe_result", return_value=None):
        result = await scraper.crawl_and_clean(
            url="https://example.com", univ_slug="hku", year=2025,
        )

    assert result == 0


@pytest.mark.asyncio
async def test_crawl_and_clean_browser_detail() -> None:
    """Browser HTML + DETAIL hint should call _process_browser_html."""
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    probe = CrawlPageResult(url="u", markdown="# Test\n\nTuition fee", char_count=20)

    with (
        patch.object(scraper, "_prepare_probe_result", return_value=probe),
        patch.object(scraper, "_determine_page_type", return_value=False),
        patch.object(scraper, "_process_browser_html", return_value=1),
    ):
        result = await scraper.crawl_and_clean(
            url="https://example.com", univ_slug="hku", year=2025,
            html_content="<html>test</html>",
        )

    assert result == 1


# ── crawl_selected_urls ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawl_selected_urls() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    with patch.object(scraper, "_crawl_depth", return_value=3):
        result = await scraper.crawl_selected_urls(
            urls=["https://a.com", "https://b.com", "https://c.com"],
            univ_slug="hku", year=2025,
        )

    assert result == 3


# ── _crawl_depth ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawl_depth_skips_visited() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)
    scraper._visited_urls = {"https://example.com/a", "https://example.com/b"}

    with patch.object(scraper, "_crawl_urls", return_value=[]):
        result = await scraper._crawl_depth(
            urls=["https://example.com/a"],
            univ_slug="hku", year=2025,
            current_depth=0, max_continue=0, is_index_layer=False,
        )

    assert result == 0


@pytest.mark.asyncio
async def test_crawl_depth_index_layer() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    pages = [CrawlPageResult(url="https://example.com", markdown="# Index", char_count=7)]

    with (
        patch.object(scraper, "_crawl_urls", return_value=pages),
        patch.object(scraper, "_handle_index_layer", return_value=5),
    ):
        result = await scraper._crawl_depth(
            urls=["https://example.com"],
            univ_slug="hku", year=2025,
            current_depth=0, max_continue=1, is_index_layer=True,
        )

    assert result == 5


@pytest.mark.asyncio
async def test_crawl_depth_detail_layer() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    pages = [CrawlPageResult(url="https://example.com/p", markdown="# Detail", char_count=8)]

    with (
        patch.object(scraper, "_crawl_urls", return_value=pages),
        patch.object(scraper, "_handle_detail_layer", return_value=1),
    ):
        result = await scraper._crawl_depth(
            urls=["https://example.com/p"],
            univ_slug="hku", year=2025,
            current_depth=0, max_continue=0, is_index_layer=False,
        )

    assert result == 1


# ── _crawl_urls ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawl_urls_handles_pdf() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    with patch("src.scrapers.engine.PDFProcessor") as MockPDF:
        mock_result = MagicMock()
        mock_result.markdown = "# PDF Content"
        mock_result.char_count = 13
        MockPDF.return_value.convert_to_markdown.return_value = mock_result

        results = await scraper._crawl_urls(["https://example.com/doc.pdf"])

    assert len(results) == 1
    assert results[0].markdown == "# PDF Content"


@pytest.mark.asyncio
async def test_crawl_urls_handles_scraper_error() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    with patch.object(scraper, "crawl_page", side_effect=ScraperError("blocked")):
        results = await scraper._crawl_urls(["https://blocked.com"])

    assert len(results) == 0


# ── _handle_index_layer ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_index_layer_no_links() -> None:
    """When index has no links, should fall back to detail processing."""
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    pages = [CrawlPageResult(url="https://example.com", markdown="No links here", char_count=13)]

    with patch.object(scraper, "_handle_detail_layer", return_value=1):
        result = await scraper._handle_index_layer(
            pages, "hku", 2025, 0, 0,
        )

    assert result == 1


@pytest.mark.asyncio
async def test_handle_index_layer_with_links() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    pages = [CrawlPageResult(
        url="https://example.com",
        markdown="# Index\n[Prog A](https://example.com/a)\n[Prog B](https://example.com/b)",
        char_count=100,
    )]

    with (
        patch("src.scrapers.engine.filter_links_by_llm",
              return_value=["https://example.com/a", "https://example.com/b"]),
        patch.object(scraper, "_crawl_depth", return_value=2),
    ):
        result = await scraper._handle_index_layer(pages, "hku", 2025, 0, 0)

    assert result == 2


# ── _handle_detail_layer ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_detail_layer_no_scout() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    pages = [CrawlPageResult(url="https://example.com/p", markdown="# Prog", char_count=6)]

    with patch("src.scrapers.engine.process_pages_batch", return_value=(2, [], [])):
        result = await scraper._handle_detail_layer(pages, "hku", 2025, 0, 0)

    assert result == 2


@pytest.mark.asyncio
async def test_handle_detail_layer_with_scout() -> None:
    router = MagicMock()
    scraper = AdmissionScraper(router=router)

    failed_page = CrawlPageResult(
        url="https://example.com/fail",
        markdown="# No data",
        char_count=9,
        links=["https://example.com/deeper"],
    )

    mock_scouted = MagicMock()
    mock_scouted.url = "https://example.com/deeper"

    with (
        patch("src.scrapers.engine.process_pages_batch", return_value=(0, [failed_page], [])),
        patch("src.scrapers.engine.run_scout", return_value=(["https://example.com/deeper"], 1, [mock_scouted])),
        patch.object(scraper, "_crawl_depth", return_value=1),
    ):
        result = await scraper._handle_detail_layer(page_results=[failed_page], univ_slug="hku", year=2025, current_depth=0, max_continue=1)

    assert result == 1
