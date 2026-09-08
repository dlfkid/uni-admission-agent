"""The cleaner copies every fee the page states and derives the headline in code."""

import json
from decimal import Decimal
from unittest.mock import patch

from src.agents.cleaner_agent import (
    ChunkParseResult, LLMCleanerAgent, ParsedProgramBatch, ParsedProgramData, ParsedStudyOption,
    ParsedTuitionFee, _normalize_parsed_data,
)
from src.agents.providers.base import LLMResponse
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


def test_default_schema_hides_computed_fee_fields() -> None:
    """Gemini (and anything else handed the raw class) sees model_json_schema()'s
    default mode, so the computed fields must be absent there too, not just under
    mode="serialization". Scoped to ParsedTuitionFee's own $defs entry — some other
    model (ParsedRequirement) legitimately has an unrelated field also named
    applicant_scope, so a whole-schema substring check would false-positive."""
    for schema_cls in (ParsedProgramData, ChunkParseResult, ParsedProgramBatch):
        schema = schema_cls.model_json_schema()
        fee_def = schema["$defs"]["ParsedTuitionFee"]
        dumped = json.dumps(fee_def)
        assert "applicant_scope" not in dumped, schema_cls.__name__
        assert "is_derived" not in dumped, schema_cls.__name__


def test_llm_supplied_computed_fields_are_ignored() -> None:
    """An LLM must never be able to set applicant_scope/is_derived directly —
    applicant_scope is always recomputed from scope_label, and is_derived always
    defaults to False for anything the constructor receives as a dict."""
    fee = ParsedTuitionFee.model_validate({
        "amount": 1, "currency": "HKD", "basis": "per_annum",
        "scope_label": "Non-local", "is_derived": True, "applicant_scope": "local",
    })
    assert fee.is_derived is False
    assert fee.applicant_scope is TuitionScope.NON_LOCAL


def test_amount_schema_still_advertises_a_number() -> None:
    """Pins that ParsedTuition's schema is the pre-86a9b2a validation-mode shape
    (anyOf number/decimal-string), not the stricter serialization-mode string
    the reverted llm_json_schema() helper produced."""
    from src.agents.cleaner_agent import ParsedTuition
    amount_schema = ParsedTuition.model_json_schema()["properties"]["amount"]
    assert "number" in json.dumps(amount_schema)


def test_clean_row_prompt_asks_for_every_fee_not_a_choice() -> None:
    """clean_row builds its own inline prompt (not clean_chunk.txt); make sure the
    controller-mandated rewrite of that prompt is actually in place and stays."""
    captured = {}

    class _FakeRouter:
        def generate(self, prompt, schema):
            captured["prompt"] = prompt
            return LLMResponse(text=schema().model_dump_json(), model="fake")

    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    agent.router = _FakeRouter()

    agent.clean_row({"Tuition Fee": "HK$ 350,000"})

    prompt = captured["prompt"].lower()
    assert "one entry per" in prompt
    assert "living" in prompt
    assert "deposit" in prompt
    assert "extract the total" not in prompt
