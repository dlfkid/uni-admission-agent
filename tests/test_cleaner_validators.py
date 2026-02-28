"""Tests for cleaner_agent validators and data processing."""

from datetime import datetime, timezone
from decimal import Decimal

from src.agents.cleaner_agent import (
    ParsedTuition,
    ParsedDeadline,
    ParsedStudyOption,
    ParsedProgramData,
    _merge_parsed_data,
)
from src.models.admission import StudyMode


# ── ParsedTuition amount validator ──────────────────────────────────


def test_tuition_amount_normal_decimal() -> None:
    """Test normal decimal string."""
    tuition = ParsedTuition(amount="350000.00", currency="HKD")
    assert tuition.amount == Decimal("350000.00")


def test_tuition_amount_with_commas() -> None:
    """Test amount with thousand separators."""
    tuition = ParsedTuition(amount="350,000", currency="HKD")
    assert tuition.amount == Decimal("350000")


def test_tuition_amount_lowercase_k() -> None:
    """Test 'k' suffix for thousands."""
    tuition = ParsedTuition(amount="14k", currency="GBP")
    assert tuition.amount == Decimal("14000")


def test_tuition_amount_uppercase_k() -> None:
    """Test 'K' suffix for thousands."""
    tuition = ParsedTuition(amount="350K", currency="HKD")
    assert tuition.amount == Decimal("350000")


def test_tuition_amount_decimal_k() -> None:
    """Test decimal with 'k' suffix."""
    tuition = ParsedTuition(amount="1.5k", currency="USD")
    assert tuition.amount == Decimal("1500")


def test_tuition_amount_lowercase_m() -> None:
    """Test 'm' suffix for millions."""
    tuition = ParsedTuition(amount="1.5m", currency="HKD")
    assert tuition.amount == Decimal("1500000")


def test_tuition_amount_uppercase_m() -> None:
    """Test 'M' suffix for millions."""
    tuition = ParsedTuition(amount="2M", currency="USD")
    assert tuition.amount == Decimal("2000000")


def test_tuition_amount_with_spaces() -> None:
    """Test amount with spaces."""
    tuition = ParsedTuition(amount=" 350 000 ", currency="HKD")
    assert tuition.amount == Decimal("350000")


# ── Deadline datetime handling ──────────────────────────────────────


def test_deadline_none_cutoff() -> None:
    """Test deadline with no cutoff date."""
    deadline = ParsedDeadline(description="Rolling admission", cutoff_date=None)
    assert deadline.cutoff_date is None


def test_deadline_naive_datetime() -> None:
    """Test deadline with naive (no timezone) datetime."""
    dt = datetime(2026, 12, 31, 23, 59)
    deadline = ParsedDeadline(description="Round 1", cutoff_date=dt)
    assert deadline.cutoff_date == dt
    assert deadline.cutoff_date.tzinfo is None


def test_deadline_aware_datetime() -> None:
    """Test deadline with timezone-aware datetime."""
    dt = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
    deadline = ParsedDeadline(description="Round 1", cutoff_date=dt)
    assert deadline.cutoff_date == dt
    assert deadline.cutoff_date.tzinfo is not None


# ── ParsedStudyOption ───────────────────────────────────────────────


def test_study_option_fulltime() -> None:
    """Test full-time study option."""
    option = ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12)
    assert option.mode == StudyMode.FULL_TIME
    assert option.duration_months == 12


def test_study_option_parttime() -> None:
    """Test part-time study option."""
    option = ParsedStudyOption(mode=StudyMode.PART_TIME, duration_months=24)
    assert option.mode == StudyMode.PART_TIME
    assert option.duration_months == 24


def test_study_option_hybrid() -> None:
    """Test hybrid study option."""
    option = ParsedStudyOption(mode=StudyMode.HYBRID, duration_months=18)
    assert option.mode == StudyMode.HYBRID
    assert option.duration_months == 18


# ── ParsedProgramData validators ────────────────────────────────────


def test_program_data_defaults() -> None:
    """Test ParsedProgramData with default values."""
    program = ParsedProgramData()
    assert program.faculty is None
    assert program.tuition is None
    assert program.study_options == []
    assert program.deadlines == []


def test_program_data_full() -> None:
    """Test ParsedProgramData with all fields populated."""
    tuition = ParsedTuition(amount="350000", currency="HKD")
    options = [ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12)]
    deadlines = [ParsedDeadline(description="Round 1", cutoff_date=datetime(2026, 12, 31))]
    
    program = ParsedProgramData(
        faculty="Faculty of Engineering",
        tuition=tuition,
        study_options=options,
        deadlines=deadlines,
    )
    
    assert program.faculty == "Faculty of Engineering"
    assert program.tuition == tuition
    assert len(program.study_options) == 1
    assert len(program.deadlines) == 1


def test_program_data_none_to_list_validator() -> None:
    """Test that None is converted to [] for study_options and deadlines."""
    program = ParsedProgramData(
        faculty="Faculty of Science",
        tuition=None,
        study_options=None,  # type: ignore
        deadlines=None,  # type: ignore
    )
    assert program.study_options == []
    assert program.deadlines == []


# ── _merge_parsed_data tests ────────────────────────────────────────


def test_merge_empty_data() -> None:
    """Test merging two empty ParsedProgramData objects."""
    existing = ParsedProgramData()
    new = ParsedProgramData()
    merged = _merge_parsed_data(existing, new)
    
    assert merged.faculty is None
    assert merged.tuition is None
    assert merged.study_options == []
    assert merged.deadlines == []


def test_merge_new_overwrites_faculty() -> None:
    """Test that new faculty overwrites existing."""
    existing = ParsedProgramData(faculty="Faculty of Science")
    new = ParsedProgramData(faculty="Faculty of Engineering")
    merged = _merge_parsed_data(existing, new)
    
    assert merged.faculty == "Faculty of Engineering"


def test_merge_keeps_existing_faculty_if_new_is_none() -> None:
    """Test that existing faculty is kept if new is None."""
    existing = ParsedProgramData(faculty="Faculty of Science")
    new = ParsedProgramData(faculty=None)
    merged = _merge_parsed_data(existing, new)
    
    assert merged.faculty == "Faculty of Science"


def test_merge_tuition() -> None:
    """Test tuition merging logic."""
    tuition1 = ParsedTuition(amount="100000", currency="USD")
    tuition2 = ParsedTuition(amount="200000", currency="HKD")
    
    existing = ParsedProgramData(tuition=tuition1)
    new = ParsedProgramData(tuition=tuition2)
    merged = _merge_parsed_data(existing, new)
    
    # New tuition should overwrite
    assert merged.tuition == tuition2


def test_merge_study_options_dedup() -> None:
    """Test that study options are deduplicated when merging."""
    opt1 = ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12)
    opt2 = ParsedStudyOption(mode=StudyMode.PART_TIME, duration_months=24)
    opt3 = ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12)  # Duplicate
    
    existing = ParsedProgramData(study_options=[opt1, opt2])
    new = ParsedProgramData(study_options=[opt3])  # Same as opt1
    merged = _merge_parsed_data(existing, new)
    
    # Should not duplicate opt1
    assert len(merged.study_options) == 2
    assert opt1 in merged.study_options
    assert opt2 in merged.study_options


def test_merge_study_options_append() -> None:
    """Test that new study options are appended."""
    opt1 = ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12)
    opt2 = ParsedStudyOption(mode=StudyMode.PART_TIME, duration_months=24)
    
    existing = ParsedProgramData(study_options=[opt1])
    new = ParsedProgramData(study_options=[opt2])
    merged = _merge_parsed_data(existing, new)
    
    assert len(merged.study_options) == 2
    assert opt1 in merged.study_options
    assert opt2 in merged.study_options


def test_merge_deadlines_dedup() -> None:
    """Test that deadlines are deduplicated when merging."""
    dl1 = ParsedDeadline(description="Round 1", cutoff_date=datetime(2026, 12, 31))
    dl2 = ParsedDeadline(description="Round 2", cutoff_date=datetime(2027, 2, 28))
    dl3 = ParsedDeadline(description="Round 1", cutoff_date=datetime(2026, 12, 31))  # Duplicate
    
    existing = ParsedProgramData(deadlines=[dl1, dl2])
    new = ParsedProgramData(deadlines=[dl3])  # Same as dl1
    merged = _merge_parsed_data(existing, new)
    
    # Should not duplicate dl1
    assert len(merged.deadlines) == 2


def test_merge_complex_scenario() -> None:
    """Test merging with multiple fields populated."""
    tuition1 = ParsedTuition(amount="100000", currency="USD")
    tuition2 = ParsedTuition(amount="200000", currency="HKD")
    opt1 = ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12)
    opt2 = ParsedStudyOption(mode=StudyMode.PART_TIME, duration_months=24)
    dl1 = ParsedDeadline(description="Round 1", cutoff_date=datetime(2026, 12, 31))
    dl2 = ParsedDeadline(description="Round 2", cutoff_date=datetime(2027, 2, 28))
    
    existing = ParsedProgramData(
        faculty="Faculty of Science",
        tuition=tuition1,
        study_options=[opt1],
        deadlines=[dl1],
    )
    
    new = ParsedProgramData(
        faculty="Faculty of Engineering",
        tuition=tuition2,
        study_options=[opt2],
        deadlines=[dl2],
    )
    
    merged = _merge_parsed_data(existing, new)
    
    assert merged.faculty == "Faculty of Engineering"  # New overwrites
    assert merged.tuition == tuition2  # New overwrites
    assert len(merged.study_options) == 2  # Both accumulated
    assert len(merged.deadlines) == 2  # Both accumulated
