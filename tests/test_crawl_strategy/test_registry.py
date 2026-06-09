from src.services.crawl_strategy.registry import lookup, REGISTRY
from src.services.crawl_strategy.types import FetchMode, ExtractKind


def test_known_leeds_pinned_to_server_heading():
    s = lookup("https://courses.leeds.ac.uk/course-search/masters-courses")
    assert s is not None
    assert s.fetch is FetchMode.SERVER
    assert s.extract is ExtractKind.HEADING_LINK


def test_known_ucl_pinned_to_client_inline():
    s = lookup("https://www.ucl.ac.uk/prospective-students/undergraduate/degrees")
    assert s.fetch is FetchMode.CLIENT
    assert s.extract is ExtractKind.INLINE_DEGREE


def test_unknown_domain_returns_none():
    assert lookup("https://example.edu/programmes") is None


def test_subdomain_and_scheme_insensitive():
    assert lookup("http://COURSES.leeds.ac.uk/anything") is not None


def test_known_nus_pinned_to_client_wait_text_heading():
    s = lookup("https://study.nus.edu.sg/programme")
    assert s.fetch is FetchMode.CLIENT_WAIT
    assert s.extract is ExtractKind.TEXT_HEADING


def test_known_sites_pin_paginate_mechanism():
    from src.services.crawl_strategy.types import PaginateMode
    assert lookup("https://study.nus.edu.sg/programme").paginate is PaginateMode.SCROLL
    assert lookup("https://courses.leeds.ac.uk/x").paginate is PaginateMode.URL_PAGES
    assert lookup("https://www.ucl.ac.uk/x").paginate is PaginateMode.NONE
    assert lookup("https://www.manchester.ac.uk/x").paginate is PaginateMode.NONE
    assert lookup("https://www.polyu.edu.hk/x").paginate is PaginateMode.NONE


def test_leeds_carries_url_page_param():
    s = lookup("https://courses.leeds.ac.uk/x")
    assert s.params.get("page_param") == "page"
