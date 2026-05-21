"""Tests for src/services/quality_gate.py.

The quality gate is the last line of defense between extraction and
persistence: it refuses to commit program records that are obviously
broken — empty shells, noise names, missing identifying fields.
"""
from __future__ import annotations

from src.services.quality_gate import (
    QuarantineReason,
    QualityVerdict,
    evaluate_extraction,
)


def _good_program() -> dict:
    """A program that should pass the gate cleanly."""
    return {
        "academic_year": 2026,
        "name_en": "MSc Finance",
        "name_zh": "金融学硕士",
        "faculty": "Faculty of Business",
        "tuition_amount": 350000,
        "currency": "HKD",
        "deadlines": [
            {"round": 1, "description": "Main round", "cutoff_date": "2026-01-15"}
        ],
        "requirements": [{"subject": "GPA", "min_value": "3.0"}],
    }


class TestEvaluateExtraction:
    def test_good_program_passes(self) -> None:
        verdict = evaluate_extraction(_good_program())
        assert verdict.passed is True
        assert verdict.reason is None

    def test_signals_always_present(self) -> None:
        """Signals carry diagnostic data regardless of pass/fail."""
        verdict = evaluate_extraction(_good_program())
        assert "deadline_count" in verdict.signals
        assert verdict.signals["deadline_count"] == 1
        assert verdict.signals["has_tuition"] is True
        assert verdict.signals["requirement_count"] == 1
        assert verdict.signals["name_length"] == len("MSc Finance")

    def test_empty_name_fails(self) -> None:
        prog = _good_program()
        prog["name_en"] = ""
        verdict = evaluate_extraction(prog)
        assert verdict.passed is False
        assert verdict.reason == QuarantineReason.EMPTY_NAME

    def test_missing_name_field_fails(self) -> None:
        prog = _good_program()
        del prog["name_en"]
        verdict = evaluate_extraction(prog)
        assert verdict.passed is False
        assert verdict.reason == QuarantineReason.EMPTY_NAME

    def test_whitespace_only_name_fails(self) -> None:
        prog = _good_program()
        prog["name_en"] = "   "
        verdict = evaluate_extraction(prog)
        assert verdict.passed is False
        assert verdict.reason == QuarantineReason.EMPTY_NAME

    def test_name_too_short_fails(self) -> None:
        prog = _good_program()
        prog["name_en"] = "AB"
        verdict = evaluate_extraction(prog)
        assert verdict.passed is False
        assert verdict.reason == QuarantineReason.NAME_TOO_SHORT

    def test_noise_name_fails(self) -> None:
        """Names matching the project's existing noise filter are rejected."""
        prog = _good_program()
        # Phrases from _NOISE_PROGRAM_NAME_RE in src/scrapers/helpers.py:
        for noise in [
            "Course Search",
            "Home",
            "Postgraduate Programmes",
            "A to Z of Programmes",
            "Browse by Faculty",
        ]:
            prog["name_en"] = noise
            verdict = evaluate_extraction(prog)
            assert verdict.passed is False, f"expected {noise!r} to be rejected"
            assert verdict.reason == QuarantineReason.NOISE_NAME

    def test_empty_shell_fails(self) -> None:
        """A program with only a name and nothing else is rejected."""
        prog = _good_program()
        prog["tuition_amount"] = None
        prog["currency"] = None
        prog["deadlines"] = []
        prog["requirements"] = []
        prog["study_options"] = []
        verdict = evaluate_extraction(prog)
        assert verdict.passed is False
        assert verdict.reason == QuarantineReason.EMPTY_SHELL

    def test_name_plus_only_tuition_passes(self) -> None:
        """Tuition alone is enough to escape the empty-shell trap."""
        prog = _good_program()
        prog["deadlines"] = []
        prog["requirements"] = []
        verdict = evaluate_extraction(prog)
        assert verdict.passed is True

    def test_name_plus_only_deadline_passes(self) -> None:
        prog = _good_program()
        prog["tuition_amount"] = None
        prog["currency"] = None
        prog["requirements"] = []
        verdict = evaluate_extraction(prog)
        assert verdict.passed is True

    def test_name_plus_only_requirement_passes(self) -> None:
        prog = _good_program()
        prog["tuition_amount"] = None
        prog["currency"] = None
        prog["deadlines"] = []
        verdict = evaluate_extraction(prog)
        assert verdict.passed is True

    def test_failure_precedence_name_before_shell(self) -> None:
        """If both name and content are bad, the name failure wins (it's the
        more actionable root cause)."""
        prog = _good_program()
        prog["name_en"] = ""
        prog["tuition_amount"] = None
        prog["currency"] = None
        prog["deadlines"] = []
        prog["requirements"] = []
        verdict = evaluate_extraction(prog)
        assert verdict.passed is False
        assert verdict.reason == QuarantineReason.EMPTY_NAME

    def test_verdict_is_immutable_dataclass(self) -> None:
        """Signals dict is included in the verdict for downstream logging."""
        verdict: QualityVerdict = evaluate_extraction(_good_program())
        # Touching it shouldn't blow up.
        assert isinstance(verdict.signals, dict)
        assert verdict.passed is True
