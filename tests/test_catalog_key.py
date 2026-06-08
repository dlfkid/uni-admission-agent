"""catalog_key identity priority: group_code > source_url > name.

Regression guard for the Leeds index crawl where multiple distinct courses
mis-extracted to the SAME name ("A bachelor degree with a 2:1 (hons)") and
collapsed into one catalog row under name-based keying — silently dropping
courses. URL keying must keep them distinct.
"""
from __future__ import annotations

from src.storage.db_helpers import catalog_key


def test_group_code_takes_priority():
    assert catalog_key("ABC123", "Anything", source_url="https://x/y") == "group:abc123"


def test_url_preferred_over_name_when_no_group():
    k = catalog_key(None, "AI for Business MSc", source_url="https://courses.leeds.ac.uk/k198/ai-for-business-msc")
    assert k == "url:https://courses.leeds.ac.uk/k198/ai-for-business-msc"


def test_distinct_urls_with_same_bad_name_do_not_collapse():
    bad = "A bachelor degree with a 2:1 (hons)"
    k1 = catalog_key(None, bad, source_url="https://courses.leeds.ac.uk/k198/ai-for-business-msc")
    k2 = catalog_key(None, bad, source_url="https://courses.leeds.ac.uk/k164/ai-ethics-and-society-msc")
    assert k1 != k2, "distinct course URLs must yield distinct catalog keys even with identical names"


def test_url_canonicalized_query_and_trailing_slash_ignored():
    base = "https://courses.leeds.ac.uk/k198/ai-for-business-msc"
    k_clean = catalog_key(None, "X", source_url=base)
    k_query = catalog_key(None, "X", source_url=base + "?searchOrigin=query%3D%26type%3DPGT")
    k_slash = catalog_key(None, "X", source_url=base + "/")
    assert k_clean == k_query == k_slash


def test_name_fallback_when_no_url_or_group():
    assert catalog_key(None, "MSc Finance", source_url=None) == "name:msc-finance"
    assert catalog_key(None, "", source_url=None) == "name:unnamed"
