"""The cleaner copies every fee the page states and derives the headline in code."""

from decimal import Decimal
from unittest.mock import patch

from src.agents.cleaner_agent import (
    LLMCleanerAgent, ParsedProgramData, ParsedStudyOption, ParsedTuitionFee, _normalize_parsed_data,
)
from src.models.admission import CurrencyCode, StudyMode, TuitionBasis, TuitionScope


def _fee(amount, basis, mode=StudyMode.UNKNOWN, label=None, credits=None, text=None):
    return ParsedTuitionFee(amount=Decimal(amount), currency=CurrencyCode.HKD, basis=basis,
                            study_mode=mode, scope_label=label, credits=credits, source_text=text)


def test_scope_is_computed_from_the_label() -> None:
    assert _fee(1, TuitionBasis.PER_ANNUM, label="Non-local Students").applicant_scope is TuitionScope.NON_LOCAL
    assert _fee(1, TuitionBasis.PER_ANNUM, label="Local").applicant_scope is TuitionScope.LOCAL
    assert _fee(1, TuitionBasis.PER_ANNUM).applicant_scope is TuitionScope.ALL


def test_amount_accepts_the_same_shorthand_as_the_old_field() -> None:
    assert ParsedTuitionFee(amount="198k", currency="HKD", basis="per_annum").amount == Decimal(198000)


def test_llm_output_without_tuition_fees_parses_to_an_empty_list() -> None:
    assert ParsedProgramData.model_validate({"faculty": "Arts", "tuition_fees": None}).tuition_fees == []


def test_dedup_keeps_the_row_with_the_longer_source_text() -> None:
    short = _fee(198000, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME, text="HK$198,000")
    long_ = _fee(198000, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME, text="Tuition Fee HK$198,000 per annum (full-time)")
    out = _normalize_parsed_data(ParsedProgramData(tuition_fees=[short, long_]))
    assert len(out.tuition_fees) == 1 and out.tuition_fees[0].source_text == long_.source_text


def test_dedup_keeps_rows_that_differ_in_any_key_dimension() -> None:
    rows = [_fee(1, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME), _fee(2, TuitionBasis.PER_ANNUM, StudyMode.PART_TIME),
            _fee(3, TuitionBasis.PER_PROGRAMME, StudyMode.FULL_TIME), _fee(4, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME, label="Local")]
    assert len(_normalize_parsed_data(ParsedProgramData(tuition_fees=rows)).tuition_fees) == 4


def test_clean_markdown_sets_the_headline_from_the_rows_and_ignores_what_the_llm_put_in_tuition() -> None:
    llm = ParsedProgramData(
        tuition={"amount": 1, "currency": "HKD"},                 # stale guess from the model
        tuition_fees=[_fee(198000, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME),
                      _fee(99000, TuitionBasis.PER_ANNUM, StudyMode.PART_TIME)],
        study_options=[ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12),
                       ParsedStudyOption(mode=StudyMode.PART_TIME, duration_months=24)],
    )
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    with patch.object(LLMCleanerAgent, "_parse_single_pass", return_value=llm):
        out = agent.clean_markdown("short page", "https://x")
    assert out.tuition.amount == Decimal(198000)
    assert out.tuition.currency is CurrencyCode.HKD


def test_clean_markdown_appends_derived_rows_to_the_detail() -> None:
    llm = ParsedProgramData(
        tuition_fees=[_fee(9500, TuitionBasis.PER_CREDIT, credits=30, text="HK$9,500 per credit")],
    )
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    with patch.object(LLMCleanerAgent, "_parse_single_pass", return_value=llm):
        out = agent.clean_markdown("short page", "https://x")
    assert out.tuition.amount == Decimal(285000)
    derived = [f for f in out.tuition_fees if f.is_derived]
    assert len(derived) == 1 and derived[0].basis is TuitionBasis.PER_PROGRAMME
    assert derived[0].amount == Decimal(285000)


def test_clean_markdown_with_no_fee_rows_leaves_tuition_none() -> None:
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    with patch.object(LLMCleanerAgent, "_parse_single_pass", return_value=ParsedProgramData(faculty="Arts")):
        out = agent.clean_markdown("short page", "https://x")
    assert out.tuition is None and out.tuition_fees == []


def test_the_prompt_asks_for_every_fee_not_a_choice() -> None:
    from src.agents.cleaner_agent import _load_prompt
    text = _load_prompt("clean_chunk.txt")
    assert "one row per" in text.lower() or "one entry per" in text.lower()
    assert "extract the TOTAL" not in text
    assert "living" in text.lower() and "deposit" in text.lower()
