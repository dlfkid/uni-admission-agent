"""QueryRequest validates the tuition filter fields (I5).

Before this, QueryRequest carried tuition_scope/tuition_study_mode/
tuition_basis/tuition_max with no validators and no consumer — the MCP
db_query tool passed raw strings straight to query_programs, so a bad scope
surfaced as a ValueError deep inside a SQLAlchemy filter build instead of a
clear, tool-facing validation error, and an unrecognised study_mode silently
fell back to "Unknown" instead of failing loudly.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas import QueryRequest


def test_good_values_pass_through_normalised() -> None:
    req = QueryRequest(
        univ_slug="cuhk", year=2027,
        tuition_scope="  NON_LOCAL ", tuition_study_mode=" FullTime",
        tuition_basis="PER_ANNUM", tuition_max=200000,
    )
    assert req.tuition_scope == "non_local"
    assert req.tuition_study_mode == "FullTime"
    assert req.tuition_basis == "per_annum"


def test_none_filters_pass_through() -> None:
    req = QueryRequest(univ_slug="cuhk")
    assert req.tuition_scope is None
    assert req.tuition_study_mode is None
    assert req.tuition_basis is None
    assert req.tuition_max is None


def test_bad_scope_raises() -> None:
    with pytest.raises(ValidationError):
        QueryRequest(univ_slug="x", tuition_scope="martian")


def test_bad_study_mode_raises() -> None:
    with pytest.raises(ValidationError):
        QueryRequest(univ_slug="x", tuition_study_mode="martian")


def test_bad_basis_raises() -> None:
    with pytest.raises(ValidationError):
        QueryRequest(univ_slug="x", tuition_basis="martian")


def test_negative_tuition_max_raises() -> None:
    with pytest.raises(ValidationError):
        QueryRequest(univ_slug="x", tuition_max=-1)


def test_zero_tuition_max_is_allowed() -> None:
    assert QueryRequest(univ_slug="x", tuition_max=0).tuition_max == 0


@pytest.mark.parametrize("mode", ["FullTime", "PartTime", "Hybrid", "Unknown"])
def test_all_four_study_modes_accepted(mode: str) -> None:
    assert QueryRequest(univ_slug="x", tuition_study_mode=mode).tuition_study_mode == mode


@pytest.mark.parametrize("scope", ["all", "local", "non_local"])
def test_all_three_scopes_accepted(scope: str) -> None:
    assert QueryRequest(univ_slug="x", tuition_scope=scope).tuition_scope == scope


@pytest.mark.parametrize("basis", ["per_programme", "per_annum", "per_semester", "per_credit"])
def test_all_four_bases_accepted(basis: str) -> None:
    assert QueryRequest(univ_slug="x", tuition_basis=basis).tuition_basis == basis
