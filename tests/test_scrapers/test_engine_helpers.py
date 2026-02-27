"""Tests for deterministic helper functions in src.scrapers modules."""

import pytest

from src.scrapers.helpers import (
    split_markdown_chunks,
    extract_program_name,
)
from src.scrapers.link_parser import (
    extract_links,
    extract_links_with_text,
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


def test_extract_skips_cookie_banner() -> None:
    """Real-world case: cookie/privacy headings precede the actual course name."""
    md = (
        "## Changes to our privacy policy\n"
        "We've updated our privacy policy.\n\n"
        "## Tell us whether you accept cookies\n"
        "We use cookies to collect information.\n\n"
        "## Your privacy options\n"
        "### Our use of cookies\n\n"
        "#  AI for Business MSc \n"
        "## Year of entry 2026\n"
    )
    assert extract_program_name(md) == "AI for Business MSc"


def test_extract_degree_keyword_in_h2() -> None:
    """Degree keyword in H2 should be preferred over a generic non-degree H1."""
    md = (
        "# Welcome to Our University\n"
        "## MSc Computer Science\n"
        "## Programme Overview\n"
    )
    assert extract_program_name(md) == "MSc Computer Science"


def test_extract_degree_keyword_in_h1() -> None:
    """Degree keyword in H1 takes priority."""
    md = (
        "## Navigation\n"
        "# PhD in Physics\n"
        "## Entry Requirements\n"
    )
    assert extract_program_name(md) == "PhD in Physics"


def test_extract_noise_only_headings() -> None:
    """If all headings are noise, return first heading anyway."""
    md = (
        "## Cookie Settings\n"
        "## Your privacy options\n"
    )
    # Falls through all passes; returns first heading
    assert extract_program_name(md) == "Cookie Settings"


def test_extract_masters_keyword() -> None:
    """'Masters' as keyword should be recognized."""
    md = (
        "## Skip to main content\n"
        "## Masters in Data Science\n"
    )
    assert extract_program_name(md) == "Masters in Data Science"


def test_extract_pgdip_keyword() -> None:
    """PGDip/PGCert programs should be recognized."""
    md = "## PGDip Nursing\n## Overview\n"
    assert extract_program_name(md) == "PGDip Nursing"


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


# ── extract_links_with_text ───────────────────────────────────────────


def test_links_with_text_captures_anchor() -> None:
    md = "[MSc Finance](https://example.com/msc-finance)\n[BA English](/ba-english)"
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    urls = [u for u, _ in pairs]
    texts = [t for _, t in pairs]
    assert "https://example.com/msc-finance" in urls
    assert "https://example.com/ba-english" in urls
    assert "MSc Finance" in texts
    assert "BA English" in texts


def test_links_with_text_filters_same_as_extract() -> None:
    md = (
        "[Logo](https://example.com/logo.png)\n"
        "[Login](https://example.com/login)\n"
        "[Prog](https://example.com/prog)\n"
        "[Home](https://example.com/)\n"
        "[Jump](#section)"
    )
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    urls = [u for u, _ in pairs]
    assert len(urls) == 1
    assert urls[0] == "https://example.com/prog"


def test_links_with_text_deduplicates() -> None:
    md = (
        "[Link A](https://example.com/prog)\n"
        "[Link B](https://example.com/prog)\n"
    )
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    assert len(pairs) == 1
    # First occurrence wins
    assert pairs[0] == ("https://example.com/prog", "Link A")


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
