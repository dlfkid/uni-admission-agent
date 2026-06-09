"""Tests for the expanded noise-name detector.

Beyond the original 'Course Search' / 'Browse by Faculty' patterns, the
detector should also catch generic faculty/department/school headings
and common navigation labels that LLMs occasionally extract as the
program name.
"""
from __future__ import annotations

import pytest

from src.scrapers.helpers import is_noise_program_name


# Names that MUST be flagged as noise — these are the failure cases the
# user is complaining about.
NOISE_CASES = [
    "Faculty of Business",
    "Faculty of Engineering",
    "Faculty of Arts and Humanities",
    "School of Computer Science",
    "School of Law",
    "Department of Mathematics",
    "Department of Psychology",
    "About the University",
    "About Us",
    "Apply Now",
    "Apply",
    "Apply Online",
    "Contact Us",
    "Contact",
    "Visit Us",
    "Visit",
    # Existing patterns still pass.
    "Course Search",
    "Postgraduate Programmes",
    "A to Z of Programmes",
    "Browse by Faculty",
]


# Entry-requirement sentences the LLM occasionally mis-extracts as the
# program name. These are REAL failures observed in a Leeds masters crawl
# (the program title is the actual name; the requirement leaked into name_en).
REQUIREMENT_NOISE_CASES = [
    "A bachelor degree with a 2:1 (hons)",
    "A bachelor degree with a 2:1 (hons) in an engineering discipline.",
    "A bachelor degree with a 2:1 (hons) in any subject.",
    "A bachelor degree with a 2:1 (hons) in computer science.",
    "A bachelor degree with a 2:1 (hons) in health-related subject + current registration",
    "A bachelor degree with a 2:1 (hons) in Music or Business.",
    "A good Bachelor degree plus management work experience",
    # Common phrasings of the same failure mode.
    "Applicants must hold a 2:1 honours degree",
    "We require an IELTS score of 6.5",
    "Entry requirements: a relevant undergraduate degree",
]


# Real program names that MUST NOT be flagged.
LEGITIMATE_CASES = [
    "MSc Finance",
    "MSc Finance with AI Specialization",
    "Master of Computer Science",
    "Bachelor of Engineering in Civil Engineering",
    "PhD in Economics",
    "MBA",
    "MA Education",
    "LLB Law",
    "BSc (Hons) Computer Science",
    "Master of Business Administration in International Business",
    # Programs that contain "faculty" or "school" or similar as part of name
    # (these are tricky but legitimate):
    "Master of Education (Faculty of Education option)",
    # Real Leeds masters titles from the same crawl — must survive the
    # requirement-sentence filter (regression guard against over-matching).
    "Advanced Computer Science (Artificial Intelligence) MSc",
    "Advanced Mechanical Engineering MSc (Eng)",
    "Accounting and Finance",
    "Applied and Professional Ethics PGDip",
]


@pytest.mark.parametrize("name", NOISE_CASES)
def test_noise_names_are_rejected(name: str) -> None:
    assert is_noise_program_name(name) is True, (
        f"expected {name!r} to be flagged as noise"
    )


@pytest.mark.parametrize("name", REQUIREMENT_NOISE_CASES)
def test_requirement_sentences_are_rejected(name: str) -> None:
    assert is_noise_program_name(name) is True, (
        f"expected requirement sentence {name!r} to be flagged as noise"
    )


@pytest.mark.parametrize("name", LEGITIMATE_CASES)
def test_legitimate_program_names_pass(name: str) -> None:
    assert is_noise_program_name(name) is False, (
        f"expected {name!r} to be accepted as a program name"
    )
