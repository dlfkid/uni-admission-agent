"""End-to-end integration test for db-export/db-import — exercises the real
CLI commands against real (temp-file) SQLite databases, with no mocking of
DatabaseManager, export_database, or import_database. This is a regression
test for two Critical bugs found in final review:

- C1: SQLite's create_all only created 14 of 17 tables because
  src.models.quarantine/extraction_audit were never eagerly imported.
- C2: db_import used to call the CLI's full _init_db(), which bootstraps
  subject_taxonomy and falsifies the "target database is empty" check.
"""
from __future__ import annotations

from sqlmodel import Session, select
from typer.testing import CliRunner

from src.cmd import cli
from src.models.admission import University
from src.services.subject_taxonomy import get_subject_taxonomy_service
from src.storage.db_manager import DatabaseManager

runner = CliRunner()


def teardown_function(_function) -> None:
    """Guard against leaking a tmp_path-bound singleton into later tests —
    matches the pattern already established in
    tests/test_db_portability.py's _PortabilityTestBase.teardown_method."""
    DatabaseManager._instance = None


def test_db_export_then_db_import_end_to_end(tmp_path, monkeypatch) -> None:
    DatabaseManager._instance = None
    # get_subject_taxonomy_service() is @lru_cache(maxsize=1): without
    # clearing it here, a cached service instance from a previous test (or
    # an earlier database in this same test) keeps pointing at the OLD
    # engine, so any taxonomy auto-seed silently lands on the wrong
    # database instead of the one DATABASE_URL currently points at.
    get_subject_taxonomy_service.cache_clear()
    source_url = f"sqlite:///{tmp_path / 'source.db'}"
    monkeypatch.setenv("DATABASE_URL", source_url)

    source_db = DatabaseManager()
    source_db.init_db()
    with Session(source_db.engine) as session:
        session.add(University(name="Leeds", slug="leeds"))
        session.commit()

    output = tmp_path / "export.zip"
    export_result = runner.invoke(cli.app, ["db-export", "--output", str(output)])
    assert export_result.exit_code == 0, export_result.output

    DatabaseManager._instance = None
    get_subject_taxonomy_service.cache_clear()
    target_url = f"sqlite:///{tmp_path / 'target.db'}"
    monkeypatch.setenv("DATABASE_URL", target_url)

    import_result = runner.invoke(
        cli.app, ["db-import", "--file", str(output), "--yes"]
    )
    assert import_result.exit_code == 0, import_result.output

    # Open a session against the target engine directly and confirm the
    # seeded row actually round-tripped — a zero exit code alone doesn't
    # catch a silent partial import (e.g. the import running against the
    # wrong database due to a stale cached service).
    target_db = DatabaseManager()
    with Session(target_db.engine) as session:
        universities = session.exec(select(University)).all()
        assert len(universities) == 1
        assert universities[0].slug == "leeds"
