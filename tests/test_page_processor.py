"""Tests for src/scrapers/page_processor.py – process_page_for_program & process_pages_batch.

All LLM calls and DB operations are mocked.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional
from unittest.mock import MagicMock, patch, call

import pytest

from src.agents.cleaner_agent import (
    ParsedProgramData,
    ParsedTuition,
    ParsedDeadline,
    ParsedStudyOption,
)
from src.models.admission import CurrencyCode, StudyMode
from src.models.scraper_models import CrawlPageResult
from src.scrapers.page_processor import (
    extract_program_data_from_page,
    process_page_for_program,
    process_pages_batch,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_page(
    url: str = "https://example.com/prog",
    markdown: str = "# MSc Computer Science\n\nTuition: HK$ 350,000",
    html: Optional[str] = None,
    links: list | None = None,
) -> CrawlPageResult:
    return CrawlPageResult(
        url=url,
        markdown=markdown,
        char_count=len(markdown),
        links=links or [],
        status_code=200,
        html=html,
    )


def _make_parsed_data(
    *,
    faculty: str = "Faculty of Engineering",
    amount: Decimal = Decimal("350000"),
    currency: CurrencyCode = CurrencyCode.HKD,
) -> ParsedProgramData:
    return ParsedProgramData(
        faculty=faculty,
        tuition=ParsedTuition(amount=amount, currency=currency),
        study_options=[ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12)],
        deadlines=[ParsedDeadline(description="Main Round", cutoff_date=datetime(2025, 12, 31))],
    )


def _make_mock_cleaner(
    parsed: Optional[ParsedProgramData] = None,
) -> MagicMock:
    cleaner = MagicMock()
    cleaner.clean_markdown_with_critique.return_value = parsed
    return cleaner


def _make_mock_db() -> MagicMock:
    db = MagicMock()
    mock_program = MagicMock()
    db.upsert_program.return_value = (mock_program, True)
    return db


# ── process_page_for_program ────────────────────────────────────────


def test_process_page_success() -> None:
    page = _make_page()
    cleaner = _make_mock_cleaner(_make_parsed_data())
    db = _make_mock_db()

    success, error = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    assert success is True
    assert error is None
    cleaner.clean_markdown_with_critique.assert_called_once()
    db.upsert_program.assert_called_once()

    # Check the upsert data
    call_args = db.upsert_program.call_args
    program_data = call_args[0][0]
    assert program_data["academic_year"] == 2025
    assert program_data["faculty"] == "Faculty of Engineering"
    assert program_data["tuition_amount"] == Decimal("350000")
    assert program_data["currency"] == CurrencyCode.HKD


def test_process_page_no_markdown() -> None:
    page = _make_page(markdown="")
    cleaner = _make_mock_cleaner()
    db = _make_mock_db()

    success, error = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    assert success is False
    assert error == "No markdown content"
    cleaner.clean_markdown_with_critique.assert_not_called()


def test_process_page_no_parsed_data() -> None:
    page = _make_page()
    cleaner = _make_mock_cleaner(parsed=None)
    db = _make_mock_db()

    success, error = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    assert success is False
    assert "No structured data" in (error or "")


def test_process_page_html_fallback() -> None:
    """When markdown is suspiciously short, HTML should be passed to clean_markdown."""
    short_md = "x" * 10
    large_html = "<html>" + "a" * 5000 + "</html>"
    page = _make_page(markdown=short_md, html=large_html)

    cleaner = _make_mock_cleaner(_make_parsed_data())
    db = _make_mock_db()

    with patch("src.scrapers.page_processor.extract_program_name", return_value="MSc Test"):
        success, error = process_page_for_program(
            page=page, cleaner=cleaner, db_manager=db,
            univ_slug="hku", year=2025, current_depth=0,
        )

    assert success is True
    # The HTML should have been passed to clean_markdown instead of short markdown
    call_args = cleaner.clean_markdown_with_critique.call_args
    assert call_args[1]["markdown"] == large_html


def test_process_page_no_html_fallback_when_md_long_enough() -> None:
    """When markdown is long enough relative to HTML, use markdown as-is."""
    md = "# Test\n\n" + "Content " * 500
    html = "<html>" + "a" * (len(md) * 2) + "</html>"
    page = _make_page(markdown=md, html=html)

    cleaner = _make_mock_cleaner(_make_parsed_data())
    db = _make_mock_db()

    process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    # Markdown should be used (not HTML)
    call_args = cleaner.clean_markdown_with_critique.call_args
    content_used = call_args[1].get("markdown") or call_args[0][0]
    assert content_used == md


def test_process_page_with_deadlines_sorted() -> None:
    """Deadlines should be sorted chronologically with round numbers."""
    parsed = ParsedProgramData(
        faculty="School of Law",
        tuition=ParsedTuition(amount=Decimal("200000"), currency=CurrencyCode.HKD),
        study_options=[],
        deadlines=[
            ParsedDeadline(description="Round 2", cutoff_date=datetime(2025, 9, 1)),
            ParsedDeadline(description="Round 1", cutoff_date=datetime(2025, 3, 1)),
        ],
    )
    cleaner = _make_mock_cleaner(parsed)
    db = _make_mock_db()
    page = _make_page(markdown="# LLM Program\n\nDeadlines here")

    process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    call_args = db.upsert_program.call_args
    program_data = call_args[0][0]
    deadlines = program_data["deadlines"]
    # Should be sorted: Round 1 (March) < Round 2 (Sep)
    assert deadlines[0]["description"] == "Round 1"
    assert deadlines[0]["round"] == 1
    assert deadlines[1]["description"] == "Round 2"
    assert deadlines[1]["round"] == 2


def test_process_page_mixed_tz_deadlines() -> None:
    """Handle both tz-aware and tz-naive datetimes in sorting."""
    parsed = ParsedProgramData(
        faculty="Engineering",
        tuition=ParsedTuition(amount=Decimal("100000"), currency=CurrencyCode.USD),
        study_options=[],
        deadlines=[
            ParsedDeadline(description="Aware", cutoff_date=datetime(2025, 6, 1, tzinfo=timezone.utc)),
            ParsedDeadline(description="Naive", cutoff_date=datetime(2025, 3, 1)),
            ParsedDeadline(description="No date", cutoff_date=None),
        ],
    )
    cleaner = _make_mock_cleaner(parsed)
    db = _make_mock_db()
    page = _make_page()

    success, _ = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="test", year=2025, current_depth=0,
    )
    assert success is True

    call_args = db.upsert_program.call_args
    deadlines = call_args[0][0]["deadlines"]
    # Naive (March) < Aware (June) < None (max)
    assert deadlines[0]["description"] == "Naive"
    assert deadlines[1]["description"] == "Aware"
    assert deadlines[2]["description"] == "No date"


def test_process_page_from_browser_metadata() -> None:
    page = _make_page()
    cleaner = _make_mock_cleaner(_make_parsed_data())
    db = _make_mock_db()

    process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=1,
        from_browser=True,
    )

    call_args = db.upsert_program.call_args
    metadata = call_args[0][0]["extra_metadata"]
    assert metadata["from_browser"] is True
    assert metadata["crawl_depth"] == 1


def test_process_page_no_program_name() -> None:
    """If program name can't be extracted, should fail."""
    page = _make_page(markdown="No headings at all, just plain text without any useful info.")
    parsed = ParsedProgramData(
        faculty="Eng",
        tuition=ParsedTuition(amount=Decimal("100"), currency=CurrencyCode.HKD),
        study_options=[], deadlines=[],
    )
    cleaner = _make_mock_cleaner(parsed)
    db = _make_mock_db()

    success, error = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    assert success is False
    assert "program name" in (error or "").lower()


def test_process_page_falls_back_to_html_title_for_program_name() -> None:
    """When markdown has no heading but HTML title has program name, should still import."""
    page = _make_page(
        markdown="\n",
        html=(
            "<html><head><title>"
            "02007-DFA-DPA - Doctor of Business Administration | "
            "The Hong Kong Polytechnic University (PolyU)"
            "</title></head><body></body></html>"
        ),
    )
    parsed = ParsedProgramData(
        faculty="Faculty of Business",
        tuition=ParsedTuition(amount=Decimal("100"), currency=CurrencyCode.HKD),
        study_options=[], deadlines=[],
    )
    cleaner = _make_mock_cleaner(parsed)
    db = _make_mock_db()

    success, error = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="polyu", year=2026, current_depth=0,
    )

    assert success is True
    assert error is None
    call_args = db.upsert_program.call_args
    program_data = call_args[0][0]
    assert "Doctor of Business Administration" in str(program_data.get("name_en"))


def test_extract_program_data_uses_selected_anchor_text_when_title_is_generic() -> None:
    page = _make_page(
        markdown="\n",
        html="<html><head><title>Programmes | PolyU</title></head><body></body></html>",
    )
    cleaner = _make_mock_cleaner(
        ParsedProgramData(
            faculty="Faculty of Engineering",
            tuition=ParsedTuition(amount=Decimal("500"), currency=CurrencyCode.HKD),
            study_options=[],
            deadlines=[],
        )
    )

    program_data, error = extract_program_data_from_page(
        page=page,
        cleaner=cleaner,
        univ_slug="polyu",
        year=2026,
        current_depth=0,
        selected_anchor_text="MSc in Artificial Intelligence and Data Analytics",
    )

    assert error is None
    assert program_data is not None
    assert (
        program_data.get("name_en")
        == "MSc in Artificial Intelligence and Data Analytics"
    )


def test_process_page_llm_exception() -> None:
    page = _make_page()
    cleaner = MagicMock()
    cleaner.clean_markdown_with_critique.side_effect = RuntimeError("LLM crashed")
    db = _make_mock_db()

    success, error = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    assert success is False
    assert "LLM crashed" in (error or "")


# ── process_pages_batch ─────────────────────────────────────────────


def test_process_pages_batch_all_success() -> None:
    pages = [
        _make_page(url="https://example.com/p1", markdown="# Program A\n\nContent"),
        _make_page(url="https://example.com/p2", markdown="# Program B\n\nContent"),
    ]

    mock_parsed = _make_parsed_data()

    with (
        patch("src.scrapers.page_processor.LLMCleanerAgent") as MockCleaner,
        patch("src.scrapers.page_processor.DatabaseManager") as MockDB,
    ):
        mock_cleaner = _make_mock_cleaner(mock_parsed)
        MockCleaner.return_value = mock_cleaner
        mock_db = _make_mock_db()
        MockDB.return_value = mock_db

        router = MagicMock()
        imported, scouts, failed = process_pages_batch(
            pages=pages, router=router,
            univ_slug="hku", year=2025, current_depth=0,
        )

    assert imported == 2
    assert len(scouts) == 0
    assert len(failed) == 0


def test_process_pages_batch_partial_failure() -> None:
    pages = [
        _make_page(url="https://example.com/ok", markdown="# MSc Finance\n\nData"),
        _make_page(url="https://example.com/bad", markdown="# No data page\nNothing useful"),
    ]

    call_count = 0

    def mock_clean_markdown(markdown, source_url="", name_hints=None, academic_year=0):
        _ = markdown, source_url, name_hints, academic_year
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_parsed_data()
        return None  # Second page fails

    with (
        patch("src.scrapers.page_processor.LLMCleanerAgent") as MockCleaner,
        patch("src.scrapers.page_processor.DatabaseManager") as MockDB,
    ):
        mock_cleaner = MagicMock()
        mock_cleaner.clean_markdown_with_critique.side_effect = mock_clean_markdown
        MockCleaner.return_value = mock_cleaner
        mock_db = _make_mock_db()
        MockDB.return_value = mock_db

        router = MagicMock()
        imported, scouts, failed = process_pages_batch(
            pages=pages, router=router,
            univ_slug="hku", year=2025, current_depth=0,
        )

    assert imported == 1
    assert len(failed) == 1
    assert "https://example.com/bad" in failed


def test_process_pages_batch_skip_empty_markdown() -> None:
    pages = [
        _make_page(url="https://example.com/empty", markdown=""),
        _make_page(url="https://example.com/ok", markdown="# Real Program\n\nContent"),
    ]

    with (
        patch("src.scrapers.page_processor.LLMCleanerAgent") as MockCleaner,
        patch("src.scrapers.page_processor.DatabaseManager") as MockDB,
    ):
        mock_cleaner = _make_mock_cleaner(_make_parsed_data())
        MockCleaner.return_value = mock_cleaner
        mock_db = _make_mock_db()
        MockDB.return_value = mock_db

        router = MagicMock()
        imported, scouts, failed = process_pages_batch(
            pages=pages, router=router,
            univ_slug="hku", year=2025, current_depth=0,
        )

    # Only 1 processed (the empty one is skipped)
    assert imported == 1
    assert mock_cleaner.clean_markdown_with_critique.call_count == 1


# ── Quality gate integration ────────────────────────────────────────


def _make_empty_shell_parsed() -> ParsedProgramData:
    """ParsedProgramData with a faculty but no tuition/deadline/requirement.

    This represents the "LLM returned only structure, no actual data"
    failure mode that the quality gate is designed to catch.
    """
    return ParsedProgramData(faculty="Faculty of Engineering")


def test_quality_gate_routes_empty_shell_to_quarantine() -> None:
    """Empty-shell extraction must NOT hit upsert_program, but MUST hit
    upsert_quarantine."""
    page = _make_page(markdown="# MSc Computer Science\n\n(empty page body)")
    cleaner = _make_mock_cleaner(_make_empty_shell_parsed())
    db = _make_mock_db()
    db.upsert_quarantine = MagicMock()

    success, error = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    assert success is False
    assert error is not None
    assert "quarantine" in error.lower()
    db.upsert_program.assert_not_called()
    db.upsert_quarantine.assert_called_once()

    # Quarantine signals must include diagnostic data.
    kwargs = db.upsert_quarantine.call_args.kwargs
    assert kwargs["university_slug"] == "hku"
    assert kwargs["reason"].value == "empty_shell"
    assert kwargs["signals"]["deadline_count"] == 0
    assert kwargs["signals"]["has_tuition"] is False


def test_quality_gate_routes_noise_name_to_quarantine() -> None:
    """A name that matches the noise filter must be quarantined even when
    the page has tuition/deadline data."""
    page = _make_page(markdown="# Course Search\n\nFind your programme here")
    parsed = _make_parsed_data()  # has tuition + deadline
    cleaner = _make_mock_cleaner(parsed)
    db = _make_mock_db()
    db.upsert_quarantine = MagicMock()

    with patch("src.scrapers.page_processor.extract_program_name", return_value="Course Search"):
        success, error = process_page_for_program(
            page=page, cleaner=cleaner, db_manager=db,
            univ_slug="hku", year=2025, current_depth=0,
        )

    assert success is False
    db.upsert_program.assert_not_called()
    db.upsert_quarantine.assert_called_once()
    assert db.upsert_quarantine.call_args.kwargs["reason"].value == "noise_name"


def test_quality_gate_lets_good_program_through() -> None:
    """Sanity: a complete program with a real name still hits upsert_program."""
    page = _make_page()
    cleaner = _make_mock_cleaner(_make_parsed_data())
    db = _make_mock_db()
    db.upsert_quarantine = MagicMock()

    with patch("src.scrapers.page_processor.extract_program_name", return_value="MSc Computer Science"):
        success, error = process_page_for_program(
            page=page, cleaner=cleaner, db_manager=db,
            univ_slug="hku", year=2025, current_depth=0,
        )

    assert success is True
    assert error is None
    db.upsert_program.assert_called_once()
    db.upsert_quarantine.assert_not_called()


def test_successful_upsert_graduates_prior_quarantine() -> None:
    """If a page was quarantined previously and now extracts cleanly,
    the prior quarantine record must be auto-removed."""
    page = _make_page(url="https://example.com/prog")
    cleaner = _make_mock_cleaner(_make_parsed_data())
    db = _make_mock_db()
    db.upsert_quarantine = MagicMock()
    db.clear_quarantine = MagicMock(return_value=1)

    with patch("src.scrapers.page_processor.extract_program_name", return_value="MSc Computer Science"):
        success, error = process_page_for_program(
            page=page, cleaner=cleaner, db_manager=db,
            univ_slug="hku", year=2025, current_depth=0,
        )

    assert success is True
    db.upsert_program.assert_called_once()
    db.clear_quarantine.assert_called_once_with(
        university_slug="hku", source_url="https://example.com/prog"
    )


def test_quarantine_path_does_not_call_clear() -> None:
    """When the gate rejects, we do NOT call clear_quarantine — the new
    record IS the (overwriting) quarantine entry via upsert_quarantine."""
    page = _make_page(markdown="# MSc Computer Science")
    cleaner = _make_mock_cleaner(_make_empty_shell_parsed())
    db = _make_mock_db()
    db.upsert_quarantine = MagicMock()
    db.clear_quarantine = MagicMock()

    process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    db.upsert_program.assert_not_called()
    db.upsert_quarantine.assert_called_once()
    db.clear_quarantine.assert_not_called()


# ── Silent-failure quarantine: routes that previously bypassed the gate ──


def test_no_markdown_routes_to_quarantine() -> None:
    """A page with empty markdown must produce a quarantine entry so the
    URL is visible — not silently dropped with `0 programs imported`."""
    page = _make_page(url="https://e.edu/blocked", markdown="")
    cleaner = _make_mock_cleaner()
    db = _make_mock_db()
    db.upsert_quarantine = MagicMock()

    success, error = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    assert success is False
    assert error is not None
    cleaner.clean_markdown_with_critique.assert_not_called()  # markdown empty → never reaches LLM
    db.upsert_program.assert_not_called()
    db.upsert_quarantine.assert_called_once()
    kwargs = db.upsert_quarantine.call_args.kwargs
    assert kwargs["university_slug"] == "hku"
    assert kwargs["reason"].value == "no_markdown"
    assert kwargs["program_data"]["source_url"] == "https://e.edu/blocked"
    assert kwargs["program_data"]["academic_year"] == 2025


def test_cleaner_returns_none_routes_to_quarantine() -> None:
    """When the cleaner returns None (LLM said 'nothing to extract'), the
    URL must appear in quarantine with EXTRACTION_FAILED — this is the
    exact bug surfaced by smoke-testing Edinburgh accounting."""
    page = _make_page(url="https://e.edu/needs-interaction")
    cleaner = _make_mock_cleaner(parsed=None)  # LLM returned no data
    db = _make_mock_db()
    db.upsert_quarantine = MagicMock()

    success, error = process_page_for_program(
        page=page, cleaner=cleaner, db_manager=db,
        univ_slug="hku", year=2025, current_depth=0,
    )

    assert success is False
    cleaner.clean_markdown_with_critique.assert_called_once()  # we DID try the LLM
    db.upsert_program.assert_not_called()
    db.upsert_quarantine.assert_called_once()
    kwargs = db.upsert_quarantine.call_args.kwargs
    assert kwargs["reason"].value == "extraction_failed"
    assert kwargs["program_data"]["source_url"] == "https://e.edu/needs-interaction"
