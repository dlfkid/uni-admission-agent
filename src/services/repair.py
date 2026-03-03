"""Automatic database repair workflow for non-technical users."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, inspect, text

from src.services.migrations import get_migration_status, run_db_migrations

logger = logging.getLogger(__name__)

_BACKUP_PREFIX = "_ua_backup_"
_BACKUP_NAME_PATTERN = re.compile(r"^_ua_backup_(\d{14})__(.+)$")
_MAX_BACKUP_SNAPSHOTS = 3


class RepairError(Exception):
    """Raised when automated repair cannot restore a usable state."""


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _current_snapshot_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _get_user_tables(db_url: str) -> list[str]:
    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        return [name for name in tables if not name.startswith(_BACKUP_PREFIX)]
    finally:
        engine.dispose()


def create_backup_snapshot(db_url: str) -> dict[str, str]:
    """Create in-database table snapshots for rollback."""
    tables = _get_user_tables(db_url)
    snapshot_id = _current_snapshot_id()
    backup_tables: dict[str, str] = {}

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for table in tables:
                backup_table = f"{_BACKUP_PREFIX}{snapshot_id}__{table}"
                conn.execute(
                    text(
                        f"CREATE TABLE {_quote_identifier(backup_table)} AS "
                        f"SELECT * FROM {_quote_identifier(table)}"
                    )
                )
                backup_tables[table] = backup_table
    finally:
        engine.dispose()

    logger.info("Created backup snapshot %s for %d tables", snapshot_id, len(backup_tables))
    return backup_tables


def _sorted_tables_for_restore(db_url: str, tables: list[str]) -> list[str]:
    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        sorted_entries = inspector.get_sorted_table_and_fkc_names()
        order = [name for name, _ in sorted_entries if name in tables]
        missing = [name for name in tables if name not in order]
        order.extend(missing)
        return order
    finally:
        engine.dispose()


def restore_from_snapshot(db_url: str, snapshot_id: str) -> None:
    """Restore regular tables from a given snapshot."""
    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        all_tables = set(inspector.get_table_names())
        backup_map: dict[str, str] = {}
        for table_name in all_tables:
            match = _BACKUP_NAME_PATTERN.match(table_name)
            if not match:
                continue
            found_snapshot, original = match.groups()
            if found_snapshot == snapshot_id:
                backup_map[original] = table_name

        if not backup_map:
            raise RepairError(f"No backup snapshot found for id={snapshot_id}")

        restore_order = _sorted_tables_for_restore(db_url, list(backup_map.keys()))
        delete_order = list(reversed(restore_order))

        with engine.begin() as conn:
            for table in delete_order:
                if table in backup_map:
                    conn.execute(text(f"DELETE FROM {_quote_identifier(table)}"))
            for table in restore_order:
                backup_table = backup_map[table]
                conn.execute(
                    text(
                        f"INSERT INTO {_quote_identifier(table)} "
                        f"SELECT * FROM {_quote_identifier(backup_table)}"
                    )
                )

            # Reset postgres sequences so future inserts don't collide.
            dialect = engine.dialect.name
            if dialect == "postgresql":
                for table in restore_order:
                    columns = {col["name"] for col in inspector.get_columns(table)}
                    if "id" not in columns:
                        continue
                    conn.execute(
                        text(
                            "SELECT setval("
                            "pg_get_serial_sequence(:table_name, 'id'), "
                            "COALESCE((SELECT MAX(id) FROM "
                            + _quote_identifier(table)
                            + "), 1), "
                            "(SELECT MAX(id) IS NOT NULL FROM "
                            + _quote_identifier(table)
                            + ")"
                            ")"
                        ),
                        {"table_name": table},
                    )
    finally:
        engine.dispose()

    logger.info("Restored database from backup snapshot %s", snapshot_id)


def cleanup_old_backups(db_url: str, keep: int = _MAX_BACKUP_SNAPSHOTS) -> None:
    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        grouped: dict[str, list[str]] = {}
        for table_name in inspector.get_table_names():
            match = _BACKUP_NAME_PATTERN.match(table_name)
            if not match:
                continue
            snapshot_id, _ = match.groups()
            grouped.setdefault(snapshot_id, []).append(table_name)

        snapshots = sorted(grouped.keys(), reverse=True)
        stale = snapshots[keep:]
        if not stale:
            return

        with engine.begin() as conn:
            for snapshot_id in stale:
                for backup_table in grouped[snapshot_id]:
                    conn.execute(text(f"DROP TABLE {_quote_identifier(backup_table)}"))
    finally:
        engine.dispose()


def check_database_health(db_url: str) -> dict[str, Any]:
    """Minimal health check for post-migration availability."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        required_tables = {"university", "program"}
        missing = sorted(required_tables - tables)
    finally:
        engine.dispose()

    return {"ok": not missing, "missing_tables": missing}


def run_auto_repair(db_url: str, verbose: bool = False) -> dict[str, Any]:
    """Run migration with rollback safety net for non-technical operators."""
    health_before = check_database_health(db_url)
    status_before = get_migration_status(db_url=db_url)

    if verbose:
        logger.info("Repair pre-check status: %s", status_before)

    backup_tables = create_backup_snapshot(db_url)
    snapshot_id = next(iter(backup_tables.values())).split("__", maxsplit=1)[0].replace(
        _BACKUP_PREFIX, ""
    ) if backup_tables else _current_snapshot_id()

    try:
        migration_result = run_db_migrations(db_url=db_url, verbose=verbose)
    except Exception as exc:
        restore_error = None
        restored = False
        try:
            restore_from_snapshot(db_url, snapshot_id)
            restored = True
        except Exception as restore_exc:  # pragma: no cover - rare fallback path
            restore_error = str(restore_exc)
        finally:
            try:
                cleanup_old_backups(db_url)
            except Exception:  # pragma: no cover - cleanup best effort
                pass

        raise RepairError(
            "Migration failed and rollback "
            + ("succeeded." if restored else "failed.")
            + f" migration_error={exc}"
            + (f" rollback_error={restore_error}" if restore_error else "")
        ) from exc

    health_after = check_database_health(db_url)
    cleanup_old_backups(db_url)

    return {
        "status_before": status_before,
        "health_before": health_before,
        "migration_result": migration_result,
        "health_after": health_after,
        "snapshot_id": snapshot_id,
    }
