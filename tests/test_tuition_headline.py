"""Scope normalisation and headline derivation for tuition rows.

The page's wording for who pays what varies (UK / Home / International /
Overseas / EU / 本地); the filterable column has three values. The mapping
lives here, not in the LLM.
"""

import pytest

from src.models.admission import TuitionScope
from src.agents.tuition_headline import normalize_applicant_scope


@pytest.mark.parametrize("label", [
    "Local", "local students", "Home", "UK", "UK/EU", "UK and EU", "Domestic", "本地", "本地学生",
])
def test_local_wordings(label: str) -> None:
    assert normalize_applicant_scope(label) is TuitionScope.LOCAL


@pytest.mark.parametrize("label", [
    "Non-local", "Non-local Students", "International", "International, including EU",
    "Overseas", "EU", "非本地", "國際學生",
])
def test_non_local_wordings(label: str) -> None:
    assert normalize_applicant_scope(label) is TuitionScope.NON_LOCAL


@pytest.mark.parametrize("label", [None, "", "  ", "for local and non-local students", "all applicants"])
def test_undistinguished_is_all(label) -> None:
    assert normalize_applicant_scope(label) is TuitionScope.ALL


def test_unknown_wording_maps_to_all_and_warns(caplog) -> None:
    with caplog.at_level("WARNING"):
        assert normalize_applicant_scope("Martian residents") is TuitionScope.ALL
    assert "Martian residents" in caplog.text


def test_uk_eu_before_brexit_is_local_but_bare_eu_is_non_local() -> None:
    """UK pages price EU with International today; the older 'UK/EU' pairing
    was one home band."""
    assert normalize_applicant_scope("UK/EU") is TuitionScope.LOCAL
    assert normalize_applicant_scope("EU") is TuitionScope.NON_LOCAL
