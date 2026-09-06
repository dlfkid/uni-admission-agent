"""The index page's markdown must reach detail extraction as the boilerplate
reference — through every path that fetches an index.

strip_shared_boilerplate() needs the site's own index page to tell HKBU's
mega-menu from its programme content. That page is fetched in three places:
the crawl-strategy orchestrator (fast path, never enters the pipeline's LLM
index-analysis branch), the pipeline's entry-index branch, and the pipeline's
browser-HTML branch. The sibling-URL map already had to be wired through the
same three seams after two separate bugs; this file pins the third.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.crawl_strategy.discovery as disc
import src.services.crawler as crawler_mod
from src.models.scraper_models import CrawlPageResult
from src.services.crawl_strategy.discovery import DiscoveryResult
from src.services.crawl_strategy.orchestrator import crawl_index
from src.services.crawl_strategy.types import CrawlOutcome, CrawlRange, ExtractItem
from src.services.ingestion_pipeline import IngestionPipeline

INDEX_MD = "".join(
    f"[Programme {i} BSc](https://example.edu/degrees/programme-{i}-bsc)\n" for i in range(9)
)


# ── 1. orchestrator → CrawlOutcome ────────────────────────────────────


def test_crawl_outcome_carries_the_index_markdown(tmp_path) -> None:
    out = crawl_index(
        "https://example.edu/degrees",
        server_fetch=lambda url: ("<html>", INDEX_MD),
        client_fetch=lambda url, **kw: ("", ""),
        report_out=tmp_path, timestamp="t",
    )
    assert out.status == "ok"
    assert out.index_markdown == INDEX_MD


# ── 2. discovery → DiscoveryResult ────────────────────────────────────


def test_discovery_result_carries_the_index_markdown(monkeypatch, tmp_path) -> None:
    items = [ExtractItem(name_en="A BSc", detail_url="https://x.edu/a")]
    outcome = CrawlOutcome(
        status="ok", university="x", names=["A BSc"], items=items, names_count=1,
        strategy_used="server×inline_degree", index_markdown="# index\n[A BSc](https://x.edu/a)",
    )
    monkeypatch.setattr(disc, "crawl_index", lambda *a, **k: outcome)
    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.default(),
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        api_fetch=lambda e, **k: "", report_out=tmp_path, timestamp="t",
    )
    assert r.matched is True
    assert r.index_markdown == "# index\n[A BSc](https://x.edu/a)"


# ── 3. crawler → run_new_job ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawl_url_threads_the_index_markdown_into_the_job(monkeypatch) -> None:
    spy = AsyncMock(return_value={"imported_count": 0, "persisted_program_ids": []})
    monkeypatch.setattr(crawler_mod.IngestionPipeline, "run_new_job", spy, raising=True)
    monkeypatch.setattr(
        crawler_mod.browser_provider_service, "resolve_browser_inputs",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(crawler_mod, "_build_review_items", lambda *a, **k: [])
    matched = DiscoveryResult(
        matched=True, link_texts={"https://x.edu/a": "A MSc"}, names_total=1,
        strategy_used="server×heading_link", index_markdown="# the index",
    )
    monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", lambda url, rng: matched)

    await crawler_mod.crawl_url("https://x.edu/p", "xuni", 2027, page_type_hint="index", limit=10)

    assert spy.call_args.kwargs["index_markdown"] == "# the index"


def test_run_new_job_defaults_to_index_not_auto() -> None:
    """'auto' was retired; the pipeline's own default must not resurrect it."""
    assert inspect.signature(IngestionPipeline.run_new_job).parameters["page_type_hint"].default == "index"


# ── 4. pipeline fetch_raw → context ───────────────────────────────────


def _fake_scraper_class(seed_markdown: str, detail_pages: list[CrawlPageResult]):
    class FakeScraper:
        def __init__(self) -> None:
            self.router = MagicMock()
            self._export_md = False
            self._export_path = None

        def _reset_session_state(self) -> None:
            return

        def _create_result_from_browser_html(self, url: str, html_content: str) -> CrawlPageResult:
            return CrawlPageResult(
                url=url, markdown=seed_markdown, char_count=len(seed_markdown),
                links=[], status_code=200, html=html_content,
            )

        def _determine_page_type(self, page, hint):  # noqa: ARG002
            return hint == "index"

        async def crawl_page(self, url: str) -> CrawlPageResult:
            return CrawlPageResult(
                url=url, markdown=seed_markdown, char_count=len(seed_markdown),
                links=[], status_code=200, html="<html>seed</html>",
            )

        async def _crawl_urls(self, urls):
            return [p for p in detail_pages if p.url in urls]

    return FakeScraper


_DETAIL = CrawlPageResult(
    url="https://x.edu/a", markdown="# A MSc\nTuition 1", char_count=18, links=[],
)


@pytest.mark.asyncio
async def test_fetch_raw_selected_urls_branch_keeps_the_discovery_index_markdown(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.AdmissionScraper",
        _fake_scraper_class("unused", [_DETAIL]),
    )
    pipeline = IngestionPipeline(db_manager=MagicMock())
    result = await pipeline._stage_fetch_raw(
        {
            "url": "https://x.edu/p", "page_type_hint": "index",
            "selected_urls": ["https://x.edu/a"], "index_markdown": "# from discovery",
        }
    )
    assert result["raw_page_count"] == 1
    assert result["index_markdown"] == "# from discovery"


@pytest.mark.asyncio
async def test_fetch_raw_entry_index_branch_keeps_the_seed_page_markdown(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.AdmissionScraper",
        _fake_scraper_class("# seed index", [_DETAIL]),
    )
    pipeline = IngestionPipeline(db_manager=MagicMock())
    select = AsyncMock(return_value=(["https://x.edu/a"], {"https://x.edu/a": "A MSc"}))
    monkeypatch.setattr(pipeline, "_select_detail_urls", select)
    result = await pipeline._stage_fetch_raw(
        {"url": "https://x.edu/p", "page_type_hint": "index", "selected_urls": []}
    )
    assert result["raw_page_count"] == 1
    assert result["index_markdown"] == "# seed index"


@pytest.mark.asyncio
async def test_fetch_raw_browser_html_branch_keeps_the_probe_markdown(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.AdmissionScraper",
        _fake_scraper_class("# probe index", [_DETAIL]),
    )
    pipeline = IngestionPipeline(db_manager=MagicMock())
    select = AsyncMock(return_value=(["https://x.edu/a"], {"https://x.edu/a": "A MSc"}))
    monkeypatch.setattr(pipeline, "_select_detail_urls", select)
    result = await pipeline._stage_fetch_raw(
        {
            "url": "https://x.edu/p", "page_type_hint": "index", "selected_urls": [],
            "html_content": "<html>index</html>",
        }
    )
    assert result["raw_page_count"] == 1
    assert result["index_markdown"] == "# probe index"


@pytest.mark.asyncio
async def test_fetch_raw_detail_mode_has_no_reference(monkeypatch) -> None:
    """A single detail page has no index to compare against; nothing is filtered."""
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.AdmissionScraper",
        _fake_scraper_class("# the detail itself", []),
    )
    pipeline = IngestionPipeline(db_manager=MagicMock())
    result = await pipeline._stage_fetch_raw(
        {"url": "https://x.edu/a", "page_type_hint": "detail", "selected_urls": []}
    )
    assert result["raw_page_count"] == 1
    assert not result.get("index_markdown")


# ── 5. pipeline extract_structured → extract_program_data_from_page ───


def test_extract_structured_hands_the_reference_to_extraction(monkeypatch) -> None:
    monkeypatch.setattr("src.services.ingestion_pipeline.LLMCleanerAgent", MagicMock)
    extract_mock = MagicMock(return_value=({"name_en": "A MSc", "tuition_amount": 1}, None))
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page", extract_mock
    )
    pipeline = IngestionPipeline(db_manager=MagicMock())
    context = {
        "index_markdown": "# the index",
        "raw_pages": [
            {
                "url": "https://x.edu/a", "markdown": "# A MSc\n" + ("body " * 400),
                "char_count": 2000, "links": [], "status_code": 200, "html": "",
                "crawl_depth": 1, "from_browser": False, "selected_anchor_text": "A MSc",
            }
        ],
    }
    pipeline._stage_extract_structured(
        {"univ_slug": "x", "year": 2027, "page_type_hint": "index",
         "selected_urls": ["https://x.edu/a"]},
        context,
    )
    assert extract_mock.call_args.kwargs["boilerplate_reference"] == "# the index"


# ── 6. extract_program_data_from_page → cleaner ───────────────────────


def test_extraction_feeds_the_cleaner_the_filtered_markdown() -> None:
    from src.scrapers.page_processor import extract_program_data_from_page

    menu = "\n".join(f"* [Item {i}](https://x.edu/{i})" for i in range(6))
    index = menu + "\n# Programmes\n"
    detail = menu + "\n# A MSc\nTuition Fee HK$1\n"
    cleaner = MagicMock()
    cleaner.clean_markdown_with_critique.return_value = None  # extraction outcome irrelevant

    page = CrawlPageResult(url="https://x.edu/a", markdown=detail, char_count=len(detail), links=[])
    extract_program_data_from_page(
        page=page, cleaner=cleaner, univ_slug="x", year=2027, current_depth=1,
        boilerplate_reference=index,
    )

    sent = cleaner.clean_markdown_with_critique.call_args.kwargs["markdown"]
    assert "Item 3" not in sent
    assert "Tuition Fee HK$1" in sent
