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

    def test_record_falls_back_to_extra_metadata_source_url(self, session: Session) -> None:
        """page_processor's generic extraction path only ever sets source_url
        under extra_metadata, never as a top-level key. Without the fallback,
        every such rejection resolves to source_url="" and distinct pages
        collapse into a single overwritten row — this locks in the fix."""
        repo = QuarantineRepo(session)
        repo.record(
            university_slug="eduhk",
            program_data={
                **_sample_program(name="MACSLE"),
                "source_url": "",
                "extra_metadata": {"source_url": "https://eduhk.hk/fhm/macsle"},
            },
            reason=QuarantineReason.EMPTY_SHELL,
            signals={},
        )
        repo.record(
            university_slug="eduhk",
            program_data={
                **_sample_program(name="MADHCP"),
                "source_url": "",
                "extra_metadata": {"source_url": "https://eduhk.hk/fhm/madhcp"},
            },
            reason=QuarantineReason.EMPTY_SHELL,
            signals={},
        )
        rows = repo.list_for(university_slug="eduhk")
        assert {r.extracted_name for r in rows} == {"MACSLE", "MADHCP"}
        assert {r.source_url for r in rows} == {
            "https://eduhk.hk/fhm/macsle",
            "https://eduhk.hk/fhm/madhcp",
        }

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


class TestQuarantineRepoClear:
    """Cleanup operations on the quarantine table."""

    def _seed(self, session: Session) -> QuarantineRepo:
        repo = QuarantineRepo(session)
        repo.record(
            university_slug="hku",
            program_data={**_sample_program(name="A"), "source_url": "https://e.edu/a"},
            reason=QuarantineReason.EMPTY_SHELL,
            signals={},
        )
        repo.record(
            university_slug="hku",
            program_data={**_sample_program(name="B"), "source_url": "https://e.edu/b"},
            reason=QuarantineReason.NOISE_NAME,
            signals={},
        )
        repo.record(
            university_slug="ust",
            program_data={**_sample_program(name="C"), "source_url": "https://o.edu/c"},
            reason=QuarantineReason.EMPTY_SHELL,
            signals={},
        )
        return repo

    def test_clear_by_university(self, session: Session) -> None:
        repo = self._seed(session)
        deleted = repo.clear(university_slug="hku")
        assert deleted == 2
        remaining = repo.list_for()
        assert [r.university_slug for r in remaining] == ["ust"]

    def test_clear_by_university_and_reason(self, session: Session) -> None:
        repo = self._seed(session)
        deleted = repo.clear(university_slug="hku", reason=QuarantineReason.EMPTY_SHELL)
        assert deleted == 1
        remaining = {(r.university_slug, r.quarantine_reason) for r in repo.list_for()}
        assert remaining == {
            ("hku", "noise_name"),
            ("ust", "empty_shell"),
        }

    def test_clear_by_source_url(self, session: Session) -> None:
        repo = self._seed(session)
        deleted = repo.clear(university_slug="hku", source_url="https://e.edu/a")
        assert deleted == 1
        remaining = {r.source_url for r in repo.list_for()}
        assert remaining == {"https://e.edu/b", "https://o.edu/c"}

    def test_clear_nothing_matches_returns_zero(self, session: Session) -> None:
        repo = self._seed(session)
        deleted = repo.clear(university_slug="unknown-university")
        assert deleted == 0
        assert len(repo.list_for()) == 3

    def test_clear_requires_at_least_one_filter(self, session: Session) -> None:
        """Refuse to nuke the whole table with no filter."""
        repo = self._seed(session)
        with pytest.raises(ValueError):
            repo.clear()

    def test_clear_filters_by_academic_year(self, session: Session) -> None:
        """Year filter scopes the delete so other years' diagnostic
        history is preserved."""
        repo = QuarantineRepo(session)
        repo.record(
            university_slug="hku",
            program_data={**_sample_program(name="A"),
                          "academic_year": 2026, "source_url": "https://e.edu/a26"},
            reason=QuarantineReason.EMPTY_SHELL,
            signals={},
        )
        repo.record(
            university_slug="hku",
            program_data={**_sample_program(name="B"),
                          "academic_year": 2027, "source_url": "https://e.edu/b27"},
            reason=QuarantineReason.EMPTY_SHELL,
            signals={},
        )

        deleted = repo.clear(university_slug="hku", year=2026)
        assert deleted == 1
        remaining = repo.list_for(university_slug="hku")
        assert [r.academic_year for r in remaining] == [2027]
