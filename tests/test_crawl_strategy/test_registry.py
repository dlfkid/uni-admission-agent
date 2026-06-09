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
