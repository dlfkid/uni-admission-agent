from src.services.crawl_strategy.types import (
    CrawlRange, CrawlOutcome, ExtractKind, FetchMode, PaginateMode, Strategy,
)


def test_paginate_mode_values():
    assert PaginateMode.NONE.value == "none"
    assert PaginateMode.SCROLL.value == "scroll"
    assert PaginateMode.URL_PAGES.value == "url_pages"


def test_crawl_range_default_is_first_batch_capped_30():
    r = CrawlRange.default()
    assert r.limit == 30
    assert r.paginate is False


def test_crawl_range_of_n_paginates():
    r = CrawlRange.of(200)
    assert r.limit == 200
    assert r.paginate is True


def test_crawl_range_all_is_unbounded_paginating():
    r = CrawlRange.all_()
    assert r.limit is None
    assert r.paginate is True


def test_strategy_defaults_to_no_pagination():
    s = Strategy(FetchMode.SERVER, ExtractKind.HEADING_LINK)
    assert s.paginate is PaginateMode.NONE


def test_strategy_can_pin_a_mechanism():
    s = Strategy(FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING,
                 paginate=PaginateMode.SCROLL)
    assert s.paginate is PaginateMode.SCROLL


def test_crawl_outcome_has_pagination_fields():
    o = CrawlOutcome(status="ok", university="x")
    assert o.pages_fetched == 0
    assert o.stopped_reason == ""
