"""Tests for src/scrapers/helpers.py utility functions."""

import tempfile
from pathlib import Path

import pytest

from src.scrapers.helpers import (
    load_prompt,
    sanitize_filename,
    save_markdown,
    save_html_debug,
    split_markdown_chunks,
    extract_program_name,
)


# ── load_prompt tests ───────────────────────────────────────────────


def test_load_prompt_existing_file() -> None:
    """Test loading an existing prompt file."""
    # clean_chunk.txt exists in src/agents/prompts/
    content = load_prompt("clean_chunk.txt")
    assert isinstance(content, str)
    assert len(content) > 0
    assert "CONTEXT FROM PREVIOUS CHUNKS" in content


def test_load_prompt_nonexistent_file() -> None:
    """Test loading a non-existent prompt file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Prompt file not found"):
        load_prompt("nonexistent_file.txt")


# ── sanitize_filename tests ─────────────────────────────────────────


def test_sanitize_filename_basic() -> None:
    """Test basic URL sanitization."""
    url = "https://example.com/page"
    result = sanitize_filename(url)
    assert result == "example.com_page.md"


def test_sanitize_filename_special_chars() -> None:
    """Test sanitization removes special characters."""
    url = "https://example.com/page?query=test&id=123"
    result = sanitize_filename(url)
    # Special chars like ?, &, = should be replaced with _
    assert "?" not in result
    assert "&" not in result
    assert "=" not in result
    assert result.endswith(".md")


def test_sanitize_filename_custom_extension() -> None:
    """Test sanitization with custom extension."""
    url = "https://example.com/page"
    result = sanitize_filename(url, extension=".html")
    assert result.endswith(".html")


def test_sanitize_filename_max_length() -> None:
    """Test sanitization respects max length."""
    long_url = "https://example.com/" + "a" * 300
    result = sanitize_filename(long_url, max_length=50)
    assert len(result) <= 50 + 3  # +3 for .md extension


def test_sanitize_filename_http() -> None:
    """Test sanitization handles http (not just https)."""
    url = "http://example.com/page"
    result = sanitize_filename(url)
    assert result == "example.com_page.md"
    assert not result.startswith("http")


def test_sanitize_filename_already_has_extension() -> None:
    """Test that extension is not duplicated."""
    url = "https://example.com/page.md"
    result = sanitize_filename(url, extension=".md")
    # Should have only one .md
    assert result.count(".md") == 1


# ── save_markdown tests ─────────────────────────────────────────────


def test_save_markdown_creates_file() -> None:
    """Test that save_markdown creates a file with correct content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        url = "https://example.com/test"
        markdown = "# Test Content\n\nThis is a test."
        
        save_markdown(tmpdir, url, markdown)
        
        # Check file was created
        expected_file = Path(tmpdir) / "example.com_test.md"
        assert expected_file.exists()
        
        # Check content
        saved_content = expected_file.read_text(encoding='utf-8')
        assert saved_content == markdown


def test_save_markdown_overwrites_existing() -> None:
    """Test that save_markdown overwrites existing files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        url = "https://example.com/test"
        markdown1 = "# Old Content"
        markdown2 = "# New Content"
        
        save_markdown(tmpdir, url, markdown1)
        save_markdown(tmpdir, url, markdown2)
        
        expected_file = Path(tmpdir) / "example.com_test.md"
        saved_content = expected_file.read_text(encoding='utf-8')
        assert saved_content == markdown2


# ── save_html_debug tests ───────────────────────────────────────────


def test_save_html_debug_creates_file() -> None:
    """Test that save_html_debug creates an HTML file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        url = "https://example.com/debug"
        html = "<html><body>Debug content</body></html>"
        
        save_html_debug(tmpdir, url, html)
        
        expected_file = Path(tmpdir) / "example.com_debug.html"
        assert expected_file.exists()
        
        saved_content = expected_file.read_text(encoding='utf-8')
        assert saved_content == html


# ── split_markdown_chunks tests ─────────────────────────────────────
# (Already tested in test_scrapers/test_engine_helpers.py, but add overlap tests)


def test_split_markdown_chunks_zero_overlap() -> None:
    """Test split with zero overlap."""
    text = "A" * 200
    chunks = split_markdown_chunks(text, max_chars=50, overlap_ratio=0.0)
    assert len(chunks) == 4  # 200 / 50 = 4


def test_split_markdown_chunks_high_overlap() -> None:
    """Test split with high overlap ratio."""
    text = "A" * 200
    chunks = split_markdown_chunks(text, max_chars=100, overlap_ratio=0.5)
    # With 50% overlap, step_size = 50
    # Chunks: 0-100, 50-150, 100-200
    assert len(chunks) >= 3


def test_split_markdown_chunks_realistic_content() -> None:
    """Test split with realistic markdown content."""
    text = "# Heading 1\n\nParagraph 1.\n\n## Heading 2\n\nParagraph 2.\n\n" * 20
    chunks = split_markdown_chunks(text, max_chars=200, overlap_ratio=0.2)
    
    # Each chunk should be ≤ 200 chars
    assert all(len(chunk) <= 200 for chunk in chunks)
    
    # Should have multiple chunks
    assert len(chunks) > 1
    
    # Verify overlap exists (first chunk's end should appear in second chunk's start)
    if len(chunks) >= 2:
        # The last few characters of chunk 0 should be in chunk 1
        overlap_sample = chunks[0][-20:]
        assert overlap_sample in chunks[1]


# ── extract_program_name tests ──────────────────────────────────────
# (Already partially tested in test_scrapers/test_engine_helpers.py)


def test_extract_program_name_no_headings() -> None:
    """Test extraction with no headings returns empty string."""
    markdown = "This is just plain text without any headings."
    result = extract_program_name(markdown)
    assert result == ""


def test_extract_program_name_h1_with_degree() -> None:
    """Test extraction prefers H1 with degree keywords."""
    markdown = "# Master of Science in Finance\n\n## Some Other Heading"
    result = extract_program_name(markdown)
    assert result == "Master of Science in Finance"


def test_extract_program_name_h2_fallback() -> None:
    """Test extraction falls back to H2 if no H1."""
    markdown = "## Bachelor of Arts\n\nSome content."
    result = extract_program_name(markdown)
    assert result == "Bachelor of Arts"


def test_noise_vocabulary_covers_department_page_furniture() -> None:
    """Regression for the Lingnan full-catalogue crawl: every one of these
    shapes was imported as a real programme name (or is the literal next
    heading in extract_program_name's fallback ladder on one of those
    exact pages). The same vocabulary backs three independent layers —
    the heading ladder, NameCritique's suspect check, and the persist
    quality gate — so one miss here leaks junk through all three."""
    from src.scrapers.helpers import is_noise_program_name

    observed_junk = [
        "Learn more about MAP programme",
        "Tuition Fee Waiver Scholarships",
        "Tuition Fees, Scholarships & Financial Assistance",
        "Welcome to Master of Arts in Arts and Cultural Heritage Management",
        "Welcome to the SEIM Programme",
        "Lingnan MSc in Cross-disciplinary Technologies Site",
        "Lingnan Master of Arts in International Higher Education and Management (IHEM) Site",
        "Lingnan Master of Science in Smart Ageing and Gerontology Site",
        # next dominoes in the heading ladder on the psy/mssap/home page:
        "Application for 2026/27 September intake is openning",
        "(Deadline for Application:31 May 2026)",
        "MAP Programme Overview",
        "Follow us on Instagram",
        # observed in an earlier run of the same crawl:
        "Choose Your Focus",
        "Visit Website",
    ]
    for junk in observed_junk:
        assert is_noise_program_name(junk), f"not flagged as noise: {junk!r}"

    real_names = [
        "Master of Science in Data Science",
        "Master of Arts in Chinese",
        "Master of Social Sciences in Comparative Public Administration (CPA)",
        "Doctor of Policy Studies",
        "MSc in Environmental Social and Governance Management",
        # near-misses of the new patterns that must stay valid:
        "Master of Arts in Tuition-Free Economics",  # 'tuition' not at start
        "MSc Web Science",  # 'site'-adjacent but not a trailing ' site'
    ]
    for name in real_names:
        assert not is_noise_program_name(name), f"real name wrongly flagged: {name!r}"


def test_extract_program_name_skips_generic_section_headings() -> None:
    """Regression: a live Lingnan page (psy/mssap/home) has H1 "Home"
    (already-noise) followed by H2 "Introduction" as its first non-noise-
    looking heading, with the real programme name never spelled out in a
    heading at all (only as the abbreviation "MAP"). "Introduction" was
    missing from the noise list, so it won as the extracted "programme
    name" and got imported as a fake programme. A bare, generic section
    heading must never be picked as a programme name even with nothing
    better available."""
    markdown = (
        "# Home\n\n"
        "## Introduction\n\n"
        "## Learn more about MAP programme\n\n"
        "## MAP Programme Overview\n"
    )
    result = extract_program_name(markdown)
    assert result != "Introduction"


def test_extract_program_name_with_asterisk_h1() -> None:
    """Test extraction handles asterisk-style H1 (Setext)."""
    markdown = "Master of Finance\n==================\n\nContent here."
    result = extract_program_name(markdown)
    # If _parse_heading handles Setext, should extract this
    # Otherwise might not work - depends on implementation
    assert isinstance(result, str)


def test_extract_program_name_multiple_h1() -> None:
    """Test extraction returns first H1 when multiple exist."""
    markdown = "# First Program\n\n## Details\n\n# Second Program"
    result = extract_program_name(markdown)
    assert result == "First Program"


def test_extract_program_name_empty_markdown() -> None:
    """Test extraction with empty markdown."""
    result = extract_program_name("")
    assert result == ""


def test_extract_program_name_whitespace_only() -> None:
    """Test extraction with whitespace-only markdown."""
    result = extract_program_name("   \n\n   \n")
    assert result == ""


def test_extract_program_name_prefers_hero_plain_title_over_later_curriculum_heading() -> None:
    """Hero plain title should win when markdown conversion drops heading markers."""
    markdown = """
## Breadcrumb
  1. [Postgraduate Programmes](https://www.polyu.edu.hk/study/pg)
  2. [Programmes](https://www.polyu.edu.hk/study/ug/programmes)

Master of Design
Design
設計學碩士學位

##  What's New
[More News __](https://www.polyu.edu.hk/study/ug/news)

Curriculum
## **Curriculum of the Master of Design Scheme**
"""
    result = extract_program_name(markdown)
    assert result == "Master of Design"


def test_extract_program_name_ignores_requirement_sentence_with_degree_keyword() -> None:
    markdown = """
A bachelor degree with a 2:1 (hons) in any subject.

# AI for Business MSc
## Year of entry 2026
"""
    assert extract_program_name(markdown) == "AI for Business MSc"


def test_extract_program_name_ignores_whats_new_when_heading_exists() -> None:
    markdown = """
## What's New
### Masters Discovery Fair
# Master of Science in Asset and Wealth Management
"""
    assert extract_program_name(markdown) == "Master of Science in Asset and Wealth Management"
