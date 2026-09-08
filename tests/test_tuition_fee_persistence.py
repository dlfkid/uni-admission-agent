# tests/test_tuition_fee_persistence.py
"""Fee rows are synced by (mode, scope, basis) on re-crawl and removed with the programme."""

from decimal import Decimal

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from src.models.admission import University
from src.models.requirement import ProgramTuitionFee
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas


def _row(amount, basis="per_annum", mode="FullTime", scope="all", derived=False, text=None):
    return {"amount": amount, "currency": "HKD", "basis": basis, "study_mode": mode,
            "applicant_scope": scope, "scope_label": None, "credits": None,
            "is_derived": derived, "source_text": text}


class TestTuitionFeeSync:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        _attach_sqlite_pragmas(self.engine)
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager()
        self.dm.engine = self.engine
        with Session(self.engine) as s:
            s.add(University(name="CUHK", slug="cuhk")); s.commit()

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def _upsert(self, fees, **extra):
        data = {"name_en": "MA in Anthropology", "academic_year": 2027,
                "source_url": "https://gs.cuhk.edu.hk/programmes/arts/ma-anthropology",
                "tuition_fees": fees, **extra}
        program, _ = self.dm.upsert_program(data, "cuhk", enable_auto_translation=False)
        return program

    def _rows(self, program_id):
        with Session(self.engine) as s:
            return s.exec(select(ProgramTuitionFee).where(ProgramTuitionFee.program_id == program_id)
                          .order_by(ProgramTuitionFee.amount)).all()

    def test_rows_are_inserted(self) -> None:
        p = self._upsert([_row(198000, mode="FullTime"), _row(99000, mode="PartTime")])
        rows = self._rows(p.id)
        assert [int(r.amount) for r in rows] == [99000, 198000]
        assert {r.study_mode.value for r in rows} == {"FullTime", "PartTime"}

    def test_recrawl_updates_amount_on_the_same_key(self) -> None:
        p = self._upsert([_row(198000)])
        self._upsert([_row(205000, text="HK$205,000 per annum")])
        rows = self._rows(p.id)
        assert len(rows) == 1 and int(rows[0].amount) == 205000 and rows[0].source_text == "HK$205,000 per annum"

    def test_recrawl_deletes_rows_no_longer_on_the_page(self) -> None:
        p = self._upsert([_row(198000, mode="FullTime"), _row(99000, mode="PartTime")])
        self._upsert([_row(198000, mode="FullTime")])
        assert len(self._rows(p.id)) == 1

    def test_derived_flag_and_credits_round_trip(self) -> None:
        p = self._upsert([{**_row(285000, basis="per_programme", mode="Unknown", derived=True,
                                  text="derived: 9500 per credit × 30 credits")},
                         {**_row(9500, basis="per_credit", mode="Unknown"), "credits": 30}])
        rows = {r.basis.value: r for r in self._rows(p.id)}
        assert rows["per_programme"].is_derived is True
        assert rows["per_credit"].credits == 30

    def test_payload_without_tuition_fees_leaves_existing_rows_alone(self) -> None:
        p = self._upsert([_row(198000)])
        data = {"name_en": "MA in Anthropology", "academic_year": 2027,
                "source_url": "https://gs.cuhk.edu.hk/programmes/arts/ma-anthropology", "faculty": "Arts"}
        self.dm.upsert_program(data, "cuhk", enable_auto_translation=False)
        assert len(self._rows(p.id)) == 1

    def test_deleting_the_program_removes_its_rows(self) -> None:
        p = self._upsert([_row(198000)])
        self.dm.delete_program_snapshot(p.id)
        with Session(self.engine) as s:
            assert s.exec(select(ProgramTuitionFee)).all() == []
