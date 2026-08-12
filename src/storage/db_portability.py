"""Whole-database export/import — generic, metadata-driven (not a
hand-maintained table list). See docs/superpowers/specs/2026-08-11-db-export-import-design.md.
"""
from __future__ import annotations

import json
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum as SqlEnum,
    Integer,
    Numeric,
    Table,
    func,
    text,
)
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
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "alembic_revision": get_migration_status()["current_revision"],
            "tables": row_counts,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    return row_counts


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
        if not isinstance(pk_columns[0].type, Integer):
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
