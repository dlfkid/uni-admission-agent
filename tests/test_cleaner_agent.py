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
    ParsedRequirement,
    ChunkParseResult,
    _merge_parsed_data,
    _reconcile_per_credit_tuition,
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


def test_split_chunks_no_content_dropped_at_paragraph_break() -> None:
    """A value just past an early paragraph break must survive chunking.

    Regression: advancing the next chunk by a fixed step from the original start
    (instead of from where the chunk actually ended at a paragraph break) left a
    gap between chunk_end and the next start, silently dropping any text in it —
    e.g. a tuition figure sitting just after the break. See _split_chunks.
    """
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    # Unique numbered lines so positions are unambiguous; an early "\n\n" break
    # forces chunk 0 to end before max_chars, putting the sentinel in what used to
    # be the dropped gap between chunk_end and the next fixed-step start.
    lines = [f"line{i:04d}" for i in range(200)]
    sentinel = "TUITION_SENTINEL_424800"
    lines.insert(60, sentinel)
    text = "\n\n".join(lines)
    chunks = agent._split_chunks(text, max_chars=400, overlap_ratio=0.2)

    assert any(sentinel in c for c in chunks), "value in the gap was dropped"
    # No coverage gap: each chunk must start at/before where the previous ended.
    end_so_far = 0
    for c in chunks:
        idx = text.index(c)
        assert idx <= end_so_far, f"gap before chunk at {idx} (covered to {end_so_far})"
        end_so_far = max(end_so_far, idx + len(c))
    assert end_so_far == len(text), "tail of text not covered by any chunk"


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


# ── _merge_parsed_data semantic dedup ───────────────────────────────


def test_merge_dedups_deadlines_by_date_and_drops_null() -> None:
    """Same cutoff date with different labels collapses; null-date entries drop.

    Overlapping chunks re-emit deadlines with slightly different descriptions
    ("Early Round" vs "Early"); exact-object dedup let them both through.
    """
    a = ParsedProgramData(
        deadlines=[ParsedDeadline(description="Early Round", cutoff_date=datetime(2026, 10, 20))]
    )
    b = ParsedProgramData(
        deadlines=[
            ParsedDeadline(description="Early", cutoff_date=datetime(2026, 10, 20)),
            ParsedDeadline(description="Main Round", cutoff_date=None),
        ]
    )
    merged = _merge_parsed_data(a, b)
    assert len(merged.deadlines) == 1
    assert merged.deadlines[0].cutoff_date == datetime(2026, 10, 20)


def test_merge_dedups_requirements_by_normalized_text() -> None:
    """Identical requirement text differing only in other fields collapses to one."""
    a = ParsedProgramData(
        requirements=[ParsedRequirement(
            category="academic_subject",
            requirement_text="A Bachelor's degree or equivalent in any discipline.")]
    )
    b = ParsedProgramData(
        requirements=[ParsedRequirement(
            category="academic_subject",
            subject_name="Bachelor's degree",
            requirement_text="A Bachelor's degree or equivalent in any discipline.")]
    )
    merged = _merge_parsed_data(a, b)
    assert len(merged.requirements) == 1


def test_merge_keeps_distinct_deadlines_and_requirements() -> None:
    """Genuinely different dates / texts are preserved (no over-dedup)."""
    a = ParsedProgramData(
        deadlines=[ParsedDeadline(description="Early", cutoff_date=datetime(2026, 10, 20))],
        requirements=[ParsedRequirement(category="academic_subject", requirement_text="Bachelor degree.")],
    )
    b = ParsedProgramData(
        deadlines=[ParsedDeadline(description="Main", cutoff_date=datetime(2027, 2, 25))],
        requirements=[ParsedRequirement(category="language", requirement_text="IELTS 6.5 overall.")],
    )
    merged = _merge_parsed_data(a, b)
    assert len(merged.deadlines) == 2
    assert len(merged.requirements) == 2


def test_merge_drops_requirement_contained_in_longer_one() -> None:
    """A requirement whose text is fully contained in a longer one is dropped.

    Overlapping chunks capture a subset of a rule in one chunk and the full
    statement in another; the subset is redundant (option-A substring dedup).
    Cross-category containment is intentional — chunk paraphrases split one rule
    across academic/experience labels.
    """
    short_academic = ParsedRequirement(
        category="academic_subject",
        requirement_text="A Bachelor's degree or an equivalent professional qualification.")
    experience = ParsedRequirement(
        category="experience",
        requirement_text="employed for no less than 6 years in a managerial capacity.")
    full = ParsedRequirement(
        category="academic_subject",
        requirement_text=("A Bachelor's degree or an equivalent professional qualification. "
                          "employed for no less than 6 years in a managerial capacity."))
    merged = _merge_parsed_data(
        ParsedProgramData(requirements=[short_academic, experience, full]),
        ParsedProgramData(),
    )
    texts = [r.requirement_text for r in merged.requirements]
    assert len(merged.requirements) == 1
    assert texts[0] == full.requirement_text


def test_merge_keeps_paraphrased_requirements() -> None:
    """Different wording of the same rule (neither contains the other) is kept.

    Substring dedup deliberately does NOT collapse paraphrases — that would need
    semantic dedup (option B), which we did not adopt.
    """
    a = ParsedRequirement(category="language",
                          requirement_text="Non-native English speakers must meet the English requirement.")
    b = ParsedRequirement(category="language",
                          requirement_text="If you are not a native speaker of English you must fulfil the language requirement.")
    merged = _merge_parsed_data(ParsedProgramData(requirements=[a, b]), ParsedProgramData())
    assert len(merged.requirements) == 2


def test_merge_collapses_same_test_threshold_across_wildly_different_lengths() -> None:
    """Regression for Lingnan battle-test round: a thin/hub-page supplement
    merges several pages that each restate the same admission threshold at
    a very different length — a terse table cell next to a full sentence.
    Neither substring containment (not contiguous) nor the 0.82
    SequenceMatcher ratio (length mismatch alone tanks it) catches this;
    the structured (test name, numeric threshold) key does.
    """
    terse = ParsedRequirement(
        category="standardized_test", subject_name="TOEFL",
        minimum_value="79", requirement_text="TOEFL 79")
    verbose = ParsedRequirement(
        category="language", subject_name="TOEFL iBT",
        minimum_value="79", unit="score",
        requirement_text=(
            "Minimum score of 79 in TOEFL Internet-Based Test "
            "(single test sitting, within two-year validity period)."
        ))
    merged = _merge_parsed_data(
        ParsedProgramData(requirements=[terse, verbose]), ParsedProgramData()
    )
    assert len(merged.requirements) == 1
    assert merged.requirements[0].requirement_text == verbose.requirement_text


def test_merge_keeps_different_test_thresholds_distinct() -> None:
    """Same test family, different threshold — must NOT collapse."""
    ielts_65 = ParsedRequirement(
        category="language", subject_name="IELTS",
        minimum_value="6.5", requirement_text="IELTS 6.5")
    ielts_60 = ParsedRequirement(
        category="language", subject_name="IELTS",
        minimum_value="6.0", requirement_text="IELTS 6.0")
    merged = _merge_parsed_data(
        ParsedProgramData(requirements=[ielts_65, ielts_60]), ParsedProgramData()
    )
    assert len(merged.requirements) == 2


def test_merge_does_not_collapse_degree_statements_without_threshold() -> None:
    """A requirement with no minimum_value (e.g. a bare "Bachelor's degree"
    statement) must fall through to the existing text-based rules rather
    than being swept into the structured-threshold dedup — different
    degree-requirement wordings can carry genuinely different content
    (e.g. a Mainland-China-specific certificate clause), so collapsing them
    on no signal at all would risk silently deleting real content."""
    a = ParsedRequirement(
        category="academic_subject",
        requirement_text="A recognized bachelor's degree is required.")
    b = ParsedRequirement(
        category="academic_subject",
        requirement_text=(
            "Bachelor's degree or equivalent; applicants from Mainland "
            "China must provide degree certificate, graduation certificate."
        ))
    merged = _merge_parsed_data(ParsedProgramData(requirements=[a, b]), ParsedProgramData())
    assert len(merged.requirements) == 2


def test_merge_collapses_near_verbatim_paraphrase() -> None:
    """A near-verbatim restatement (single word inserted/dropped) collapses to
    one, keeping the more complete phrasing.

    Regression test for CUHK MPhil-in-History extraction: the same clause was
    extracted twice as "...crucial to research" and "...crucial to their
    research" — differing by one word, which defeats exact-substring
    containment. This is narrower than semantic dedup (option B, rejected in
    test_merge_keeps_paraphrased_requirements above): it only fires on
    near-identical sentences (>=0.82 similarity), not differently-worded
    restatements of the same idea.
    """
    a = ParsedRequirement(
        category="language",
        requirement_text="Demonstrate proficient command of language(s) crucial to research.")
    b = ParsedRequirement(
        category="language",
        requirement_text=(
            "demonstrate proficient command of language(s) which is/are "
            "crucial to their research"
        ))
    merged = _merge_parsed_data(ParsedProgramData(requirements=[a, b]), ParsedProgramData())
    assert len(merged.requirements) == 1
    assert merged.requirements[0].requirement_text == b.requirement_text


# ── _reconcile_per_credit_tuition ───────────────────────────────────


def _mk_tuition(amount) -> ParsedProgramData:
    return ParsedProgramData(tuition=ParsedTuition(amount=Decimal(str(amount)), currency=CurrencyCode.HKD))


def test_reconcile_per_credit_multiplies_by_credits() -> None:
    """Per-credit-only page: amount is the per-credit rate -> compute total."""
    p = _mk_tuition(8200)
    md = "STUDY MODE Full-time CREDIT REQUIRED 30 Tuition Fee HK$8,200 per credit for local students"
    _reconcile_per_credit_tuition(p, md, "u")
    assert p.tuition.amount == Decimal("246000")


def test_reconcile_leaves_per_programme_total_untouched() -> None:
    """When a per-programme total is present, the extracted amount is trusted."""
    p = _mk_tuition(424800)
    md = "Tuition Fee HK$424,800 per programme (HK$11,800 per credit for 36 credits) CREDIT REQUIRED 43"
    _reconcile_per_credit_tuition(p, md, "u")
    assert p.tuition.amount == Decimal("424800")


def test_reconcile_no_credit_count_leaves_amount() -> None:
    """Per-credit rate but no credit count on page -> cannot compute, leave as-is."""
    p = _mk_tuition(9500)
    _reconcile_per_credit_tuition(p, "Tuition Fee HK$9,500 per credit for local students", "u")
    assert p.tuition.amount == Decimal("9500")


def test_reconcile_ignores_non_matching_amount() -> None:
    """A normal total that doesn't equal any per-credit rate is never rewritten."""
    p = _mk_tuition(300000)
    _reconcile_per_credit_tuition(p, "HK$9,500 per credit CREDIT REQUIRED 30", "u")
    assert p.tuition.amount == Decimal("300000")


def test_single_pass_path_dedups_and_drops_null_deadline() -> None:
    """Review fix ①: dedup/null-drop must apply on the SINGLE-PASS path too.

    Previously the dedup lived only in _merge_parsed_data (rolling-chunks path), so a
    small (single-pass) page kept duplicate/null-date items a large page would drop —
    inconsistent by page size. clean_markdown now normalizes both paths uniformly.
    """
    parsed_json = json.dumps({
        "faculty": "Faculty of Business",
        "tuition": {"amount": "300000", "currency": "HKD"},
        "study_options": [
            {"mode": "FullTime", "duration_months": 12},
            {"mode": "FullTime", "duration_months": 12},  # exact dup
        ],
        "deadlines": [
            {"description": "Early Round", "cutoff_date": "2026-10-20T00:00:00"},
            {"description": "Early", "cutoff_date": "2026-10-20T00:00:00"},  # same date dup
            {"description": "Rolling", "cutoff_date": None},                 # null date -> dropped
        ],
        "requirements": [
            {"category": "academic_subject", "requirement_text": "A Bachelor's degree."},
            {"category": "academic_subject", "requirement_text": "A Bachelor's degree."},  # dup
        ],
    })
    agent = LLMCleanerAgent(router=_mock_router(parsed_json))
    result = agent.clean_markdown("# Small page\n\nTuition HK$300,000", source_url="https://x/y")

    assert result is not None
    assert len(result.study_options) == 1
    assert len(result.deadlines) == 1          # one date kept, dup collapsed, null dropped
    assert result.deadlines[0].cutoff_date is not None
    assert len(result.requirements) == 1


def test_normalize_parsed_data_is_idempotent() -> None:
    """Normalizing already-normalized data changes nothing (safe to run on both paths)."""
    from src.agents.cleaner_agent import _normalize_parsed_data
    p = ParsedProgramData(
        study_options=[ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12)],
        deadlines=[ParsedDeadline(description="Early", cutoff_date=datetime(2026, 10, 20))],
        requirements=[ParsedRequirement(category="language", requirement_text="IELTS 6.5.")],
    )
    once = _normalize_parsed_data(p)
    twice = _normalize_parsed_data(once)
    assert len(twice.study_options) == 1 and len(twice.deadlines) == 1 and len(twice.requirements) == 1
