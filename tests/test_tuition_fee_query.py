"""Programmes list their fee rows, and can be filtered by one row that matches."""

from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from src.models.admission import University
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas
from src.services.crawler import query_programs


def _row(amount, basis="per_programme", mode="FullTime", scope="non_local", derived=False):
    return {"amount": amount, "currency": "HKD", "basis": basis, "study_mode": mode,
            "applicant_scope": scope, "scope_label": None, "credits": None, "is_derived": derived,
            "source_text": None}


class TestQuery:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        _attach_sqlite_pragmas(self.engine)
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager(); self.dm.engine = self.engine
        with Session(self.engine) as s:
            s.add(University(name="X", slug="x")); s.commit()
        self._add("Cheap FT",  [_row(150000), _row(300000, mode="PartTime")])
        self._add("Pricey FT", [_row(350000)])
        self._add("Local only", [_row(90000, scope="local")])
        self._add("No fees", [])

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def _add(self, name, fees):
        self.dm.upsert_program({"name_en": name, "academic_year": 2027,
                                "source_url": f"https://x.edu/{name.replace(' ', '-')}",
                                "tuition_fees": fees}, "x", enable_auto_translation=False)

    def test_summaries_carry_the_rows(self) -> None:
        by_name = {p.name_en: p for p in query_programs("x", 2027)}
        assert len(by_name["Cheap FT"].tuition_fees) == 2
        assert by_name["Cheap FT"].tuition_fees[0]["basis"] == "per_programme"
        assert by_name["No fees"].tuition_fees == []

    def test_no_filter_returns_everything(self) -> None:
        assert len(query_programs("x", 2027)) == 4

    def test_tuition_max_keeps_programmes_with_one_matching_row(self) -> None:
        names = {p.name_en for p in query_programs("x", 2027, tuition_max=200000)}
        assert names == {"Cheap FT", "Local only"}

    def test_conditions_must_hold_on_the_same_row(self) -> None:
        """Cheap FT has a non_local row under 200k (FullTime) and a PartTime row
        over it — asking for PartTime under 200k must exclude it."""
        names = {p.name_en for p in query_programs("x", 2027, tuition_study_mode="PartTime", tuition_max=200000)}
        assert names == set()

    def test_scope_filter(self) -> None:
        names = {p.name_en for p in query_programs("x", 2027, tuition_scope="local")}
        assert names == {"Local only"}

    def test_tuition_max_is_inclusive(self) -> None:
        names = {p.name_en for p in query_programs("x", 2027, tuition_max=150000)}
        assert "Cheap FT" in names

    def test_one_year_per_annum_programme_is_visible_via_its_derived_row(self) -> None:
        """CUHK-shaped (I2): a 1-year FullTime per_annum fee also carries a
        derived FullTime/all/per_programme row, so the default per_programme
        filter and tuition_max can see a 1-year programme."""
        self._add("CUHK-shaped", [
            _row(198000, basis="per_annum", mode="FullTime", scope="all"),
            _row(198000, basis="per_programme", mode="FullTime", scope="all", derived=True),
        ])

        by_max = {p.name_en for p in query_programs("x", 2027, tuition_max=200000)}
        assert "CUHK-shaped" in by_max

        by_scope = {p.name_en for p in query_programs("x", 2027, tuition_scope="all")}
        assert "CUHK-shaped" in by_scope

        program = next(p for p in query_programs("x", 2027) if p.name_en == "CUHK-shaped")
        derived_row = next(f for f in program.tuition_fees if f["basis"] == "per_programme")
        assert derived_row["is_derived"] is True
        assert derived_row["amount"] == 198000.0


def test_get_programs_forwards_the_filters() -> None:
    from src.api import server as server_mod
    with patch.object(server_mod, "query_programs", return_value=[]) as spy:
        resp = TestClient(server_mod.app).get(
            "/programs", params={"univ_slug": "x", "year": 2027, "tuition_scope": "non_local",
                                 "tuition_study_mode": "FullTime", "tuition_max": 200000})
    assert resp.status_code == 200
    assert spy.call_args.kwargs["tuition_scope"] == "non_local"
    assert spy.call_args.kwargs["tuition_study_mode"] == "FullTime"
    assert spy.call_args.kwargs["tuition_max"] == 200000


def test_get_programs_rejects_an_unknown_scope() -> None:
    from src.api import server as server_mod
    resp = TestClient(server_mod.app).get("/programs", params={"univ_slug": "x", "tuition_scope": "martian"})
    assert resp.status_code == 422
