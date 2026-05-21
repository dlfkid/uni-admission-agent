"""Tests for ProgramQuarantine model + quarantine DB operations."""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from src.models.quarantine import ProgramQuarantine
from src.services.quality_gate import QuarantineReason
from src.storage.quarantine_repo import QuarantineRepo


@pytest.fixture(name="session")
def fixture_session():
    """In-memory SQLite session with quarantine + supporting tables."""
    # Pull in dependent model modules so the mapper registry is complete.
    import src.models.admission  # noqa: F401
    import src.models.requirement  # noqa: F401
    import src.models.quarantine  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine, tables=[SQLModel.metadata.tables["program_quarantine"]]
    )
    with Session(engine) as session:
        yield session


def _sample_program(name: str = "MSc Finance") -> dict:
    return {
        "academic_year": 2026,
        "name_en": name,
        "name_zh": "",
        "source_url": "https://example.edu/finance",
    }


class TestProgramQuarantineModel:
    def test_model_persists_basic_fields(self, session: Session) -> None:
        entry = ProgramQuarantine(
            university_slug="hku",
            academic_year=2026,
            source_url="https://example.edu/p1",
            extracted_name="MSc Finance",
            payload='{"some":"json"}',
            quarantine_reason="empty_shell",
            quarantine_signals='{"deadline_count":0}',
        )
        session.add(entry)
        session.commit()

        loaded = session.exec(select(ProgramQuarantine)).one()
        assert loaded.university_slug == "hku"
        assert loaded.academic_year == 2026
        assert loaded.quarantine_reason == "empty_shell"
        assert loaded.id is not None  # auto-assigned PK


class TestQuarantineRepo:
    def test_record_inserts_new_entry(self, session: Session) -> None:
        repo = QuarantineRepo(session)
        repo.record(
            university_slug="hku",
            program_data=_sample_program(),
            reason=QuarantineReason.EMPTY_SHELL,
            signals={"deadline_count": 0},
        )
        rows = session.exec(select(ProgramQuarantine)).all()
        assert len(rows) == 1
        assert rows[0].quarantine_reason == QuarantineReason.EMPTY_SHELL.value
        assert rows[0].extracted_name == "MSc Finance"

    def test_record_same_source_url_overwrites(self, session: Session) -> None:
        """Re-extracting the same URL replaces the prior quarantine entry —
        we don't want the table to grow on every retry."""
        repo = QuarantineRepo(session)
        repo.record(
            university_slug="hku",
            program_data=_sample_program(name="First Attempt Name"),
            reason=QuarantineReason.EMPTY_SHELL,
            signals={"deadline_count": 0},
        )
        repo.record(
            university_slug="hku",
            program_data=_sample_program(name="Second Attempt Name"),
            reason=QuarantineReason.NOISE_NAME,
            signals={"name_length": 18},
        )
        rows = session.exec(select(ProgramQuarantine)).all()
        assert len(rows) == 1
        assert rows[0].extracted_name == "Second Attempt Name"
        assert rows[0].quarantine_reason == QuarantineReason.NOISE_NAME.value

    def test_list_by_university_year(self, session: Session) -> None:
        repo = QuarantineRepo(session)
        repo.record(
            university_slug="hku",
            program_data={
                **_sample_program(name="A"),
                "source_url": "https://example.edu/a",
            },
            reason=QuarantineReason.EMPTY_NAME,
            signals={},
        )
        repo.record(
            university_slug="hku",
            program_data={
                **_sample_program(name="B"),
                "academic_year": 2027,
                "source_url": "https://example.edu/b",
            },
            reason=QuarantineReason.NOISE_NAME,
            signals={},
        )
        repo.record(
            university_slug="ust",
            program_data={
                **_sample_program(name="C"),
                "source_url": "https://other.edu/c",
            },
            reason=QuarantineReason.EMPTY_SHELL,
            signals={},
        )

        hku_2026 = repo.list_for(university_slug="hku", year=2026)
        assert [r.extracted_name for r in hku_2026] == ["A"]

        hku_all = repo.list_for(university_slug="hku")
        assert {r.extracted_name for r in hku_all} == {"A", "B"}

        all_rows = repo.list_for()
        assert len(all_rows) == 3
