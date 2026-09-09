"""The shared helper the API and the export both call for fee-row dicts."""

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from src.models.admission import University
from src.storage.db_helpers import tuition_fee_dicts
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas


def _row(amount, basis="per_annum", mode="FullTime", scope="all"):
    return {"amount": amount, "currency": "HKD", "basis": basis, "study_mode": mode,
            "applicant_scope": scope, "scope_label": None, "credits": None, "is_derived": False,
            "source_text": None}


class TestTuitionFeeDicts:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        _attach_sqlite_pragmas(self.engine)
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager(); self.dm.engine = self.engine
        with Session(self.engine) as s:
            s.add(University(name="X", slug="x")); s.commit()

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def test_returns_nine_keys_in_deterministic_order(self) -> None:
        program, _ = self.dm.upsert_program(
            {
                "name_en": "Two Rows", "academic_year": 2027, "source_url": "https://x.edu/two-rows",
                "tuition_fees": [
                    _row(300000, mode="PartTime"),
                    _row(150000, mode="FullTime"),
                ],
            },
            "x",
            enable_auto_translation=False,
        )
        with Session(self.engine) as session:
            rows = tuition_fee_dicts(session, program.id)

        assert len(rows) == 2
        # Ordered by scope, mode, study_mode ("FullTime" < "PartTime"), basis, id.
        assert [r["study_mode"] for r in rows] == ["FullTime", "PartTime"]
        for row in rows:
            assert set(row.keys()) == {
                "amount", "currency", "basis", "study_mode", "applicant_scope",
                "scope_label", "credits", "is_derived", "source_text",
            }
        assert rows[0]["amount"] == 150000.0
        assert isinstance(rows[0]["amount"], float)
        assert rows[0]["currency"] == "HKD"
        assert rows[0]["basis"] == "per_annum"

    def test_returns_empty_list_for_program_without_fees(self) -> None:
        program, _ = self.dm.upsert_program(
            {"name_en": "No Fees", "academic_year": 2027, "source_url": "https://x.edu/no-fees"},
            "x",
            enable_auto_translation=False,
        )
        with Session(self.engine) as session:
            rows = tuition_fee_dicts(session, program.id)

        assert rows == []
