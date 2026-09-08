"""Validate -> persist -> read, end to end, with a page_processor-shaped payload.

Regression for I4 (final whole-branch review): the validate stage and
persistence were each tested separately; nothing drove a fee row through
_stage_validate_rules AND a real DatabaseManager.upsert_program AND back out
through query_programs / tuition_fee_dicts, matching what
src/scrapers/page_processor.py actually hands the pipeline: each fee dict is
ParsedTuitionFee.model_dump(mode="json") (amount as a STRING, e.g.
"198000.00" — Decimal serializes to string in JSON mode) plus
applicant_scope/is_derived added explicitly, since both are exclude=True on
the model.
"""

from decimal import Decimal
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from src.agents.cleaner_agent import ParsedTuitionFee
from src.models.admission import CurrencyCode, StudyMode, TuitionBasis, University
from src.services.crawler import query_programs
from src.services.ingestion_pipeline import IngestionPipeline
from src.storage.db_helpers import tuition_fee_dicts
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas


def _page_processor_fee(amount, basis, mode, applicant_scope="all", is_derived=False, text=None):
    """Exactly the shape page_processor.py (and importer.py) hand the
    validate stage: ParsedTuitionFee.model_dump(mode="json") plus the two
    computed fields added back on top."""
    fee = ParsedTuitionFee(amount=Decimal(amount), currency=CurrencyCode.HKD, basis=basis,
                           study_mode=mode, source_text=text)
    dumped = fee.model_dump(mode="json")
    assert dumped["amount"] == "198000.00"  # pin the exact string shape the pipeline receives
    assert "applicant_scope" not in dumped and "is_derived" not in dumped
    return {**dumped, "applicant_scope": applicant_scope, "is_derived": is_derived}


class TestTuitionFeeEndToEnd:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        _attach_sqlite_pragmas(self.engine)
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager()
        self.dm.engine = self.engine
        with Session(self.engine) as session:
            session.add(University(name="CUHK", slug="cuhk"))
            session.commit()

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def test_validate_persist_read_round_trip(self) -> None:
        stated = _page_processor_fee("198000.00", TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME,
                                     text="HK$198,000 per annum")
        derived = _page_processor_fee("198000.00", TuitionBasis.PER_PROGRAMME, StudyMode.FULL_TIME,
                                      is_derived=True, text="derived: 198000 per annum × 1 year")

        pipeline = IngestionPipeline(db_manager=MagicMock())
        request_payload = {"year": 2027}
        context = {
            "program_candidates": [
                {
                    "name_en": "MA in Anthropology",
                    "academic_year": 2027,
                    "source_url": "https://gs.cuhk.edu.hk/programmes/arts/ma-anthropology",
                    "tuition_fees": [stated, derived],
                },
            ]
        }

        validation_result = pipeline._stage_validate_rules(request_payload, context)
        assert validation_result["validated_count"] == 1
        validated_item = validation_result["validated_programs"][0]
        assert validated_item["tuition_fees"][0]["amount"] == "198000.00"

        program, created = self.dm.upsert_program(validated_item, "cuhk", enable_auto_translation=False)
        assert created is True

        programs = query_programs("cuhk", 2027)
        assert len(programs) == 1
        by_basis = {f["basis"]: f for f in programs[0].tuition_fees}
        assert set(by_basis) == {"per_annum", "per_programme"}
        assert by_basis["per_annum"]["amount"] == 198000.0
        assert isinstance(by_basis["per_annum"]["amount"], float)
        assert by_basis["per_programme"]["is_derived"] is True
        assert by_basis["per_annum"]["is_derived"] is False

        with Session(self.engine) as session:
            dicts = tuition_fee_dicts(session, program.id)
        assert {d["basis"] for d in dicts} == {"per_annum", "per_programme"}
        for row in dicts:
            assert isinstance(row["amount"], float)
        # tuition_fee_dicts is the single source query_programs itself uses, so
        # the two read surfaces must agree row for row.
        assert {d["basis"]: d for d in dicts} == by_basis
