"""Tests for src/agents/cleaner_agent.py – LLMCleanerAgent methods.

All LLM calls are mocked to avoid real API usage.
"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

from src.agents.cleaner_agent import (
    LLMCleanerAgent,
    ParsedProgramData,
    ParsedProgramBatch,
    ParsedTuition,
    ParsedDeadline,
    ParsedStudyOption,
    ChunkParseResult,
    _merge_parsed_data,
    _load_prompt,
    MAX_DETAIL_CHARS,
    CHUNK_OVERLAP_RATIO,
)
from src.agents.providers.base import LLMResponse
from src.models.admission import CurrencyCode, StudyMode


# ── Helpers ──────────────────────────────────────────────────────────


def _mock_router(response_text: str) -> MagicMock:
    """Create a mock RouterAgent returning the given JSON text."""
    router = MagicMock()
    router.generate.return_value = LLMResponse(
        text=response_text,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        model="mock",
    )
    return router


# ── LLMCleanerAgent.__init__ ────────────────────────────────────────


def test_cleaner_agent_accepts_router() -> None:
    router = MagicMock()
    agent = LLMCleanerAgent(router=router)
    assert agent.router is router


def test_cleaner_agent_creates_default_router() -> None:
    """When no router is passed, it creates one via create_router (may fail without env)."""
    with patch("src.agents.cleaner_agent.create_router") as mock_create:
        mock_create.return_value = MagicMock()
        agent = LLMCleanerAgent()
        mock_create.assert_called_once()


# ── clean_row ────────────────────────────────────────────────────────


def test_clean_row_success() -> None:
    parsed_json = json.dumps({
        "faculty": "Faculty of Engineering",
        "tuition": {"amount": "350000", "currency": "HKD"},
        "study_options": [{"mode": "FullTime", "duration_months": 12}],
        "deadlines": [{"description": "Main Round", "cutoff_date": "2025-12-31T00:00:00"}],
    })
    router = _mock_router(parsed_json)
    agent = LLMCleanerAgent(router=router)

    result = agent.clean_row({"raw_content": "some text"})

    assert result is not None
    assert result.faculty == "Faculty of Engineering"
    assert result.tuition is not None
    assert result.tuition.amount == Decimal("350000")


def test_clean_row_empty_response() -> None:
    router = MagicMock()
    router.generate.return_value = LLMResponse(
        text="", prompt_tokens=0, completion_tokens=0, total_tokens=0, model="m",
    )
    agent = LLMCleanerAgent(router=router)

    result = agent.clean_row({"raw_content": "data"})
    assert result is None


def test_clean_row_llm_exception() -> None:
    router = MagicMock()
    router.generate.side_effect = RuntimeError("LLM crashed")
    agent = LLMCleanerAgent(router=router)

    with pytest.raises(RuntimeError, match="LLM crashed"):
        agent.clean_row({"raw_content": "data"})


# ── clean_markdown – single pass ────────────────────────────────────


def test_clean_markdown_single_pass() -> None:
    """Small markdown (<= MAX_DETAIL_CHARS) uses single-pass path."""
    parsed_json = json.dumps({
        "faculty": "School of Business",
        "tuition": {"amount": "100000", "currency": "USD"},
        "study_options": [],
        "deadlines": [],
    })
    router = _mock_router(parsed_json)
    agent = LLMCleanerAgent(router=router)

    short_md = "# MBA Program\n\nTuition: $100,000"
    result = agent.clean_markdown(short_md, source_url="https://example.com")

    assert result is not None
    assert result.faculty == "School of Business"
    router.generate.assert_called_once()


def test_clean_markdown_returns_empty_parsed_data_on_no_data() -> None:
    parsed_json = json.dumps({
        "faculty": None,
        "tuition": None,
        "study_options": [],
        "deadlines": [],
    })
    router = _mock_router(parsed_json)
    agent = LLMCleanerAgent(router=router)

    result = agent.clean_markdown("# Generic page\nNo admission info", source_url="")
    # Single-pass returns ParsedProgramData (empty) from clean_row, not None
    assert result is not None
    assert result.faculty is None
    assert result.study_options == []


# ── clean_markdown – rolling chunks ─────────────────────────────────


def test_clean_markdown_rolling_chunks() -> None:
    """Large markdown triggers multi-chunk parsing."""
    # Create markdown larger than MAX_DETAIL_CHARS
    large_md = "# Big Program\n\n" + ("Lorem ipsum admission data. " * 2000)
    assert len(large_md) > MAX_DETAIL_CHARS

    # Mock chunk prompt loading
    chunk_prompt = (
        "Context: {context_summary}\n"
        "Chunk {chunk_number}/{total_chunks}:\n"
        "{chunk_content}"
    )

    chunk_result = json.dumps({
        "data": {
            "faculty": "Faculty of Science",
            "tuition": {"amount": "200000", "currency": "HKD"},
            "study_options": [{"mode": "FullTime", "duration_months": 24}],
            "deadlines": [],
        },
        "context_summary": "Program in Faculty of Science, tuition 200k HKD.",
    })

    router = _mock_router(chunk_result)
    agent = LLMCleanerAgent(router=router)

    with patch("src.agents.cleaner_agent._load_prompt", return_value=chunk_prompt):
        result = agent.clean_markdown(large_md, source_url="https://example.com/big")

    assert result is not None
    assert result.faculty == "Faculty of Science"
    assert result.tuition is not None
    # Should have been called multiple times (once per chunk)
    assert router.generate.call_count >= 2


def test_clean_markdown_rolling_merge_across_chunks() -> None:
    """Multi-chunk parsing should merge chunk results (faculty from chunk1, tuition from chunk2)."""
    large_md = "X" * (MAX_DETAIL_CHARS + 5000)

    chunk_prompt = "{context_summary}\n{chunk_number}/{total_chunks}\n{chunk_content}"

    call_count = 0

    def side_effect(prompt, schema):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            text = json.dumps({
                "data": {
                    "faculty": "Faculty of Arts",
                    "tuition": None,
                    "study_options": [],
                    "deadlines": [{"description": "Round 1", "cutoff_date": "2025-06-01T00:00:00"}],
                },
                "context_summary": "Arts faculty, deadline June 2025.",
            })
        else:
            text = json.dumps({
                "data": {
                    "faculty": None,
                    "tuition": {"amount": "150000", "currency": "HKD"},
                    "study_options": [{"mode": "PartTime", "duration_months": 24}],
                    "deadlines": [],
                },
                "context_summary": "Tuition 150k HKD, part-time 2 years.",
            })
        return LLMResponse(
            text=text, prompt_tokens=10, completion_tokens=5,
            total_tokens=15, model="mock",
        )

    router = MagicMock()
    router.generate.side_effect = side_effect
    agent = LLMCleanerAgent(router=router)

    with patch("src.agents.cleaner_agent._load_prompt", return_value=chunk_prompt):
        result = agent.clean_markdown(large_md, source_url="https://example.com")

    assert result is not None
    # Merged: faculty from chunk 1, tuition from chunk 2
    assert result.faculty == "Faculty of Arts"
    assert result.tuition is not None
    assert result.tuition.amount == Decimal("150000")
    assert len(result.study_options) == 1
    assert len(result.deadlines) == 1


def test_clean_markdown_rolling_all_chunks_fail() -> None:
    """If all chunks fail, clean_markdown should return None."""
    large_md = "Y" * (MAX_DETAIL_CHARS + 5000)
    chunk_prompt = "{context_summary}\n{chunk_number}/{total_chunks}\n{chunk_content}"

    router = MagicMock()
    router.generate.side_effect = RuntimeError("LLM unavailable")
    agent = LLMCleanerAgent(router=router)

    with patch("src.agents.cleaner_agent._load_prompt", return_value=chunk_prompt):
        result = agent.clean_markdown(large_md, source_url="")

    assert result is None


# ── clean_batch ──────────────────────────────────────────────────────


def test_clean_batch_success() -> None:
    batch_json = json.dumps({
        "programs": [
            {"faculty": "Engineering", "tuition": {"amount": "100000", "currency": "HKD"}, "study_options": [], "deadlines": []},
            {"faculty": "Science", "tuition": None, "study_options": [], "deadlines": []},
        ]
    })
    router = _mock_router(batch_json)
    agent = LLMCleanerAgent(router=router)

    results = agent.clean_batch([{"row": "1"}, {"row": "2"}])
    assert len(results) == 2
    assert results[0].faculty == "Engineering"
    assert results[1].faculty == "Science"


def test_clean_batch_empty_input() -> None:
    router = MagicMock()
    agent = LLMCleanerAgent(router=router)

    results = agent.clean_batch([])
    assert results == []
    router.generate.assert_not_called()


def test_clean_batch_empty_response() -> None:
    router = MagicMock()
    router.generate.return_value = LLMResponse(
        text="", prompt_tokens=0, completion_tokens=0, total_tokens=0, model="m",
    )
    agent = LLMCleanerAgent(router=router)

    results = agent.clean_batch([{"row": "1"}, {"row": "2"}])
    assert len(results) == 2
    # Defaults returned for empty response
    assert all(r.faculty is None for r in results)


def test_clean_batch_llm_exception() -> None:
    router = MagicMock()
    router.generate.side_effect = RuntimeError("batch failed")
    agent = LLMCleanerAgent(router=router)

    with pytest.raises(RuntimeError, match="batch failed"):
        agent.clean_batch([{"row": "1"}])


# ── _split_chunks ────────────────────────────────────────────────────


def test_split_chunks_small_text() -> None:
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    chunks = agent._split_chunks("short text", max_chars=100)
    assert chunks == ["short text"]


def test_split_chunks_overlap_ratio() -> None:
    text = "A" * 300
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    chunks = agent._split_chunks(text, max_chars=100, overlap_ratio=0.2)

    # Each chunk <= 100 chars
    assert all(len(c) <= 100 for c in chunks)
    # Multiple chunks created
    assert len(chunks) >= 3


def test_split_chunks_overlap_ratio_zero() -> None:
    text = "B" * 250
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    chunks = agent._split_chunks(text, max_chars=100, overlap_ratio=0.0)
    # With 0 overlap, step == max_chars
    assert len(chunks) >= 3


def test_split_chunks_clamps_overlap() -> None:
    """Overlap ratio > 0.5 should be clamped to 0.5."""
    text = "C" * 300
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    # Should not crash even with out-of-range overlap
    chunks = agent._split_chunks(text, max_chars=100, overlap_ratio=0.9)
    assert len(chunks) >= 2


# ── _merge_parsed_data ──────────────────────────────────────────────


def test_merge_prefers_new_faculty() -> None:
    old = ParsedProgramData(faculty="Old Faculty")
    new = ParsedProgramData(faculty="New Faculty")
    merged = _merge_parsed_data(old, new)
    assert merged.faculty == "New Faculty"


def test_merge_keeps_existing_faculty_if_new_is_none() -> None:
    old = ParsedProgramData(faculty="Keep This")
    new = ParsedProgramData(faculty=None)
    merged = _merge_parsed_data(old, new)
    assert merged.faculty == "Keep This"


def test_merge_accumulates_deadlines() -> None:
    old = ParsedProgramData(deadlines=[
        ParsedDeadline(description="Round 1", cutoff_date=datetime(2025, 6, 1)),
    ])
    new = ParsedProgramData(deadlines=[
        ParsedDeadline(description="Round 2", cutoff_date=datetime(2025, 9, 1)),
    ])
    merged = _merge_parsed_data(old, new)
    assert len(merged.deadlines) == 2


def test_merge_deduplicates_deadlines() -> None:
    dl = ParsedDeadline(description="Same", cutoff_date=datetime(2025, 6, 1))
    old = ParsedProgramData(deadlines=[dl])
    new = ParsedProgramData(deadlines=[dl])
    merged = _merge_parsed_data(old, new)
    assert len(merged.deadlines) == 1


def test_merge_accumulates_study_options() -> None:
    old = ParsedProgramData(study_options=[
        ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12),
    ])
    new = ParsedProgramData(study_options=[
        ParsedStudyOption(mode=StudyMode.PART_TIME, duration_months=24),
    ])
    merged = _merge_parsed_data(old, new)
    assert len(merged.study_options) == 2


def test_merge_prefers_new_tuition() -> None:
    old = ParsedProgramData(
        tuition=ParsedTuition(amount=Decimal("100000"), currency=CurrencyCode.HKD)
    )
    new = ParsedProgramData(
        tuition=ParsedTuition(amount=Decimal("200000"), currency=CurrencyCode.USD)
    )
    merged = _merge_parsed_data(old, new)
    assert merged.tuition is not None
    assert merged.tuition.amount == Decimal("200000")
    assert merged.tuition.currency == CurrencyCode.USD
