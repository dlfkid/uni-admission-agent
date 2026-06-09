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


def test_known_nus_pinned_to_api():
    s = lookup("https://study.nus.edu.sg/programme")
    assert s.fetch is FetchMode.API
    assert s.extract is ExtractKind.JSON_API


def test_nus_pinned_to_api_json_strategy():
    from src.services.crawl_strategy.types import ExtractKind, FetchMode
    s = lookup("https://study.nus.edu.sg/programme")
    assert s.fetch is FetchMode.API
    assert s.extract is ExtractKind.JSON_API
    assert s.params["items_path"] == "returnValue"
    assert s.params["name_path"] == "programme.Title__c"
    assert s.params["detail_url_path"] == "programme.Program_Page_Link__c"
    assert s.params["body"]["method"] == "searchProgrammes"
    assert "apex/execute" in s.params["endpoint"]


def test_known_sites_pin_paginate_mechanism():
    from src.services.crawl_strategy.types import PaginateMode
    # NUS renders 10 programmes once and does not scroll-load more (probe-
    # confirmed), so it is pinned NONE — its full catalogue needs a filter/API
    # mechanism that is out of scope here.
    assert lookup("https://study.nus.edu.sg/programme").paginate is PaginateMode.NONE
    assert lookup("https://courses.leeds.ac.uk/x").paginate is PaginateMode.URL_PAGES
    assert lookup("https://www.ucl.ac.uk/x").paginate is PaginateMode.NONE
    assert lookup("https://www.manchester.ac.uk/x").paginate is PaginateMode.NONE
    assert lookup("https://www.polyu.edu.hk/x").paginate is PaginateMode.NONE


def test_leeds_carries_url_page_param():
    s = lookup("https://courses.leeds.ac.uk/x")
    assert s.params.get("page_param") == "page"
