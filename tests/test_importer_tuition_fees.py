# tests/test_importer_tuition_fees.py
"""Excel `_tuition_raw` -> `tuition_fees` payload path (regression guard).

Covers the currency fix in `ExcelImporter._parse_row`
(`src/storage/importer.py`): the payload's `currency` must be the plain
string value of `CurrencyCode`, not the enum instance, since `str(enum)`
does not round-trip through `CurrencyCode(...)` in `_sync_tuition_fee_records`.
If `.value` is ever dropped, Excel-imported fee rows would silently be
skipped (logged and swallowed as an "unknown currency" `ValueError`).
"""

import pandas as pd
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from src.models.admission import CurrencyCode, StudyMode, TuitionBasis, TuitionScope, University
from src.models.requirement import ProgramTuitionFee
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas
from src.storage.importer import ExcelImporter


def _make_importer() -> ExcelImporter:
    """Build an ExcelImporter backed by a fresh in-memory SQLite DB.

    `ExcelImporter.__init__` only stores `file_path` and constructs
    `DatabaseManager()` itself (a singleton) -- it never checks that the
    file exists at construction time (only `import_data` does), so a
    placeholder path is fine here since we never call `import_data`. We
    reset the singleton and swap in an in-memory engine exactly as
    tests/test_tuition_fee_persistence.py does.
    """
    DatabaseManager._instance = None
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _attach_sqlite_pragmas(engine)
    SQLModel.metadata.create_all(engine)
    importer = ExcelImporter("placeholder.xlsx")
    importer.db_manager.engine = engine
    with Session(engine) as s:
        s.add(University(name="CUHK", slug="cuhk"))
        s.commit()
    return importer


class TestExcelTuitionRawPayloadShape:
    """`_parse_row` builds a `tuition_fees` entry with a *string* currency."""

    def test_tuition_cell_produces_one_fee_entry_with_string_currency(self) -> None:
        importer = _make_importer()
        row = pd.Series({"English Name": "MA in Anthropology", "Tuition Fee": "HK$180,000"})

        data = importer._parse_row(row)  # pylint: disable=protected-access

        assert data is not None
        fees = data["tuition_fees"]
        assert len(fees) == 1
        entry = fees[0]

        assert entry["currency"] == "HKD"
        assert isinstance(entry["currency"], str)
        assert entry["basis"] == "per_programme"
        assert entry["study_mode"] == "Unknown"
        assert entry["applicant_scope"] == "all"
        assert entry["is_derived"] is False
        assert entry["amount"] == data["tuition_amount"]
        assert entry["source_text"] == "HK$180,000"

    def test_row_without_tuition_cell_has_no_tuition_fees_key(self) -> None:
        importer = _make_importer()
        row = pd.Series({"English Name": "MA in Anthropology"})

        data = importer._parse_row(row)  # pylint: disable=protected-access

        assert data is not None
        assert "tuition_fees" not in data


class TestExcelTuitionRawPersistence:
    """End to end: the parsed payload lands as a single per_programme row."""

    def test_upsert_persists_one_hkd_per_programme_row(self) -> None:
        importer = _make_importer()
        row = pd.Series({"English Name": "MA in Anthropology", "Tuition Fee": "HK$180,000"})
        data = importer._parse_row(row)  # pylint: disable=protected-access
        data["academic_year"] = 2027
        data["source_url"] = "https://gs.cuhk.edu.hk/programmes/arts/ma-anthropology"

        program, _ = importer.db_manager.upsert_program(data, "cuhk", enable_auto_translation=False)

        with Session(importer.db_manager.engine) as s:
            rows = s.exec(
                select(ProgramTuitionFee).where(ProgramTuitionFee.program_id == program.id)
            ).all()

        assert len(rows) == 1
        row_out = rows[0]
        assert row_out.currency is CurrencyCode.HKD
        assert row_out.basis is TuitionBasis.PER_PROGRAMME
        assert row_out.applicant_scope is TuitionScope.ALL
        assert row_out.study_mode is StudyMode.UNKNOWN
        assert int(row_out.amount) == 180000
