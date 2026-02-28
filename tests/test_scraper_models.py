"""Tests for src/models/scraper_models.py Pydantic models."""

import pytest
from pydantic import ValidationError

from src.models.scraper_models import (
    CrawlPageResult,
    ExtractedLinks,
    FilteredLinks,
    ScoutedLink,
    ScoutedLinks,
    ScoutReport,
    PageType,
    PageTypeResult,
    ProgramContext,
)


# ── CrawlPageResult tests ───────────────────────────────────────────────


def test_crawl_page_result_basic() -> None:
    result = CrawlPageResult(
        url="https://example.com",
        markdown="# Test Page",
        char_count=1000,
    )
    assert result.url == "https://example.com"
    assert result.markdown == "# Test Page"
    assert result.char_count == 1000
    assert result.links == []
    assert result.status_code is None
    assert result.html is None


def test_crawl_page_result_with_all_fields() -> None:
    result = CrawlPageResult(
        url="https://example.com/page",
        markdown="# Page\nContent here.",
        char_count=500,
        links=["https://example.com/link1", "https://example.com/link2"],
        status_code=200,
        html="<html><body>Test</body></html>",
    )
    assert len(result.links) == 2
    assert result.status_code == 200
    assert result.html is not None


# ── ExtractedLinks tests ────────────────────────────────────────────────


def test_extracted_links_empty() -> None:
    extracted = ExtractedLinks()
    assert extracted.links == []


def test_extracted_links_with_urls() -> None:
    extracted = ExtractedLinks(
        links=["https://example.com/prog1", "https://example.com/prog2"]
    )
    assert len(extracted.links) == 2


# ── FilteredLinks tests ─────────────────────────────────────────────────


def test_filtered_links_empty() -> None:
    filtered = FilteredLinks()
    assert filtered.urls == []


def test_filtered_links_with_urls() -> None:
    filtered = FilteredLinks(
        urls=["https://example.com/course1", "https://example.com/course2"]
    )
    assert len(filtered.urls) == 2


# ── ScoutedLink tests ───────────────────────────────────────────────────


def test_scouted_link_creation() -> None:
    link = ScoutedLink(
        url="https://example.com/valuable",
        reason="Contains detailed admission info",
        confidence="high",
    )
    assert link.url == "https://example.com/valuable"
    assert link.reason == "Contains detailed admission info"
    assert link.confidence == "high"


# ── ScoutedLinks tests ──────────────────────────────────────────────────


def test_scouted_links_empty() -> None:
    scouted = ScoutedLinks()
    assert scouted.links == []


def test_scouted_links_with_links() -> None:
    link1 = ScoutedLink(url="https://example.com/1", reason="High value", confidence="high")
    link2 = ScoutedLink(url="https://example.com/2", reason="Medium value", confidence="medium")
    
    scouted = ScoutedLinks(links=[link1, link2])
    assert len(scouted.links) == 2
    assert scouted.links[0].confidence == "high"


# ── ScoutReport tests ───────────────────────────────────────────────────


def test_scout_report_empty() -> None:
    report = ScoutReport()
    assert report.explored_urls == []
    assert report.failed_urls == []
    assert report.scouted_links == []
    assert report.depth_reached == 0
    assert report.programs_imported == 0


def test_scout_report_with_data() -> None:
    link = ScoutedLink(url="https://example.com/prog", reason="Valid", confidence="high")
    report = ScoutReport(
        explored_urls=["https://example.com/1", "https://example.com/2"],
        failed_urls=["https://example.com/broken"],
        scouted_links=[link],
        depth_reached=2,
        programs_imported=5,
    )
    assert len(report.explored_urls) == 2
    assert len(report.failed_urls) == 1
    assert report.depth_reached == 2
    assert report.programs_imported == 5


# ── PageType tests ──────────────────────────────────────────────────────


def test_page_type_enum() -> None:
    assert PageType.INDEX == "index"
    assert PageType.DETAIL == "detail"


# ── PageTypeResult tests ────────────────────────────────────────────────


def test_page_type_result_index() -> None:
    result = PageTypeResult(
        page_type=PageType.INDEX,
        confidence=0.95,
        reasoning="Many links found",
    )
    assert result.page_type == PageType.INDEX
    assert result.confidence == 0.95


def test_page_type_result_detail() -> None:
    result = PageTypeResult(
        page_type=PageType.DETAIL,
        confidence=0.85,
        reasoning="Contains admission requirements",
    )
    assert result.page_type == PageType.DETAIL


def test_page_type_result_confidence_validation() -> None:
    """Confidence must be between 0.0 and 1.0."""
    with pytest.raises(ValidationError):
        PageTypeResult(
            page_type=PageType.DETAIL,
            confidence=1.5,  # Invalid: > 1.0
            reasoning="Test",
        )
    
    with pytest.raises(ValidationError):
        PageTypeResult(
            page_type=PageType.DETAIL,
            confidence=-0.1,  # Invalid: < 0.0
            reasoning="Test",
        )


def test_page_type_result_reasoning_max_length() -> None:
    """Reasoning must not exceed 100 characters."""
    long_reasoning = "A" * 150
    with pytest.raises(ValidationError):
        PageTypeResult(
            page_type=PageType.INDEX,
            confidence=0.8,
            reasoning=long_reasoning,
        )


# ── ProgramContext tests ────────────────────────────────────────────────


def test_program_context_basic() -> None:
    ctx = ProgramContext(
        name_en="Master of Science in Computer Science",
        program_group_code="hku-msc-cs",
    )
    assert ctx.name_en == "Master of Science in Computer Science"
    assert ctx.program_group_code == "hku-msc-cs"
    assert ctx.faculty is None
    assert ctx.tuition_amount is None


def test_program_context_with_all_fields() -> None:
    ctx = ProgramContext(
        name_en="MSc in Data Science",
        program_group_code="hku-msc-ds",
        faculty="Engineering",
        tuition_amount=250000.0,
        currency="HKD",
        frequency="per year",
    )
    assert ctx.faculty == "Engineering"
    assert ctx.tuition_amount == 250000.0
    assert ctx.currency == "HKD"
    assert ctx.frequency == "per year"


def test_program_context_normalize_name_basic() -> None:
    ctx = ProgramContext(
        name_en="Master of Science in Computer Science",
        program_group_code="test-prog",
    )
    normalized = ctx.normalize_name()
    assert normalized == "masterofscienceincomputerscience"


def test_program_context_normalize_name_with_punctuation() -> None:
    ctx = ProgramContext(
        name_en="M.Sc. in Data Science!",
        program_group_code="test-prog",
    )
    normalized = ctx.normalize_name()
    assert normalized == "mscindatascience"


def test_program_context_normalize_name_with_spaces() -> None:
    ctx = ProgramContext(
        name_en="  MBA  Program  ",
        program_group_code="test-prog",
    )
    normalized = ctx.normalize_name()
    assert normalized == "mbaprogram"


def test_program_context_normalize_name_case_insensitive() -> None:
    ctx = ProgramContext(
        name_en="MASTER OF BUSINESS ADMINISTRATION",
        program_group_code="test-prog",
    )
    normalized = ctx.normalize_name()
    assert normalized == "masterofbusinessadministration"


def test_program_context_normalize_name_with_numbers() -> None:
    ctx = ProgramContext(
        name_en="MSc 2024 Edition",
        program_group_code="test-prog",
    )
    normalized = ctx.normalize_name()
    assert normalized == "msc2024edition"


def test_program_context_normalize_name_special_chars() -> None:
    ctx = ProgramContext(
        name_en="Master's (Honours) - Science & Tech",
        program_group_code="test-prog",
    )
    normalized = ctx.normalize_name()
    # Only alphanumeric characters remain (lowercase)
    assert normalized == "mastershonourssciencetech"
