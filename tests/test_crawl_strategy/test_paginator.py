from src.services.crawl_strategy.paginator import paginate, PaginateResult
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
        extract=_extract_from_marker)
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
        extract=_extract_from_marker)
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
        extract=_extract_from_marker)
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
        extract=_extract_from_marker)
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
