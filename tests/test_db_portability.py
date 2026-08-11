"""Tests for src/storage/db_portability.py — whole-database export/import."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from src.models.admission import CurrencyCode, Program, ProgramCatalog, University
from src.models.ingestion import (
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    IngestionTask,
)
from src.models.requirement import (
    ProgramRequirement,
    RequirementCategory,
    RequirementVersion,
)
from src.services.migrations import MigrationError
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas
from src.storage.db_portability import (
    DatabaseNotEmptyError,
    count_all_rows,
    export_database,
    get_portable_tables,
    import_database,
    is_database_empty,
)

EXPECTED_TABLE_NAMES = {
    "university", "program_catalog", "program",
    "subject_dim", "exam_dim", "framework_dim", "requirement_evidence",
    "requirement_version", "program_study_option", "program_deadline",
    "program_requirement",
    "ingestion_job", "ingestion_task",
    "subject_taxonomy",
    "program_quarantine",
    "extraction_audit_link", "extraction_audit",
}


class TestGetPortableTables:
    def test_returns_all_seventeen_tables_in_fk_order(self) -> None:
        tables = get_portable_tables()
        names = [t.name for t in tables]

        assert set(names) == EXPECTED_TABLE_NAMES
        # FK-dependency order: a parent must appear before any child that
        # references it via foreign_key=.
        assert names.index("university") < names.index("program_catalog")
        assert names.index("program_catalog") < names.index("program")
        assert names.index("program") < names.index("program_requirement")
        assert names.index("requirement_version") < names.index("program_requirement")
        assert names.index("ingestion_job") < names.index("ingestion_task")
        assert names.index("extraction_audit") < names.index("extraction_audit_link")


class _PortabilityTestBase:
    """Shared real in-memory SQLite fixture — mirrors the pattern already
    established in tests/test_db_manager.py's TestProgramDeleteScope."""

    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        _attach_sqlite_pragmas(self.engine)
        get_portable_tables()  # side-effect: registers all 17 tables first
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager()
        self.dm.engine = self.engine

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def _seed_multi_type_dataset(self):
        """One row touching each 'interesting' column type: DateTime
        (Program.updated_at, RequirementVersion.effective_at), Numeric
        (Program.tuition_amount), Enum (ProgramRequirement.category,
        IngestionJob.status/current_stage), JSON (Program.deadlines/
        study_options, IngestionJob.request_payload)."""
        with Session(self.engine) as session:
            leeds = University(name="Leeds", slug="leeds")
            session.add(leeds)
            session.commit()
            session.refresh(leeds)

            catalog = ProgramCatalog(
                university_id=leeds.id, catalog_key="msc-cs",
                canonical_name_en="MSc Computer Science",
            )
            session.add(catalog)
            session.commit()
            session.refresh(catalog)

            program = Program(
                university_id=leeds.id,
                program_catalog_id=catalog.id,
                academic_year=2026,
                name_en="MSc Computer Science",
                tuition_amount=Decimal("28500.50"),
                currency=CurrencyCode.GBP,
                deadlines=[{"round": 1, "description": "Main", "cutoff_date": "2026-01-15"}],
                study_options=[{"mode": "FullTime", "duration_months": 12}],
                # Naive on purpose: Program.updated_at is a plain `datetime`
                # column with no `DateTime(timezone=True)`, so SQLite (and
                # Postgres identically, for TIMESTAMP WITHOUT TIME ZONE)
                # silently drops tzinfo at write time — before
                # export_database ever reads the row. Seeding naive avoids
                # a tzinfo mismatch that has nothing to do with
                # db_portability's own round-trip fidelity.
                updated_at=datetime(2026, 1, 1, 12, 0, 0),
            )
            session.add(program)
            session.commit()
            session.refresh(program)

            version = RequirementVersion(
                program_id=program.id,
                effective_at=datetime(2026, 1, 2, 9, 30, 0, tzinfo=timezone.utc),
            )
            session.add(version)
            session.commit()
            session.refresh(version)

            session.add(
                ProgramRequirement(
                    program_id=program.id,
                    version_id=version.id,
                    category=RequirementCategory.LANGUAGE,
                    requirement_text="IELTS 6.5",
                )
            )

            job = IngestionJob(
                job_uid="job-1",
                univ_slug="leeds",
                academic_year=2026,
                status=IngestionJobStatus.SUCCEEDED,
                current_stage=IngestionStage.PERSIST_VERSIONED,
                request_payload={"url": "https://example.edu"},
            )
            session.add(job)
            session.commit()
            session.refresh(job)

            session.add(
                IngestionTask(
                    job_id=job.id,
                    stage=IngestionStage.PERSIST_VERSIONED,
                )
            )
            session.commit()

            return {"university_id": leeds.id, "program_id": program.id}


class TestExportDatabase(_PortabilityTestBase):
    def test_export_produces_manifest_and_per_table_counts(self, tmp_path) -> None:
        self._seed_multi_type_dataset()
        output = tmp_path / "export.zip"

        row_counts = export_database(str(output))

        assert output.exists()
        assert row_counts["university"] == 1
        assert row_counts["program"] == 1
        assert row_counts["program_quarantine"] == 0  # untouched table, present, empty

        import zipfile
        import json
        with zipfile.ZipFile(output) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["tables"] == row_counts
            assert "exported_at" in manifest
            assert "alembic_revision" in manifest

    def test_export_of_empty_database_succeeds(self, tmp_path) -> None:
        output = tmp_path / "empty.zip"
        row_counts = export_database(str(output))
        assert all(count == 0 for count in row_counts.values())
        assert set(row_counts.keys()) == EXPECTED_TABLE_NAMES


class TestImportDatabase(_PortabilityTestBase):
    def _mock_migrations(self, monkeypatch) -> MagicMock:
        mock = MagicMock(return_value={"pending": False, "after_revision": "head"})
        monkeypatch.setattr("src.storage.db_portability.run_db_migrations", mock)
        return mock

    def test_round_trip_preserves_types_and_values(self, tmp_path, monkeypatch) -> None:
        self._mock_migrations(monkeypatch)
        ids = self._seed_multi_type_dataset()
        output = tmp_path / "export.zip"
        export_database(str(output))

        # Fresh empty target engine — separate from the source.
        DatabaseManager._instance = None
        target_engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        _attach_sqlite_pragmas(target_engine)
        SQLModel.metadata.create_all(target_engine)
        target_dm = DatabaseManager()
        target_dm.engine = target_engine

        row_counts = import_database(str(output))

        assert row_counts["university"] == 1
        assert row_counts["program"] == 1

        with Session(target_engine) as session:
            program = session.exec(
                select(Program).where(Program.id == ids["program_id"])
            ).one()
            assert program.tuition_amount == Decimal("28500.50")
            assert isinstance(program.tuition_amount, Decimal)
            assert program.currency == CurrencyCode.GBP
            assert program.updated_at == datetime(2026, 1, 1, 12, 0, 0)
            assert program.deadlines == [
                {"round": 1, "description": "Main", "cutoff_date": "2026-01-15"}
            ]

            requirement = session.exec(select(ProgramRequirement)).one()
            assert requirement.category == RequirementCategory.LANGUAGE

            job = session.exec(select(IngestionJob)).one()
            assert job.status == IngestionJobStatus.SUCCEEDED
            assert job.current_stage == IngestionStage.PERSIST_VERSIONED
            assert job.request_payload == {"url": "https://example.edu"}

    def test_import_calls_migrations_to_head(self, tmp_path, monkeypatch) -> None:
        mock_migrate = self._mock_migrations(monkeypatch)
        export_database(str(tmp_path / "empty.zip"))

        DatabaseManager._instance = None
        target_engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        _attach_sqlite_pragmas(target_engine)
        SQLModel.metadata.create_all(target_engine)
        target_dm = DatabaseManager()
        target_dm.engine = target_engine

        import_database(str(tmp_path / "empty.zip"))

        mock_migrate.assert_called_once_with(revision="head")

    def test_import_refuses_nonempty_target_without_force(self, tmp_path, monkeypatch) -> None:
        self._mock_migrations(monkeypatch)
        self._seed_multi_type_dataset()
        output = tmp_path / "export.zip"
        export_database(str(output))
        # self.dm's engine (still the seeded, non-empty source) is the
        # "target" here — already has data, so import must refuse.
        with pytest.raises(DatabaseNotEmptyError):
            import_database(str(output))

    def test_import_with_force_attempts_insert_on_nonempty_target(self, tmp_path, monkeypatch) -> None:
        self._mock_migrations(monkeypatch)
        self._seed_multi_type_dataset()
        output = tmp_path / "export.zip"
        export_database(str(output))
        # Forcing into the already-seeded (non-empty) source-as-target
        # collides on unique constraints — must raise, not silently succeed.
        with pytest.raises(Exception):
            import_database(str(output), force=True)

    def test_import_raises_migration_error_when_still_pending(self, tmp_path, monkeypatch) -> None:
        self._seed_multi_type_dataset()
        output = tmp_path / "export.zip"
        export_database(str(output))

        # Fresh empty target engine — separate from the source.
        DatabaseManager._instance = None
        target_engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        _attach_sqlite_pragmas(target_engine)
        SQLModel.metadata.create_all(target_engine)
        target_dm = DatabaseManager()
        target_dm.engine = target_engine

        # Monkeypatch run_db_migrations to return pending=True, simulating
        # a schema migration that didn't fully complete.
        mock_migrate = MagicMock(
            return_value={"pending": True, "after_revision": "not-head"}
        )
        monkeypatch.setattr("src.storage.db_portability.run_db_migrations", mock_migrate)

        # Assert that import_database raises MigrationError when migrations
        # still have pending changes.
        with pytest.raises(MigrationError):
            import_database(str(output))


class TestCountAllRowsAndIsDatabaseEmpty(_PortabilityTestBase):
    def test_count_all_rows_reflects_seeded_data(self) -> None:
        self._seed_multi_type_dataset()
        with Session(self.engine) as session:
            counts = count_all_rows(session)
        assert counts["university"] == 1
        assert counts["program"] == 1
        assert counts["program_quarantine"] == 0

    def test_is_database_empty_true_before_seeding(self) -> None:
        with Session(self.engine) as session:
            assert is_database_empty(session) is True

    def test_is_database_empty_false_after_seeding(self) -> None:
        self._seed_multi_type_dataset()
        with Session(self.engine) as session:
            assert is_database_empty(session) is False
