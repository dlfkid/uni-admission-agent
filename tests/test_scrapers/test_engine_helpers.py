"""Tests for deterministic helper functions in src.scrapers modules."""

import pytest

from src.scrapers.helpers import (
    split_markdown_chunks,
    extract_program_name,
)
from src.scrapers.link_parser import (
    extract_links,
    detect_page_type,
)
from src.models.scraper_models import PageType


# ── split_markdown_chunks ────────────────────────────────────────────


def test_split_short_text() -> None:
    text = "Hello world"
    chunks = split_markdown_chunks(text, max_chars=100)
    assert chunks == [text]


def test_split_exact_boundary() -> None:
    text = "A" * 100
    chunks = split_markdown_chunks(text, max_chars=100)
    assert chunks == [text]


def test_split_on_paragraph() -> None:
    text = "Part A content.\n\nPart B content."
    chunks = split_markdown_chunks(text, max_chars=20)
    assert len(chunks) >= 2
    # First chunk should end before the second paragraph
    assert "Part A" in chunks[0]


def test_split_hard_fallback() -> None:
    # No newlines at all → must hard-split
    text = "A" * 200
    chunks = split_markdown_chunks(text, max_chars=50)
    assert all(len(c) <= 50 for c in chunks)
    assert "".join(chunks) == text


def test_split_empty() -> None:
    assert split_markdown_chunks("", max_chars=100) == [""]


# ── extract_program_name ─────────────────────────────────────────────


def test_extract_h1() -> None:
    md = "# Master of Science in Finance\n\nSome body text."
    assert extract_program_name(md) == "Master of Science in Finance"


def test_extract_h2() -> None:
    md = "## Bachelor of Arts\n\nContent here."
    assert extract_program_name(md) == "Bachelor of Arts"


def test_extract_prefers_h1() -> None:
    md = "# Program Title\n## Subtitle"
    assert extract_program_name(md) == "Program Title"


def test_extract_no_heading() -> None:
    md = "Just plain text, no headings."
    assert extract_program_name(md) == ""


# ── extract_links ─────────────────────────────────────────────────────


def test_extract_links_markdown() -> None:
    md = "[Program A](https://example.com/prog-a)\n[Program B](/prog-b)"
    links = extract_links(md, base_url="https://example.com/")
    assert "https://example.com/prog-a" in links
    assert "https://example.com/prog-b" in links


def test_extract_links_raw_urls() -> None:
    md = "Visit https://example.com/apply for details."
    links = extract_links(md, base_url="https://example.com/")
    assert "https://example.com/apply" in links


def test_extract_links_filters_assets() -> None:
    md = "[Logo](https://example.com/logo.png)\n[Style](https://example.com/app.css)"
    links = extract_links(md, base_url="https://example.com/")
    assert all(".png" not in link and ".css" not in link for link in links)


def test_extract_links_filters_login() -> None:
    md = "[Login](https://example.com/login)\n[Program](https://example.com/prog)"
    links = extract_links(md, base_url="https://example.com/")
    assert all("login" not in link for link in links)
    assert "https://example.com/prog" in links


def test_extract_links_excludes_base() -> None:
    md = "[Home](https://example.com/)\n[Other](https://example.com/other)"
    links = extract_links(md, base_url="https://example.com/")
    assert "https://example.com/" not in links
    assert "https://example.com" not in links


def test_extract_links_skips_anchors_and_mailto() -> None:
    md = "[Jump](#section)\n[Email](mailto:test@test.com)"
    links = extract_links(md, base_url="https://example.com/")
    assert links == []


# ── detect_page_type ──────────────────────────────────────────────────


def test_detect_detail_by_content() -> None:
    md = "# MSc Finance\n\n## Tuition Fee\nHK$ 350,000\n\n## Application Deadline\nDec 2025"
    assert detect_page_type(md, link_count=5) == PageType.DETAIL


def test_detect_index_by_links() -> None:
    md = "# Our Programmes\n\nBrowse all available courses below."
    assert detect_page_type(md, link_count=30) == PageType.INDEX


def test_detect_detail_few_links_no_signals() -> None:
    md = "# About This Program\n\nGeneral information without keywords."
    assert detect_page_type(md, link_count=3) == PageType.DETAIL


def test_detect_detail_signals_override_links() -> None:
    """Even with many links, content signals should force DETAIL."""
    md = "# MSc Data Science\n\nEntry requirements: BSc in CS."
    assert detect_page_type(md, link_count=50) == PageType.DETAIL
