"""Tests for page_processor sorting and data handling."""

from datetime import datetime, timezone

from src.agents.cleaner_agent import ParsedDeadline


def test_deadline_sorting_mixed_timezones() -> None:
    """Test sorting deadlines with mixed offset-aware and offset-naive datetimes."""
    # Create test deadlines with mixed timezone awareness
    deadlines = [
        ParsedDeadline(description="Round 3", cutoff_date=datetime(2027, 3, 1)),  # naive
        ParsedDeadline(description="Round 1", cutoff_date=datetime(2027, 1, 15, tzinfo=timezone.utc)),  # aware
        ParsedDeadline(description="Rolling", cutoff_date=None),  # None
        ParsedDeadline(description="Round 2", cutoff_date=datetime(2027, 2, 1)),  # naive
    ]
    
    # Use the same sorting logic as in page_processor.py
    def sort_key(deadline):
        if deadline.cutoff_date is None:
            return datetime.max
        dt = deadline.cutoff_date
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    
    sorted_deadlines = sorted(deadlines, key=sort_key)
    
    # Verify order: Round 1 (Jan 15), Round 2 (Feb 1), Round 3 (Mar 1), Rolling (None/max)
    assert sorted_deadlines[0].description == "Round 1"
    assert sorted_deadlines[1].description == "Round 2"
    assert sorted_deadlines[2].description == "Round 3"
    assert sorted_deadlines[3].description == "Rolling"


def test_deadline_sorting_all_none() -> None:
    """Test sorting when all deadlines have None cutoff_date."""
    deadlines = [
        ParsedDeadline(description="Rolling A", cutoff_date=None),
        ParsedDeadline(description="Rolling B", cutoff_date=None),
    ]
    
    def sort_key(deadline):
        if deadline.cutoff_date is None:
            return datetime.max
        dt = deadline.cutoff_date
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    
    # Should not raise TypeError
    sorted_deadlines = sorted(deadlines, key=sort_key)
    assert len(sorted_deadlines) == 2
