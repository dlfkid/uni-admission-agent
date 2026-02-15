import pytest
from datetime import datetime
from src.core.parser import DataCleaner
from src.agents.cleaner_agent import ParsedDeadline

def test_parse_deadlines_regex_sorting():
    # Test DataCleaner.parse_deadlines (regex based)
    raw_text = "May 31, 2026; March 31, 2026"
    results = DataCleaner.parse_deadlines(raw_text)
    
    assert len(results) == 2
    # Verify sorting: March comes before May
    assert results[0]["round"] == 1
    assert "2026-03-31" in results[0]["cutoff_date"]
    
    assert results[1]["round"] == 2
    assert "2026-05-31" in results[1]["cutoff_date"]

def test_parsed_deadline_model_sorting_logic():
    # Simulate logic in importer/engine
    # Create unordered deadlines
    d1 = ParsedDeadline(cutoff_date=datetime(2026, 5, 31), description="Round 2")
    d2 = ParsedDeadline(cutoff_date=datetime(2026, 3, 31), description="Round 1")
    
    deadlines = [d1, d2]
    
    # Sort
    sorted_deadlines = sorted(deadlines, key=lambda x: x.cutoff_date)
    
    # Assign rounds
    final_data = []
    for i, d in enumerate(sorted_deadlines, 1):
        d_dict = d.model_dump(mode="json")
        d_dict["round"] = i
        final_data.append(d_dict)
        
    assert final_data[0]["round"] == 1
    assert final_data[0]["cutoff_date"].startswith("2026-03-31")
    assert final_data[0]["description"] == "Round 1"
    
    assert final_data[1]["round"] == 2
    assert final_data[1]["cutoff_date"].startswith("2026-05-31")
    assert final_data[1]["description"] == "Round 2"
