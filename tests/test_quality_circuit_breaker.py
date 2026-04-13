"""Tests for the quality circuit breaker."""

import pytest

from src.agent_runtime.skills.impl.quality_circuit_breaker import (
    heuristic_quality_score,
    quality_check,
)


def _make_program(name: str, faculty: str = "", tuition: str = "") -> dict:
    return {
        "name_en": name,
        "faculty": faculty,
        "tuition_amount": tuition,
        "study_options": [],
    }


class TestHeuristicScore:
    def test_all_good_programs(self):
        programs = [
            _make_program("MSc Data Science", faculty="Computing", tuition="45000"),
            _make_program("MSc Business Analytics", faculty="Business"),
            _make_program("MA English Literature", tuition="30000"),
            _make_program("BSc Computer Science", faculty="Engineering", tuition="42000"),
            _make_program("MBA", faculty="Business", tuition="60000"),
        ]
        score = heuristic_quality_score(programs)
        assert score >= 0.7

    def test_all_noise_programs(self):
        programs = [
            _make_program("Skip to main content"),
            _make_program("Home"),
            _make_program("Search"),
            _make_program("Menu"),
            _make_program("Contact Us"),
        ]
        score = heuristic_quality_score(programs)
        assert score < 0.4

    def test_all_empty_names(self):
        programs = [_make_program("") for _ in range(5)]
        score = heuristic_quality_score(programs)
        assert score < 0.4

    def test_many_duplicates(self):
        programs = [_make_program("MSc Data Science", faculty="CS")] * 8 + [
            _make_program("MA History", faculty="Arts"),
            _make_program("BSc Physics", faculty="Science"),
        ]
        score = heuristic_quality_score(programs)
        assert score < 0.7  # Duplicate penalty

    def test_mixed_quality(self):
        programs = [
            _make_program("MSc Data Science", faculty="Computing"),
            _make_program(""),
            _make_program("MA History", faculty="Arts"),
            _make_program("Skip to content"),
            _make_program("BSc Physics"),
        ]
        score = heuristic_quality_score(programs)
        assert 0.4 <= score <= 0.7  # Uncertain zone


class TestQualityCheck:
    def test_good_batch_passes_without_llm(self):
        programs = [
            _make_program(f"MSc Program {i}", faculty="Faculty", tuition="40000")
            for i in range(10)
        ]
        result = quality_check(programs)
        assert result.verdict == "pass"
        assert result.llm_used is False
        assert result.heuristic_score >= 0.7

    def test_bad_batch_fails_without_llm(self):
        programs = [_make_program("") for _ in range(10)]
        result = quality_check(programs)
        assert result.verdict == "fail"
        assert result.llm_used is False
        assert result.heuristic_score < 0.4
