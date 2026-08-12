"""Database migration service backed by Alembic."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from src.core.paths import get_bundle_dir

logger = logging.getLogger(__name__)

LEGACY_BASELINE_REVISION = "20260302_0001"


class MigrationError(Exception):
    """Raised when migration setup or execution fails."""


def _resolve_migration_paths() -> tuple[Path, Path]:
    root = get_bundle_dir()
    alembic_ini = root / "alembic.ini"
    migrations_dir = root / "migrations"

    if not alembic_ini.exists():
        raise MigrationError(f"Alembic config not found: {alembic_ini}")
    if not migrations_dir.exists():
        raise MigrationError(f"Migration scripts not found: {migrations_dir}")

    return alembic_ini, migrations_dir


def _resolve_db_url(db_url: str | None = None) -> str:
    url = db_url or os.getenv("DATABASE_URL")
    if not url:
        from src.storage.db_manager import DatabaseManager
        url = DatabaseManager._default_sqlite_url()  # pylint: disable=protected-access
    return url


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite")


def _sqlite_create_all(db_url: str) -> dict[str, Any]:
    """Bootstrap a SQLite database via ``SQLModel.metadata.create_all``.

    Alembic's migration history targets Postgres and uses dialect-specific
    DDL (CREATE TYPE for enums, ALTER TYPE for label rename) that doesn't
    translate to SQLite. For SQLite we simply materialise the current
    model state in one shot — the project has no shipped data to migrate,
    and ``create_all`` is idempotent, so re-running is safe.
    """
    # Importing here keeps alembic-only code paths from forcing the
    # full SQLModel registry to import during PG-only operations.
    from sqlmodel import SQLModel
    # Side-effect imports so all models register with the SQLModel registry.
    import src.models.admission  # noqa: F401  pylint: disable=unused-import
    import src.models.ingestion  # noqa: F401  pylint: disable=unused-import
    import src.models.requirement  # noqa: F401  pylint: disable=unused-import
    import src.models.taxonomy  # noqa: F401  pylint: disable=unused-import
    import src.models.quarantine  # noqa: F401  pylint: disable=unused-import
    import src.models.extraction_audit  # noqa: F401  pylint: disable=unused-import

    engine = create_engine(db_url)
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()
    return {
        "legacy_bootstrap": False,
        "before_revision": None,
        "after_revision": "sqlite-create-all",
        "head_revision": "sqlite-create-all",
        "pending": False,
        "engine": "sqlite",
    }


def _build_config(db_url: str | None = None) -> Config:
    alembic_ini, migrations_dir = _resolve_migration_paths()
    url = _resolve_db_url(db_url)

    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _get_current_revision(db_url: str) -> str | None:
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            ctx = MigrationContext.configure(connection)
            return ctx.get_current_revision()
    finally:
        engine.dispose()


def _get_head_revision(cfg: Config) -> str:
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    if not heads:
        raise MigrationError("No Alembic head revision found.")
    if len(heads) > 1:
        raise MigrationError(
            f"Multiple Alembic heads detected ({heads}). Merge heads before upgrade."
        )
    return heads[0]


def _legacy_tables_exist(db_url: str) -> bool:
    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        # Legacy schema is considered initialized when both core tables exist.
        return {"university", "program"}.issubset(tables)
    finally:
        engine.dispose()


def _bootstrap_legacy_schema(cfg: Config, db_url: str, head_revision: str) -> bool:
    current_revision = _get_current_revision(db_url)
    if current_revision:
        return False

    if not _legacy_tables_exist(db_url):
        return False

    script = ScriptDirectory.from_config(cfg)
    baseline_revision = (
        LEGACY_BASELINE_REVISION
        if script.get_revision(LEGACY_BASELINE_REVISION) is not None
        else head_revision
    )

    logger.info(
        "Detected legacy schema without alembic_version; stamping to baseline %s",
        baseline_revision,
    )
    command.stamp(cfg, baseline_revision)
    return True


def get_migration_status(db_url: str | None = None) -> dict[str, Any]:
    resolved_db_url = _resolve_db_url(db_url)
    if _is_sqlite_url(resolved_db_url):
        return {
            "current_revision": "sqlite-create-all",
            "head_revision": "sqlite-create-all",
            "pending": False,
            "engine": "sqlite",
        }

    cfg = _build_config(db_url)
    head_revision = _get_head_revision(cfg)
    current_revision = _get_current_revision(resolved_db_url)

    return {
        "current_revision": current_revision,
        "head_revision": head_revision,
        "pending": current_revision != head_revision,
    }


def run_db_migrations(
    db_url: str | None = None,
    revision: str = "head",
    verbose: bool = False,
) -> dict[str, Any]:
    resolved_db_url = _resolve_db_url(db_url)
    if _is_sqlite_url(resolved_db_url):
        if verbose:
            logger.info("SQLite detected — bootstrapping via create_all (alembic skipped)")
        return _sqlite_create_all(resolved_db_url)

    cfg = _build_config(db_url)
    head_revision = _get_head_revision(cfg)

    if verbose:
        logger.info("Applying database migrations to revision: %s", revision)

    legacy_bootstrap = _bootstrap_legacy_schema(cfg, resolved_db_url, head_revision)
    before_revision = _get_current_revision(resolved_db_url)
    command.upgrade(cfg, revision)
    after_revision = _get_current_revision(resolved_db_url)

    return {
        "legacy_bootstrap": legacy_bootstrap,
        "before_revision": before_revision,
        "after_revision": after_revision,
        "head_revision": head_revision,
        "pending": after_revision != head_revision,
    }
