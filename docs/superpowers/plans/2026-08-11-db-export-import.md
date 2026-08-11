# Whole-Database Export/Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user export the entire database (all 17 tables, any backend) to one portable zip file, and import that zip into a fresh install of either backend, via two new CLI commands.

**Architecture:** One new storage-layer module (`src/storage/db_portability.py`) does the generic, metadata-driven table dump/restore — it walks `SQLModel.metadata.sorted_tables` (FK-dependency order) instead of a hand-maintained table list, so new tables participate automatically. Two thin CLI commands (`db-export`/`db-import` in `src/cmd/cli.py`) call it, following the existing `db-reinit` command's confirm/error-handling pattern.

**Tech Stack:** Python 3.12, SQLAlchemy Core (`Table.select()`/`Table.insert()` — not the ORM layer, since the mechanism must work generically across arbitrary tables), stdlib `zipfile`/`json`, Typer.

## Global Constraints

- Export scope is all 17 `table=True` models — not a curated subset. No table is named in the implementation beyond a one-time explicit-import list needed to register two lazily-imported model modules (Task 1, Step 3) — the actual dump/restore loop is generic.
- Import assumes the target database is empty. Refuse (raise/exit, no partial write) if it isn't, unless `--force` is passed. `--force` only skips this guard — it does not add merge/upsert semantics; a real conflict still surfaces as a constraint-violation error.
- Original primary keys are preserved verbatim on import (safe because the target is assumed empty).
- `db-import` runs migrations to head before writing any data (reusing `run_db_migrations` from `src/services/migrations.py`, the same helper `db-reinit`/`db-migrate` already use).
- Postgres targets need a post-import sequence fix-up (advance each table's serial counter past its max inserted PK); SQLite needs no equivalent step.
- No REST/Web UI surface — CLI only.
- No merge/upsert into a non-empty target.
- No schema-version compatibility check beyond running migrations to head — `manifest.json`'s recorded source revision is informational only, never enforced.
- No streaming/chunking — whole dataset in memory, appropriate at this project's data scale.
- See spec: [`docs/superpowers/specs/2026-08-11-db-export-import-design.md`](../specs/2026-08-11-db-export-import-design.md).

---

### Task 1: Core module — `src/storage/db_portability.py`

**Files:**
- Create: `src/storage/db_portability.py`
- Test: `tests/test_db_portability.py`

**Interfaces:**
- Produces (all module-level, imported by name in Task 2):
  - `class DatabaseNotEmptyError(Exception)` — raised by `import_database` when the target has existing rows and `force=False`.
  - `get_portable_tables() -> list[sqlalchemy.Table]` — all 17 tables, FK-dependency order.
  - `count_all_rows(session: sqlmodel.Session) -> dict[str, int]` — table name → row count, for every portable table.
  - `is_database_empty(session: sqlmodel.Session) -> bool`.
  - `export_database(output_path: str) -> dict[str, int]` — writes the zip, returns table name → exported row count.
  - `import_database(file_path: str, force: bool = False) -> dict[str, int]` — returns table name → imported row count. Raises `DatabaseNotEmptyError` or `src.services.migrations.MigrationError`.

A note on why this task is one unit and not split further: the type
round-trip helpers (`_serialize_value`/`_deserialize_value`) can only be
meaningfully verified by actually running an export followed by an import
and comparing — there is no useful intermediate checkpoint between "helpers
exist" and "round-trip works," so this task delivers the whole module with
its full test suite in one pass.

**Important discovery baked into this task (read before writing Step 3):**
`SQLModel.metadata.sorted_tables` only contains tables whose defining
module has actually been imported somewhere in the running process.
`src/storage/db_manager.py`'s top-level imports already pull in
`src.models.admission`, `src.models.requirement`, `src.models.ingestion`,
and `src.models.taxonomy` — but **`src.models.quarantine` and
`src.models.extraction_audit` are only imported lazily**, inside
`src/storage/quarantine_repo.py` and `src/storage/audit_repo.py`
respectively (which are themselves only imported inside specific
`DatabaseManager` method bodies, e.g. `list_quarantine`). In a process that
never happens to call those methods first, `ProgramQuarantine`,
`ExtractionAudit`, and `ExtractionAuditLink` would be silently **absent**
from `SQLModel.metadata.sorted_tables` — a 14-of-17 export with no error.
`get_portable_tables()` (Step 3) must explicitly import both modules itself
to guarantee all 17 tables are always present, regardless of what else has
run before it in the process.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_portability.py`:

```python
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
                updated_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
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
            assert program.updated_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db_portability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.storage.db_portability'`.

- [ ] **Step 3: Implement `get_portable_tables`, `count_all_rows`, `is_database_empty`**

Create `src/storage/db_portability.py`:

```python
"""Whole-database export/import — generic, metadata-driven (not a
hand-maintained table list). See docs/superpowers/specs/2026-08-11-db-export-import-design.md.
"""
from __future__ import annotations

import json
import zipfile
from datetime import date, datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import Column, Date, DateTime, Enum as SqlEnum, Numeric, Table, func, text
from sqlmodel import Session, SQLModel, select

from src.services.migrations import MigrationError, get_migration_status, run_db_migrations
from src.storage.db_manager import DatabaseManager


class DatabaseNotEmptyError(Exception):
    """Raised by import_database when the target already has data and
    force=False."""


def get_portable_tables() -> list[Table]:
    """All 17 whole-database-portable tables, in FK-dependency order.

    Explicitly imports every model module that defines a table=True class.
    Two of them — src.models.quarantine and src.models.extraction_audit —
    are only imported lazily elsewhere in the codebase (inside specific
    DatabaseManager methods), so without this, a fresh process that hasn't
    happened to trigger those imports yet would silently export/import only
    14 of the 17 tables.
    """
    import src.models.admission  # noqa: F401
    import src.models.requirement  # noqa: F401
    import src.models.ingestion  # noqa: F401
    import src.models.taxonomy  # noqa: F401
    import src.models.quarantine  # noqa: F401
    import src.models.extraction_audit  # noqa: F401

    return list(SQLModel.metadata.sorted_tables)


def count_all_rows(session: Session) -> dict[str, int]:
    """Row count for every portable table, keyed by table name."""
    counts: dict[str, int] = {}
    for table in get_portable_tables():
        counts[table.name] = session.execute(
            select(func.count()).select_from(table)
        ).scalar_one()
    return counts


def is_database_empty(session: Session) -> bool:
    """True iff every portable table currently has zero rows."""
    return all(count == 0 for count in count_all_rows(session).values())
```

- [ ] **Step 4: Run the `get_portable_tables`/count/empty tests**

Run: `uv run pytest tests/test_db_portability.py -k "GetPortableTables or CountAllRowsAndIsDatabaseEmpty" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Implement the type round-trip helpers**

Append to `src/storage/db_portability.py`:

```python
def _serialize_value(value: Any, column: Column) -> Any:
    """Convert one DB-read value to a JSON-safe value.

    Enum columns come back from a raw Core select as actual Enum member
    instances (not their .value) — checked on the VALUE itself, not the
    column type, since that's simpler and works regardless of how the
    column's Enum was declared.
    """
    if value is None:
        return None
    if isinstance(value, PyEnum):
        return value.value
    if isinstance(column.type, (DateTime, Date)):
        return value.isoformat()
    if isinstance(column.type, Numeric):
        return str(value)
    return value


def _deserialize_value(value: Any, column: Column) -> Any:
    """Convert one JSON-loaded value back to the native Python type its
    column expects, so the DBAPI driver binds it correctly (Postgres is
    strict about this — SQLite is lenient enough to mask bugs here)."""
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    if isinstance(column.type, Numeric):
        return Decimal(value)
    if isinstance(column.type, SqlEnum) and column.type.enum_class is not None:
        return column.type.enum_class(value)
    return value
```

- [ ] **Step 6: Implement `export_database`**

Append to `src/storage/db_portability.py`:

```python
def export_database(output_path: str) -> dict[str, int]:
    """Write every portable table's rows to one zip file (manifest.json +
    one <table_name>.json per table). Returns {table_name: row_count}."""
    db = DatabaseManager()
    tables = get_portable_tables()
    row_counts: dict[str, int] = {}

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        with db.get_session() as session:
            for table in tables:
                rows = session.execute(select(table)).mappings().all()
                serialized = [
                    {col.name: _serialize_value(row[col.name], col) for col in table.columns}
                    for row in rows
                ]
                zf.writestr(f"{table.name}.json", json.dumps(serialized))
                row_counts[table.name] = len(serialized)

        manifest = {
            "exported_at": datetime.now().isoformat(),
            "alembic_revision": get_migration_status()["current_revision"],
            "tables": row_counts,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    return row_counts
```

- [ ] **Step 7: Run the export tests**

Run: `uv run pytest tests/test_db_portability.py -k TestExportDatabase -v`
Expected: PASS (2 tests).

- [ ] **Step 8: Implement `import_database` and the Postgres sequence fix-up**

Append to `src/storage/db_portability.py`:

```python
def _fix_postgres_sequences(
    session: Session, tables: list[Table], row_counts: dict[str, int]
) -> None:
    """Advance each table's serial sequence past its max inserted PK.

    Only meaningful on Postgres: primary keys were inserted explicitly
    (preserving the source's original IDs), so the serial sequence counter
    does not know to advance past them on its own. SQLite needs no
    equivalent — its rowid-based autoincrement already continues from the
    actual max rowid present, with no separate counter to desync.
    """
    for table in tables:
        if row_counts.get(table.name, 0) == 0:
            continue
        pk_columns = list(table.primary_key.columns)
        if len(pk_columns) != 1:
            continue
        pk_name = pk_columns[0].name
        session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table.name}', '{pk_name}'), "
                f'(SELECT MAX({pk_name}) FROM "{table.name}"))'
            )
        )


def import_database(file_path: str, force: bool = False) -> dict[str, int]:
    """Import a zip produced by export_database into the currently
    configured database. Returns {table_name: row_count} for the imported
    data.

    Raises DatabaseNotEmptyError if the target already has data and
    force=False. Raises src.services.migrations.MigrationError if the
    schema cannot be migrated to head.
    """
    db = DatabaseManager()
    tables = get_portable_tables()

    with db.get_session() as session:
        if not force and not is_database_empty(session):
            raise DatabaseNotEmptyError(
                "Target database already has data in one or more tables. "
                "Pass force=True to proceed anyway (a real conflict will "
                "still surface as a constraint-violation error)."
            )

    migration_result = run_db_migrations(revision="head")
    if migration_result["pending"]:
        raise MigrationError("Database schema is not at head after migration.")

    row_counts: dict[str, int] = {}
    with zipfile.ZipFile(file_path, "r") as zf, db.get_session() as session:
        for table in tables:
            raw_rows = json.loads(zf.read(f"{table.name}.json"))
            deserialized = [
                {col.name: _deserialize_value(row[col.name], col) for col in table.columns}
                for row in raw_rows
            ]
            if deserialized:
                session.execute(table.insert(), deserialized)
            row_counts[table.name] = len(deserialized)

        session.commit()

        if db.engine.dialect.name == "postgresql":
            _fix_postgres_sequences(session, tables, row_counts)
            session.commit()

    return row_counts
```

- [ ] **Step 9: Run the full test file**

Run: `uv run pytest tests/test_db_portability.py -v`
Expected: PASS (all tests — `GetPortableTables` 1, `ExportDatabase` 2, `ImportDatabase` 4, `CountAllRowsAndIsDatabaseEmpty` 3 — 10 total).

- [ ] **Step 10: Run the broader storage test suite to check for regressions**

Run: `uv run pytest tests/test_db_manager.py -v`
Expected: PASS, no regressions (this task only adds a new file — it does not modify `db_manager.py`).

- [ ] **Step 11: Commit**

```bash
git add src/storage/db_portability.py tests/test_db_portability.py
git commit -m "feat: add generic whole-database export/import (db_portability)"
```

---

### Task 2: CLI commands — `db-export` / `db-import`

**Files:**
- Modify: `src/cmd/cli.py:42` (crawler-adjacent import block — add a new import line for `db_portability`), `src/cmd/cli.py:1337` (two new commands, inserted right after `db_reinit` and before `repair`), `src/cmd/cli.py:104` (`get_help_text()` — add two lines, learned from the previous plan's final review that this listing is served by both `uni-admission help` and the MCP `help` tool and is easy to forget)
- Modify: `README.md:163` (CLI Commands table — add two rows right after the `db-reinit` row)
- Test: Create `tests/test_cli_db_export_import.py`

**Interfaces:**
- Consumes: `export_database`, `import_database`, `DatabaseNotEmptyError` (Task 1); `MigrationError` (already imported in `cli.py` from `src.services.migrations`); `_init_db`, `_setup_logging` (already defined in `cli.py`).
- Produces: `uni-admission db-export --output <file.zip> [--verbose]` and `uni-admission db-import --file <file.zip> [--yes] [--force] [--verbose]` CLI commands.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_db_export_import.py`:

```python
"""Tests for the 'db-export'/'db-import' CLI commands."""
from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from src.cmd import cli
from src.storage.db_portability import DatabaseNotEmptyError
from src.services.migrations import MigrationError

runner = CliRunner()


class TestDbExportCli:
    def test_export_writes_to_output_and_reports_counts(self) -> None:
        with (
            patch("src.cmd.cli._init_db"),
            patch(
                "src.cmd.cli.export_database",
                return_value={"university": 3, "program": 40},
            ) as mock_export,
        ):
            result = runner.invoke(cli.app, ["db-export", "--output", "out.zip"])

        assert result.exit_code == 0
        assert "43" in result.stdout  # total rows
        mock_export.assert_called_once_with("out.zip")

    def test_export_failure_exits_nonzero(self) -> None:
        with (
            patch("src.cmd.cli._init_db"),
            patch("src.cmd.cli.export_database", side_effect=RuntimeError("disk full")),
        ):
            result = runner.invoke(cli.app, ["db-export", "--output", "out.zip"])

        assert result.exit_code != 0


class TestDbImportCli:
    def test_import_cancelled_without_yes_exits_zero_and_does_not_import(self) -> None:
        with (
            patch("src.cmd.cli._init_db"),
            patch("src.cmd.cli.typer.confirm", return_value=False) as mock_confirm,
            patch("src.cmd.cli.import_database") as mock_import,
        ):
            result = runner.invoke(cli.app, ["db-import", "--file", "in.zip"])

        assert result.exit_code == 0
        assert "cancelled" in result.stdout.lower()
        mock_confirm.assert_called_once()
        mock_import.assert_not_called()

    def test_import_yes_skips_prompt_and_imports(self) -> None:
        with (
            patch("src.cmd.cli._init_db"),
            patch("src.cmd.cli.typer.confirm") as mock_confirm,
            patch(
                "src.cmd.cli.import_database",
                return_value={"university": 3, "program": 40},
            ) as mock_import,
        ):
            result = runner.invoke(cli.app, ["db-import", "--file", "in.zip", "--yes"])

        assert result.exit_code == 0
        assert "43" in result.stdout
        mock_confirm.assert_not_called()
        mock_import.assert_called_once_with("in.zip", force=False)

    def test_import_passes_force_flag_through(self) -> None:
        with (
            patch("src.cmd.cli._init_db"),
            patch(
                "src.cmd.cli.import_database", return_value={"university": 0}
            ) as mock_import,
        ):
            result = runner.invoke(
                cli.app, ["db-import", "--file", "in.zip", "--yes", "--force"]
            )

        assert result.exit_code == 0
        mock_import.assert_called_once_with("in.zip", force=True)

    def test_import_nonempty_target_without_force_reports_error(self) -> None:
        with (
            patch("src.cmd.cli._init_db"),
            patch(
                "src.cmd.cli.import_database",
                side_effect=DatabaseNotEmptyError("Target database already has data."),
            ),
        ):
            result = runner.invoke(cli.app, ["db-import", "--file", "in.zip", "--yes"])

        assert result.exit_code != 0
        assert "already has data" in result.stdout

    def test_import_migration_failure_reports_error(self) -> None:
        with (
            patch("src.cmd.cli._init_db"),
            patch(
                "src.cmd.cli.import_database",
                side_effect=MigrationError("schema not at head"),
            ),
        ):
            result = runner.invoke(cli.app, ["db-import", "--file", "in.zip", "--yes"])

        assert result.exit_code != 0
        assert "migration" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_db_export_import.py -v`
Expected: FAIL — Typer reports no such command `db-export`/`db-import` (and the `from src.storage.db_portability import DatabaseNotEmptyError` in the test file itself will already succeed since Task 1 is done — only the CLI commands are missing).

- [ ] **Step 3: Add the import**

In `src/cmd/cli.py`, add a new import statement right after the existing `from src.services.crawler import (...)` block (do not add these names into that block — `db_portability` is a different module):

```python
from src.storage.db_portability import DatabaseNotEmptyError, export_database, import_database
```

- [ ] **Step 4: Implement the two commands**

Insert into `src/cmd/cli.py`, right after `db_reinit`'s closing `except Exception as e: ... raise typer.Exit(code=1)` block and before `@app.command()\ndef repair(...)`:

```python
@app.command(name="db-export")
def db_export(
    output: str = typer.Option(..., "--output", help="Output zip file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Export the entire database (all tables) to one portable zip file."""
    _setup_logging(verbose)
    _init_db(verbose)

    typer.echo(f"Exporting database → {output}")
    try:
        row_counts = export_database(output)
    except Exception as e:
        typer.echo(f"❌ Database export failed: {e}", err=True)
        raise typer.Exit(code=1)

    total = sum(row_counts.values())
    typer.echo(f"✅ Exported {total} rows across {len(row_counts)} tables → {output}")


@app.command(name="db-import")
def db_import(
    file: str = typer.Option(..., "--file", help="Zip file produced by db-export"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    force: bool = typer.Option(
        False, "--force", help="Proceed even if the target database is not empty"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Import a database snapshot produced by db-export.

    Assumes the target database is empty — refuses otherwise unless
    --force is passed (which skips the check, not a merge: a real
    conflict still surfaces as a constraint-violation error). Runs
    pending migrations to head before writing any data.
    """
    _setup_logging(verbose)
    _init_db(verbose)

    if not yes:
        confirm = typer.confirm(
            f"This will import data from {file!r} into the currently "
            "configured database. Continue?",
            default=False,
        )
        if not confirm:
            typer.echo("ℹ️  Database import cancelled.")
            raise typer.Exit(code=0)

    try:
        row_counts = import_database(file, force=force)
    except DatabaseNotEmptyError as e:
        typer.echo(f"❌ {e}", err=True)
        typer.echo("👉 Re-run with --force to proceed anyway.", err=True)
        raise typer.Exit(code=1)
    except MigrationError as e:
        typer.echo(f"❌ Database migration failed: {e}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"❌ Database import failed: {e}", err=True)
        raise typer.Exit(code=1)

    total = sum(row_counts.values())
    typer.echo(f"✅ Imported {total} rows across {len(row_counts)} tables from {file}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_db_export_import.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Add the `get_help_text()` entries**

In `src/cmd/cli.py`'s `get_help_text()` function, add two lines right after the existing `programs delete` line (in the `DATABASE & STATUS:` section), matching the column-alignment style already there:

```
    db-export        Export the entire database to one portable zip file
    db-import        Import a database snapshot produced by db-export
```

This listing is served by both `uni-admission help` and the MCP `help` tool (`src/api/server.py` imports `get_help_text` from the CLI) — a prior round of this same series shipped a command without this entry and it went unnoticed until a final whole-branch review caught it, so this step is not optional polish.

- [ ] **Step 7: Update the README CLI Commands table**

In `README.md`, add two rows right after the existing `db-reinit` row:

```markdown
| `uni-admission db-export --output <file.zip>` | Export the entire database (all tables) to one portable zip file |
| `uni-admission db-import --file <file.zip> [--yes] [--force]` | Import a database snapshot produced by db-export (target must be empty unless `--force`) |
```

- [ ] **Step 8: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: PASS, same pass count as baseline plus this plan's new tests (Task 1's 10 + Task 2's 7 = 17 new), no unrelated failures.

- [ ] **Step 9: Run pylint on touched files**

Run: `uv run pylint src/storage/db_portability.py src/cmd/cli.py`
Expected: no new errors introduced by this change.

- [ ] **Step 10: Commit**

```bash
git add src/cmd/cli.py README.md tests/test_cli_db_export_import.py
git commit -m "feat: add 'db-export'/'db-import' CLI commands"
```
