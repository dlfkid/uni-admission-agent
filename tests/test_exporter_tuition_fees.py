"""Export keeps Tuition/Currency and adds one JSON-string column of fee rows."""

import io
import json

import pandas as pd
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from src.models.admission import University
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas
from src.storage.exporter import ExcelExporter


def test_fee_rows_are_exported_as_a_json_string_column() -> None:
    DatabaseManager._instance = None
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _attach_sqlite_pragmas(engine); SQLModel.metadata.create_all(engine)
    dm = DatabaseManager(); dm.engine = engine
    with Session(engine) as s:
        s.add(University(name="CUHK", slug="cuhk")); s.commit()
    dm.upsert_program({
        "name_en": "MA in Anthropology", "academic_year": 2027, "source_url": "https://x/a",
        "tuition_amount": 198000, "currency": "HKD",
        "tuition_fees": [
            {"amount": 198000, "currency": "HKD", "basis": "per_annum", "study_mode": "FullTime",
             "applicant_scope": "all", "scope_label": None, "credits": None, "is_derived": False,
             "source_text": "HK$198,000 per annum"},
            {"amount": 99000, "currency": "HKD", "basis": "per_annum", "study_mode": "PartTime",
             "applicant_scope": "all", "scope_label": None, "credits": None, "is_derived": False,
             "source_text": None},
        ]}, "cuhk", enable_auto_translation=False)

    buf = io.BytesIO()
    assert ExcelExporter(output_stream=buf).export_data("cuhk", 2027) == 1
    buf.seek(0)
    df = pd.read_excel(buf)
    assert df.loc[0, "Tuition"] == 198000
    rows = json.loads(df.loc[0, "Tuition Fees (JSON)"])
    assert {r["study_mode"] for r in rows} == {"FullTime", "PartTime"}
    assert rows[0]["basis"] == "per_annum"
    DatabaseManager._instance = None
