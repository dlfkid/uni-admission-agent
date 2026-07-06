"""crawl_url × strategy discovery: inject when matched, byte-identical fallback."""
from unittest.mock import AsyncMock

import pytest

import src.services.crawler as crawler_mod
from src.services.crawl_strategy.discovery import DiscoveryResult


def _matched():
    return DiscoveryResult(
        matched=True,
        link_texts={"https://x.edu/a": "A MSc", "https://x.edu/b": "B MSc"},
        names_total=2, strategy_used="server×heading_link",
        stopped_reason="exhausted", pages_fetched=1)


@pytest.fixture(name="run_new_job_spy")
def _run_new_job_spy(monkeypatch):
    spy = AsyncMock(return_value={"imported_count": 0, "persisted_program_ids": []})
    monkeypatch.setattr(
        crawler_mod.IngestionPipeline, "run_new_job", spy, raising=True)
    # Browser-provider resolution is out of scope here — identity passthrough.
    monkeypatch.setattr(
        crawler_mod.browser_provider_service, "resolve_browser_inputs",
        AsyncMock(return_value={}))
    monkeypatch.setattr(crawler_mod, "_build_review_items", lambda *a, **k: [])
    return spy


@pytest.mark.asyncio
async def test_matched_discovery_injects_selected_urls(run_new_job_spy, monkeypatch):
    monkeypatch.setattr(
        crawler_mod, "discover_with_default_adapters", lambda url, rng: _matched())

    await crawler_mod.crawl_url(
        "https://x.edu/p", "xuni", 2026, page_type_hint="index", limit=10)

    kwargs = run_new_job_spy.call_args.kwargs
    assert sorted(kwargs["selected_urls"]) == ["https://x.edu/a", "https://x.edu/b"]
    assert kwargs["selected_link_texts"] == _matched().link_texts


@pytest.mark.asyncio
async def test_unmatched_discovery_falls_back_unchanged(run_new_job_spy, monkeypatch):
    monkeypatch.setattr(
        crawler_mod, "discover_with_default_adapters",
        lambda url, rng: DiscoveryResult(matched=False))

    await crawler_mod.crawl_url(
        "https://x.edu/p", "xuni", 2026, page_type_hint="index")

    kwargs = run_new_job_spy.call_args.kwargs
    assert kwargs["selected_urls"] is None        # today's scout path, untouched
    assert kwargs["selected_link_texts"] is None
    assert kwargs == {
        "url": "https://x.edu/p",
        "univ_slug": "xuni",
        "year": 2026,
        "continue_depth": 0,
        "page_type_hint": "index",
        "export_md": False,
        "export_path": None,
        "html_content": None,
        "selected_urls": None,
        "selected_link_texts": None,
        "max_detail_pages": None,
        "browser_automation_enabled": False,
        "detail_pages_batch": None,
        "batch_index": None,
        "batch_total": None,
        "supplement_url_re": None,
        "candidate_taxonomy_filter_enabled": False,
        "candidate_taxonomy_filter_threshold": 0.75,
        "candidate_taxonomy_filter_top_k": 30,
        "taxonomy_enabled": None,
        "taxonomy_low_threshold": None,
        "taxonomy_high_threshold": None,
        "taxonomy_hint_top_k": None,
        "taxonomy_override_enabled": None,
        "name_resolution_llm_enabled": None,
        "name_resolution_low_threshold": None,
        "name_resolution_conflict_delta": None,
        "event_callback": kwargs["event_callback"],
    }


@pytest.mark.asyncio
async def test_no_discovery_when_urls_preselected(run_new_job_spy, monkeypatch):
    def boom(url, rng):
        raise AssertionError("discovery must not run when caller pre-selected URLs")
    monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", boom)

    await crawler_mod.crawl_url(
        "https://x.edu/p", "xuni", 2026, page_type_hint="index",
        selected_urls=["https://x.edu/manual"],
        selected_link_texts={"https://x.edu/manual": "Manual MSc"})

    kwargs = run_new_job_spy.call_args.kwargs
    assert kwargs["selected_urls"] == ["https://x.edu/manual"]


@pytest.mark.asyncio
async def test_no_discovery_for_detail_pages(run_new_job_spy, monkeypatch):
    def boom(url, rng):
        raise AssertionError("discovery must not run for detail pages")
    monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", boom)

    await crawler_mod.crawl_url(
        "https://x.edu/one-course", "xuni", 2026, page_type_hint="detail")
    assert run_new_job_spy.call_args.kwargs["selected_urls"] is None


@pytest.mark.asyncio
async def test_precomputed_discovery_is_used_without_rerun(run_new_job_spy, monkeypatch):
    def boom(url, rng):
        raise AssertionError("must use the precomputed DiscoveryResult")
    monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", boom)

    await crawler_mod.crawl_url(
        "https://x.edu/p", "xuni", 2026, page_type_hint="index",
        discovery=_matched())
    kwargs = run_new_job_spy.call_args.kwargs
    assert kwargs["selected_link_texts"] == _matched().link_texts
