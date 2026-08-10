import pytest

import src.services.crawl_strategy.discovery as disc
from src.services.crawl_strategy.types import CrawlOutcome, CrawlRange, ExtractItem


def _ok_outcome(items, sibling_urls=None):
    return CrawlOutcome(
        status="ok", university="leeds",
        names=[i.name_en for i in items], items=items,
        names_count=len(items), strategy_used="server×heading_link",
        pages_fetched=2, stopped_reason="reached_limit",
        sibling_urls=sibling_urls or {},
    )


def _fake_fetches():
    return {
        "server_fetch": lambda u: ("", ""),
        "client_fetch": lambda u, **k: ("", ""),
        "api_fetch": lambda e, **k: "",
    }


def test_resolve_crawl_range():
    assert disc.resolve_crawl_range(None, False) == CrawlRange.default()
    assert disc.resolve_crawl_range(50, False) == CrawlRange.of(50)
    assert disc.resolve_crawl_range(None, True) == CrawlRange.all_()
    with pytest.raises(ValueError):
        disc.resolve_crawl_range(5, True)


def test_ok_outcome_maps_to_matched(monkeypatch, tmp_path):
    items = [
        ExtractItem("Accounting MSc", "https://x.edu/acc"),
        ExtractItem("Finance MSc", "https://x.edu/fin"),
        ExtractItem("Nameless Programme", None),   # name but no detail link
    ]
    monkeypatch.setattr(disc, "crawl_index", lambda *a, **k: _ok_outcome(items))

    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.of(10),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.matched is True
    assert r.link_texts == {
        "https://x.edu/acc": "Accounting MSc",
        "https://x.edu/fin": "Finance MSc",
    }
    assert r.nameless_count == 1
    assert r.names_total == 3
    assert r.strategy_used == "server×heading_link"
    assert r.stopped_reason == "reached_limit"
    assert r.pages_fetched == 2


def test_ok_outcome_propagates_sibling_urls(monkeypatch, tmp_path):
    """Regression: discover_candidates must pass CrawlOutcome.sibling_urls
    through to DiscoveryResult unchanged — this is the only path
    crawler.py's strategy-discovery fast path has to learn about a row's
    "Visit Website" sibling link, since it never runs the LLM
    index-analysis branch that would otherwise build this map."""
    items = [ExtractItem("Accounting MSc", "https://x.edu/acc")]
    monkeypatch.setattr(
        disc, "crawl_index",
        lambda *a, **k: _ok_outcome(items, {"https://x.edu/acc": ["https://x.edu/acc-site"]}))

    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.of(10),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.sibling_urls == {"https://x.edu/acc": ["https://x.edu/acc-site"]}


def test_unsupported_discovery_has_empty_sibling_urls(monkeypatch, tmp_path):
    out = CrawlOutcome(status="unsupported", university="x",
                       report_zip="/tmp/x.zip", message_for_user="m")
    monkeypatch.setattr(disc, "crawl_index", lambda *a, **k: out)
    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.default(),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.sibling_urls == {}


def test_ok_but_all_nameless_is_not_matched(monkeypatch, tmp_path):
    items = [ExtractItem("Only Name", None)]
    monkeypatch.setattr(disc, "crawl_index", lambda *a, **k: _ok_outcome(items))
    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.default(),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.matched is False          # nothing to detail-crawl
    assert r.nameless_count == 1


def test_unsupported_maps_to_fallback(monkeypatch, tmp_path):
    out = CrawlOutcome(status="unsupported", university="x",
                       report_zip="/tmp/x.zip", message_for_user="m")
    monkeypatch.setattr(disc, "crawl_index", lambda *a, **k: out)
    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.default(),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.matched is False
    assert r.report_zip == "/tmp/x.zip"


def test_exception_maps_to_fallback(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("network exploded")
    monkeypatch.setattr(disc, "crawl_index", boom)
    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.default(),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.matched is False
    assert r.report_zip is None
