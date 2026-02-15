"""Tests for src.utils.text — deterministic normalization and code generation."""

import pytest

from src.utils.text import generate_program_group_code, normalize_program_name


# ---------- normalize_program_name ---------- #


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Master of Science (Business Analytics)", "mastersciencebusinessanalytics"),
        ("Bachelor of Arts in Design", "bachelorartsdesign"),
        ("MSc in Computer Science", "msccomputerscience"),
        ("Master of Science (Artificial Intelligence)", "masterscienceartificialintelligence"),
        ("MBA", "mba"),
        ("PhD with Finance", "phdfinance"),
        ("Master & PhD", "masterphd"),
        ("   Spaced   Out   ", "spacedout"),
        ("The Master of The Arts", "masterarts"),
        ("MA in Translation & Interpretation", "matranslationinterpretation"),
    ],
)
def test_normalize_program_name(name: str, expected: str) -> None:
    assert normalize_program_name(name) == expected


def test_normalize_empty() -> None:
    assert normalize_program_name("") == "unknown"
    assert normalize_program_name("   ") == "unknown"


def test_normalize_all_stopwords() -> None:
    # Every token is a stopword — fallback concatenation
    result = normalize_program_name("of the")
    assert result == "ofthe"


# ---------- generate_program_group_code ---------- #


@pytest.mark.parametrize(
    "univ, name, expected",
    [
        ("hku", "Master of Finance", "hku#masterfinance"),
        ("nus", "MSc in Computer Science", "nus#msccomputerscience"),
        ("cuhk", "MBA", "cuhk#mba"),
        ("hku", "", "hku#unknown"),
    ],
)
def test_generate_program_group_code(univ: str, name: str, expected: str) -> None:
    assert generate_program_group_code(univ, name) == expected


def test_deterministic_idempotency() -> None:
    """Same inputs must always produce the same output."""
    code_a = generate_program_group_code("hku", "Master of Science in Finance")
    code_b = generate_program_group_code("hku", "Master of Science in Finance")
    assert code_a == code_b == "hku#mastersciencefinance"
