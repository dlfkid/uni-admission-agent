"""mcp_db_query routes its filters through QueryRequest for validation (I5)."""

import pytest
from pydantic import ValidationError

from src.api import server


def test_db_query_rejects_a_bad_scope(monkeypatch) -> None:
    def _fail_if_called(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("query_programs must not be reached with a bad filter")

    monkeypatch.setattr(server, "query_programs", _fail_if_called)

    with pytest.raises(ValidationError):
        server.mcp_db_query(univ_slug="cuhk", tuition_scope="martian")


def test_db_query_rejects_a_bad_study_mode(monkeypatch) -> None:
    def _fail_if_called(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("query_programs must not be reached with a bad filter")

    monkeypatch.setattr(server, "query_programs", _fail_if_called)

    with pytest.raises(ValidationError):
        server.mcp_db_query(univ_slug="cuhk", tuition_study_mode="martian")


def test_db_query_forwards_normalised_kwargs_to_query_programs(monkeypatch) -> None:
    captured = {}

    def _fake_query_programs(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(server, "query_programs", _fake_query_programs)

    result = server.mcp_db_query(
        univ_slug="cuhk", year=2027,
        tuition_scope="  NON_LOCAL ", tuition_study_mode=" FullTime",
        tuition_basis="PER_ANNUM", tuition_max=200000,
    )

    assert result == []
    assert captured == {
        "univ_slug": "cuhk", "year": 2027,
        "tuition_scope": "non_local", "tuition_study_mode": "FullTime",
        "tuition_basis": "per_annum", "tuition_max": 200000,
    }
