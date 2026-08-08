"""Tests for src.core.parser — DataCleaner regex-based parsing."""

from decimal import Decimal

import pytest

from src.core.parser import DataCleaner
from src.models.admission import CurrencyCode, StudyMode


# ── parse_tuition ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected_amount, expected_currency",
    [
        ("HK$ 350,000", Decimal("350000"), CurrencyCode.HKD),
        ("HKD 100000", Decimal("100000"), CurrencyCode.HKD),
        ("US$ 50,000", Decimal("50000"), CurrencyCode.USD),
        ("USD 25,000", Decimal("25000"), CurrencyCode.USD),
        ("RMB 200,000", Decimal("200000"), CurrencyCode.CNY),
        ("CNY 180000", Decimal("180000"), CurrencyCode.CNY),
        ("£ 9,250", Decimal("9250"), CurrencyCode.GBP),
        ("GBP 15,000", Decimal("15000"), CurrencyCode.GBP),
    ],
)
def test_parse_tuition(
    text: str,
    expected_amount: Decimal,
    expected_currency: CurrencyCode,
) -> None:
    amount, currency = DataCleaner.parse_tuition(text)
    assert amount == expected_amount
    assert currency == expected_currency


def test_parse_tuition_none() -> None:
    amount, currency = DataCleaner.parse_tuition(None)
    assert amount is None
    assert currency is None


def test_parse_tuition_empty() -> None:
    amount, currency = DataCleaner.parse_tuition("")
    assert amount is None
    assert currency is None


def test_parse_tuition_no_match() -> None:
    amount, currency = DataCleaner.parse_tuition("Free admission")
    assert amount is None
    assert currency is None


# ── parse_study_options ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected_mode, expected_months",
    [
        ("Full-time 1 year", StudyMode.FULL_TIME.value, 12),
        ("Part-time 2 years", StudyMode.PART_TIME.value, 24),
        ("Full time 1.5 year", StudyMode.FULL_TIME.value, 18),
        ("全日制 1 年", StudyMode.FULL_TIME.value, 12),
        ("兼读制 24 个月", StudyMode.PART_TIME.value, 24),
        ("6 months", StudyMode.UNKNOWN.value, 6),
    ],
)
def test_parse_study_options(
    text: str, expected_mode: str, expected_months: int
) -> None:
    options = DataCleaner.parse_study_options(text)
    assert len(options) >= 1
    assert options[0]["mode"] == expected_mode
    assert options[0]["duration_months"] == expected_months


def test_parse_study_options_none() -> None:
    assert DataCleaner.parse_study_options(None) == []


def test_parse_study_options_empty() -> None:
    assert DataCleaner.parse_study_options("") == []


def test_parse_study_options_no_duration() -> None:
    # No recognisable duration → empty list
    result = DataCleaner.parse_study_options("Full-time")
    assert result == []


# ── parse_deadlines ───────────────────────────────────────────────────


def test_parse_deadlines_multiple() -> None:
    text = "May 31, 2026; March 31, 2026"
    results = DataCleaner.parse_deadlines(text)
    assert len(results) == 2
    # Sorted chronologically: March before May
    assert results[0]["round"] == 1
    assert "2026-03-31" in results[0]["cutoff_date"]
    assert results[1]["round"] == 2
    assert "2026-05-31" in results[1]["cutoff_date"]


def test_parse_deadlines_single() -> None:
    results = DataCleaner.parse_deadlines("December 15, 2025")
    assert len(results) == 1
    assert results[0]["round"] == 1
    assert "2025-12-15" in results[0]["cutoff_date"]


def test_parse_deadlines_none() -> None:
    assert DataCleaner.parse_deadlines(None) == []


def test_parse_deadlines_empty() -> None:
    assert DataCleaner.parse_deadlines("") == []


def test_parse_deadlines_unparseable() -> None:
    # Completely non-date text
    assert DataCleaner.parse_deadlines("Contact us for details") == []


def test_parse_deadlines_rejects_implausible_year_from_fuzzy_misparse() -> None:
    """Regression: a real Manchester golden-sample line that merely
    mentions "deadline" in passing ("...pay a tuition fee deposit of
    £2,500 by the deadline stated in your offer letter...") was fed
    whole into dateutil.parse(fuzzy=True), which misread "2,500" as a
    bare 3-digit year and fabricated cutoff_date=0500-02-08 — a line
    with no real date in it produced one anyway. The line still contains
    the word "deadline" (the caller's own selection heuristic is keyword-
    based and can't rule this out), so the guard must live here."""
    line = (
        "If you are successful in receiving an offer, you will be "
        "required to pay a tuition fee deposit of £2,500 by the deadline "
        "stated in your offer letter to confirm your place."
    )
    assert DataCleaner.parse_deadlines(line) == []


def test_parse_deadlines_keeps_genuine_date_alongside_implausible_line() -> None:
    """The plausible-year guard must reject only the bad line, not
    poison the whole batch — a genuine deadline elsewhere in the same
    multi-line input still comes through."""
    text = (
        "If you are successful in receiving an offer, you will be "
        "required to pay a tuition fee deposit of £2,500 by the deadline "
        "stated in your offer letter to confirm your place.\n"
        "Application deadline: 31 May 2026"
    )
    results = DataCleaner.parse_deadlines(text)
    assert len(results) == 1
    assert "2026-05-31" in results[0]["cutoff_date"]
