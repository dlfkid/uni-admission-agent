from src.services.crawl_strategy.extractors import extract_inline_degree
from src.services.crawl_strategy.paginator import detect_mechanism, paginate, PaginateResult
from src.services.crawl_strategy.types import (
    CrawlRange, ExtractItem, ExtractKind, FetchMode, PaginateMode, Strategy,
)

_STRAT = Strategy(FetchMode.SERVER, ExtractKind.HEADING_LINK,
                  params={"page_param": "page", "page_start": 1},
                  paginate=PaginateMode.URL_PAGES)


def _items(prefix, n):
    return [ExtractItem(f"{prefix} {i} MSc", f"https://x.edu/{prefix}{i}") for i in range(n)]


def _extract_from_marker(md, base_url):
    # Test fake: markdown is "P:<prefix>:<n>"; emit n items named by prefix.
    _, prefix, n = md.split(":")
    return _items(prefix, int(n))


def test_none_truncates_first_page_to_limit():
    r = paginate(
        mechanism=PaginateMode.NONE, crawl_range=CrawlRange.of(10),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:25",
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert len(r.items) == 10
    assert r.stopped_reason == "reached_limit"
    assert r.pages_fetched == 1


def test_none_returns_all_when_under_limit():
    r = paginate(
        mechanism=PaginateMode.NONE, crawl_range=CrawlRange.of(50),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:9",
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert len(r.items) == 9
    assert r.stopped_reason == "exhausted"


def test_url_pages_accumulates_across_pages_and_truncates():
    # page 1 has prefix a (5), page 2 prefix b (5), page 3 prefix c (5)
    pages = {1: "P:a:5", 2: "P:b:5", 3: "P:c:5"}

    def server(url):
        # page number is the ?page=N value; page 1 = no query (first_md)
        import urllib.parse as up
        q = dict(up.parse_qsl(up.urlsplit(url).query))
        return ("<html>", pages[int(q.get("page", 2))])

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.of(12),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md=pages[1],
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker, is_usable=bool)
    assert len(r.items) == 12         # 5 + 5 + 2 (truncated)
    assert r.stopped_reason == "reached_limit"
    assert r.pages_fetched == 3


def test_url_pages_stops_exhausted_on_zero_new():
    # every page after 1 repeats prefix a -> 0 new names
    def server(url):
        return ("<html>", "P:a:5")

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.all_(),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:5",
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker, is_usable=bool)
    assert len(r.items) == 5
    assert r.stopped_reason == "exhausted"


def test_url_pages_stops_unusable():
    def server(url):
        return ("<html>", "")   # empty -> not usable

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.all_(),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:5",
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker, is_usable=bool)
    assert len(r.items) == 5
    assert r.stopped_reason == "unusable"


def test_url_pages_safety_cap():
    # each page yields fresh names forever; all_ must stop at the page ceiling
    def server(url):
        import urllib.parse as up
        q = dict(up.parse_qsl(up.urlsplit(url).query))
        return ("<html>", f"P:p{q.get('page', '0')}:3")

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.all_(),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:first:3",
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker, is_usable=bool)
    assert r.stopped_reason == "safety_cap"
    assert r.pages_fetched == 50


def test_url_pages_paginate_false_stays_on_first_page():
    def server(url):
        raise AssertionError("paginate=False must not fetch page 2")

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.default(),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:9",
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert len(r.items) == 9
    assert r.pages_fetched == 1


def test_detect_url_pages_from_page_query_link():
    md = "[Next](https://x.edu/p?page=2)\n[Course MSc](https://x.edu/c1)\n"
    assert detect_mechanism("<html>", md, "https://x.edu/p", "server") is PaginateMode.URL_PAGES


def test_detect_url_pages_from_existing_page_param_in_url():
    assert detect_mechanism(
        "<html>", "[c](u)\n", "https://x.edu/p?page=1", "server"
    ) is PaginateMode.URL_PAGES


def test_detect_scroll_for_client_wait_app():
    assert detect_mechanism(
        "<html>", "## Doctor of X\n", "https://x.edu/p", "client_wait"
    ) is PaginateMode.SCROLL


def test_detect_none_for_static_single_page():
    assert detect_mechanism(
        "<html>", "[Course MSc](https://x.edu/c1)\n", "https://x.edu/p", "server"
    ) is PaginateMode.NONE


def test_scroll_passes_target_count_and_truncates():
    received = {}

    def client(url, **kw):
        received.update(kw)
        return ("<html>", "P:a:25")   # browser rendered 25 after scrolling

    r = paginate(
        mechanism=PaginateMode.SCROLL, crawl_range=CrawlRange.of(10),
        index_url="https://x.edu/p",
        strategy=Strategy(FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING,
                          paginate=PaginateMode.SCROLL),
        first_html="<html>", first_md="P:a:5",
        server_fetch=lambda u: ("", ""), client_fetch=client,
        extract=_extract_from_marker)
    assert received.get("wait") is True
    assert received.get("target_count") == 10
    assert len(r.items) == 10
    assert r.stopped_reason == "reached_limit"


def test_scroll_all_passes_none_target():
    received = {}

    def client(url, **kw):
        received.update(kw)
        return ("<html>", "P:a:8")

    r = paginate(
        mechanism=PaginateMode.SCROLL, crawl_range=CrawlRange.all_(),
        index_url="https://x.edu/p",
        strategy=Strategy(FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING,
                          paginate=PaginateMode.SCROLL),
        first_html="<html>", first_md="",
        server_fetch=lambda u: ("", ""), client_fetch=client,
        extract=_extract_from_marker)
    assert received.get("target_count") is None
    assert len(r.items) == 8
    assert r.stopped_reason == "exhausted"


# ── sibling-map building (thin-page-supplement fast-path fix) ───────────
#
# Regression: strategy-discovery's fast path bypasses the pipeline's LLM
# index-analysis branch entirely (it feeds `selected_urls` straight in),
# so it must build its OWN sibling map from the same markdown it already
# fetched — otherwise any domain matched here silently loses sibling-link
# discovery, starving the thin-page-supplement mechanism of its main input.
# `_extract_from_marker`'s synthetic "P:<prefix>:<n>" markdown has no real
# link syntax, so these use the real extract_inline_degree extractor
# against Lingnan-shaped rows (name link + "Visit Website" sibling link).

def _row_with_sibling(name: str, url: str, sibling_url: str) -> str:
    return f"Programme Name: [{name} MSc]({url}) | [Visit Website]({sibling_url})\n"


def test_none_builds_sibling_map_from_first_page():
    md = (
        _row_with_sibling("Master A0", "https://x.edu/a0", "https://x.edu/a0-site")
        + _row_with_sibling("Master A1", "https://x.edu/a1", "https://x.edu/a1-site")
    )
    r = paginate(
        mechanism=PaginateMode.NONE, crawl_range=CrawlRange.of(10),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md=md,
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        extract=extract_inline_degree)
    assert len(r.items) == 2
    assert r.sibling_urls == {
        "https://x.edu/a0": ["https://x.edu/a0-site"],
        "https://x.edu/a1": ["https://x.edu/a1-site"],
    }


def test_url_pages_accumulates_sibling_map_across_pages():
    pages = {
        1: _row_with_sibling("Master A0", "https://x.edu/a0", "https://x.edu/a0-site"),
        2: _row_with_sibling("Master B0", "https://x.edu/b0", "https://x.edu/b0-site"),
    }

    def server(url):
        import urllib.parse as up
        q = dict(up.parse_qsl(up.urlsplit(url).query))
        return ("<html>", pages[int(q.get("page", 2))])

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.of(2),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md=pages[1],
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=extract_inline_degree, is_usable=bool)
    assert r.stopped_reason == "reached_limit"
    assert len(r.items) == 2
    # Both pages' sibling links present — proves accumulation, not just
    # "whichever page happened to run last" overwriting the other.
    assert r.sibling_urls == {
        "https://x.edu/a0": ["https://x.edu/a0-site"],
        "https://x.edu/b0": ["https://x.edu/b0-site"],
    }


def test_scroll_builds_sibling_map():
    md = _row_with_sibling("Master A0", "https://x.edu/a0", "https://x.edu/a0-site")

    def client(url, **kw):
        return ("<html>", md)

    r = paginate(
        mechanism=PaginateMode.SCROLL, crawl_range=CrawlRange.of(10),
        index_url="https://x.edu/p",
        strategy=Strategy(FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING,
                          paginate=PaginateMode.SCROLL),
        first_html="<html>", first_md="",
        server_fetch=lambda u: ("", ""), client_fetch=client,
        extract=extract_inline_degree)
    assert r.sibling_urls == {"https://x.edu/a0": ["https://x.edu/a0-site"]}


def test_no_sibling_link_on_row_yields_empty_map():
    """A bare stub with no sibling row-mate must not fabricate an entry —
    matches the real "Master of Arts in Chinese" structural-ceiling case."""
    md = "Programme Name: [Master A0 MSc](https://x.edu/a0)\n"
    r = paginate(
        mechanism=PaginateMode.NONE, crawl_range=CrawlRange.of(10),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md=md,
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        extract=extract_inline_degree)
    assert len(r.items) == 1
    assert r.sibling_urls == {}
