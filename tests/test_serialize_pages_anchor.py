"""Regression test for the anchor-text URL-key mismatch.

The index card link carries a tracking query (?searchOrigin=...). After
crawl4ai fetches the detail page, page.url often comes back without that
query (or with a trailing-slash difference). _serialize_pages must still
map the crawled page to its index-card anchor text — otherwise the program
name falls back to detail-page body text and we get entry-requirement
sentences instead of the course title.
"""
from __future__ import annotations

from src.models.scraper_models import CrawlPageResult
from src.services.ingestion_pipeline import IngestionPipeline, _canonical_url_key


def test_anchor_resolves_despite_query_string_drift():
    # selected_link_texts keyed by the index candidate URL (with query).
    candidate_url = "https://courses.leeds.ac.uk/k198/ai-for-business-msc?searchOrigin=query%3D%26type%3DPGT"
    selected_link_texts = {candidate_url: "AI for Business MSc"}

    # The crawled page came back with the query stripped.
    page = CrawlPageResult(
        url="https://courses.leeds.ac.uk/k198/ai-for-business-msc",
        markdown="# Entry requirements\n\nA bachelor degree with a 2:1 (hons)",
        char_count=50,
    )

    rows = IngestionPipeline._serialize_pages(
        [page], depth=1, from_browser=False, selected_link_texts=selected_link_texts
    )
    assert rows[0]["selected_anchor_text"] == "AI for Business MSc"


def test_anchor_resolves_with_trailing_slash_drift():
    candidate_url = "https://x.edu/p/data-science-msc"
    page = CrawlPageResult(url=candidate_url + "/", markdown="x", char_count=1)
    rows = IngestionPipeline._serialize_pages(
        [page], depth=1, from_browser=False,
        selected_link_texts={candidate_url: "Data Science MSc"},
    )
    assert rows[0]["selected_anchor_text"] == "Data Science MSc"


def test_exact_match_still_works():
    url = "https://x.edu/p/finance-msc"
    rows = IngestionPipeline._serialize_pages(
        [CrawlPageResult(url=url, markdown="x", char_count=1)],
        depth=1, from_browser=False, selected_link_texts={url: "Finance MSc"},
    )
    assert rows[0]["selected_anchor_text"] == "Finance MSc"


def test_canonical_url_key_normalizes():
    base = "https://Courses.Leeds.AC.uk/k198/ai-for-business-msc"
    assert _canonical_url_key(base + "?q=1") == _canonical_url_key(base + "/")
    assert _canonical_url_key(base).startswith("https://courses.leeds.ac.uk/")
