# tests/test_tuition_fee_model.py
"""program_tuition_fee: one row per fee statement, keyed by (mode, scope, basis)."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from src.models.admission import (
    CurrencyCode, Program, ProgramCatalog, StudyMode, TuitionBasis, TuitionScope, University,
)
from src.models.requirement import ProgramTuitionFee
from src.storage.db_manager import _attach_sqlite_pragmas


def _engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _attach_sqlite_pragmas(engine)
    SQLModel.metadata.create_all(engine)
    return engine


def _program(session: Session) -> Program:
    uni = University(name="CUHK", slug="cuhk")
    session.add(uni); session.flush()
    catalog = ProgramCatalog(university_id=uni.id, canonical_name_en="MA in Anthropology", catalog_key="url:x")
    session.add(catalog); session.flush()
    prog = Program(name_en="MA in Anthropology", academic_year=2027,
                   university_id=uni.id, program_catalog_id=catalog.id)
    session.add(prog); session.flush()
    return prog


def test_a_fee_row_round_trips_with_all_dimensions() -> None:
    with Session(_engine()) as s:
        prog = _program(s)
        s.add(ProgramTuitionFee(
            program_id=prog.id, amount=Decimal("198000.00"), currency=CurrencyCode.HKD,
            basis=TuitionBasis.PER_ANNUM, study_mode=StudyMode.FULL_TIME,
            applicant_scope=TuitionScope.ALL, scope_label=None, credits=None,
            is_derived=False, source_text="HK$198,000 per annum",
        ))
        s.commit()
        row = s.exec(select(ProgramTuitionFee)).one()
        assert row.amount == Decimal("198000.00")
        assert row.basis is TuitionBasis.PER_ANNUM
        assert row.study_mode is StudyMode.FULL_TIME
        assert row.applicant_scope is TuitionScope.ALL
        assert row.is_derived is False


def test_the_sync_key_is_unique_per_program() -> None:
    with Session(_engine()) as s:
        prog = _program(s)
        for amount in ("1", "2"):
            s.add(ProgramTuitionFee(
                program_id=prog.id, amount=Decimal(amount), currency=CurrencyCode.HKD,
                basis=TuitionBasis.PER_PROGRAMME, study_mode=StudyMode.FULL_TIME,
                applicant_scope=TuitionScope.NON_LOCAL,
            ))
        with pytest.raises(IntegrityError):
            s.commit()


def test_program_exposes_its_fee_rows() -> None:
    with Session(_engine()) as s:
        prog = _program(s)
        s.add(ProgramTuitionFee(program_id=prog.id, amount=Decimal("1"), currency=CurrencyCode.HKD,
                                basis=TuitionBasis.PER_PROGRAMME))
        s.commit(); s.refresh(prog)
        assert len(prog.tuition_fee_records) == 1


def test_defaults_are_all_and_unknown_mode() -> None:
    row = ProgramTuitionFee(program_id=1, amount=Decimal("1"), currency=CurrencyCode.HKD,
                            basis=TuitionBasis.PER_PROGRAMME)
    assert row.study_mode is StudyMode.UNKNOWN
    assert row.applicant_scope is TuitionScope.ALL
    assert row.is_derived is False
