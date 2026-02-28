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
    # No newlines at all → must hard-split with overlap
    text = "A" * 200
    chunks = split_markdown_chunks(text, max_chars=50)
    
    # Each chunk must not exceed max_chars
    assert all(len(c) <= 50 for c in chunks)
    
    # Verify overlapping: chunks should have overlap_ratio (default 20%) overlap
    # With max_chars=50 and 20% overlap, step_size = 40
    # For 200 chars: chunks at positions 0, 40, 80, 120, 160
    # So we expect 5 chunks
    assert len(chunks) >= 4  # At least 4 chunks for 200 chars with overlap
    
    # Verify first chunk starts with original text
    assert chunks[0] == text[:len(chunks[0])]
    
    # Verify last chunk ends with original text
    last_chunk_start = text.rfind(chunks[-1])
    assert last_chunk_start != -1
    assert text[last_chunk_start:] == chunks[-1]


def test_split_empty() -> None:
    assert split_markdown_chunks("", max_chars=100) == [""]


def test_split_with_overlap() -> None:
    """Test that chunks overlap correctly to prevent context truncation."""
    # Create a text with distinct markers every 10 chars
    text = "".join(f"BLOCK_{i:03d}_" for i in range(50))  # 500 chars total
    
    chunks = split_markdown_chunks(text, max_chars=100, overlap_ratio=0.2)
    
    # With max_chars=100 and 20% overlap, step_size=80
    # Expected chunks: 0-100, 80-180, 160-260, 240-340, 320-420, 400-500
    assert len(chunks) >= 5
    
    # Verify overlap: the end of chunk[0] should appear in chunk[1]
    if len(chunks) >= 2:
        # Last 20 chars of chunk 0 should be in the first 20+ chars of chunk 1
        overlap_text = chunks[0][-20:]
        assert overlap_text in chunks[1][:40], \
            f"Expected overlap not found. Chunk 0 end: {overlap_text}, Chunk 1 start: {chunks[1][:40]}"


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


# ── extract_links_with_text ──────────────────────────────────────────


def test_extract_links_with_text_basic() -> None:
    md = "[Program A](https://example.com/prog-a)\n[Program B](/prog-b)"
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    
    urls = [url for url, _ in pairs]
    texts = [text for _, text in pairs]
    
    assert "https://example.com/prog-a" in urls
    assert "https://example.com/prog-b" in urls
    assert "Program A" in texts
    assert "Program B" in texts


def test_extract_links_with_text_empty_anchor() -> None:
    md = "[](https://example.com/empty)"
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    
    assert len(pairs) == 1
    assert pairs[0][0] == "https://example.com/empty"
    assert pairs[0][1] == ""  # Empty anchor text


def test_extract_links_with_text_filters_assets() -> None:
    md = "[Logo](https://example.com/logo.png)\n[Valid](https://example.com/valid)"
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    
    urls = [url for url, _ in pairs]
    assert "https://example.com/logo.png" not in urls
    assert "https://example.com/valid" in urls


def test_extract_links_with_text_deduplicates() -> None:
    md = "[Link1](https://example.com/page)\n[Link2](https://example.com/page)"
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    
    # Should only return one pair since URL is same
    assert len(pairs) == 1
    assert pairs[0][0] == "https://example.com/page"


def test_extract_links_with_text_skips_anchors() -> None:
    md = "[Jump](#section)\n[Valid](https://example.com/valid)"
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    
    urls = [url for url, _ in pairs]
    assert len(urls) == 1
    assert urls[0] == "https://example.com/valid"


def test_extract_links_with_text_skips_mailto() -> None:
    md = "[Email](mailto:test@test.com)\n[Valid](https://example.com/valid)"
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    
    urls = [url for url, _ in pairs]
    assert len(urls) == 1
    assert urls[0] == "https://example.com/valid"


def test_extract_links_with_text_excludes_base_url() -> None:
    md = "[Home](https://example.com/)\n[Other](https://example.com/other)"
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    
    urls = [url for url, _ in pairs]
    assert "https://example.com/" not in urls
    assert "https://example.com/other" in urls


def test_extract_links_with_text_filters_login() -> None:
    md = "[Login](https://example.com/login)\n[Program](https://example.com/prog)"
    pairs = extract_links_with_text(md, base_url="https://example.com/")
    
    urls = [url for url, _ in pairs]
    assert not any("login" in url for url in urls)
    assert "https://example.com/prog" in urls
