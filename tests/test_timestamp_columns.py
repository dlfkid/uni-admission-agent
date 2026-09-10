"""Every model timestamp must be timezone-aware, and agree with its migration.

There are two ways this schema comes into existence: an Alembic migration, or
``SQLModel.metadata.create_all()`` — which ``DatabaseManager`` calls on its
first session. Every migration in this project declares its timestamp columns
``sa.DateTime(timezone=True)``; SQLModel, given a bare ``datetime``
annotation, maps it to ``DateTime()`` without a timezone. The two paths
therefore produced different schemas for the same model, and a database
bootstrapped by ``create_all()`` drifted from the declared one silently.

The live database showed the result: 26 naive timestamp columns beside 3
aware ones, the 3 belonging to tables added late enough that only a migration
ever created them. Mixing the two raises
``TypeError: can't compare offset-naive and offset-aware datetimes`` in any
code that compares them, and a naive column loses the offset on write.

These tests pin the invariant at the source, where a new model field is
written — not in the database, where the damage only shows up later.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import DateTime
from sqlmodel import SQLModel

# Importing for the side effect of registering every table on
# SQLModel.metadata; a missed module would make this suite vacuously pass.
import src.models.admission  # noqa: F401
import src.models.extraction_audit  # noqa: F401
import src.models.ingestion  # noqa: F401
import src.models.quarantine  # noqa: F401
import src.models.requirement  # noqa: F401
import src.models.taxonomy  # noqa: F401


def _model_datetime_columns() -> list[tuple[str, str, bool]]:
    out = []
    for table in SQLModel.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, DateTime):
                out.append((table.name, col.name, bool(col.type.timezone)))
    return out


def test_metadata_registers_the_tables_we_expect() -> None:
    """Guard against a vacuous pass if a model module stops being imported."""
    cols = _model_datetime_columns()
    assert len(cols) >= 29, (
        f"only {len(cols)} datetime columns found — a model module is probably "
        "not imported, which would make the timezone test below meaningless"
    )


def test_every_model_datetime_column_is_timezone_aware() -> None:
    naive = [f"{t}.{c}" for t, c, aware in _model_datetime_columns() if not aware]
    assert not naive, (
        "these columns would be created naive by create_all() while their "
        f"migration creates them aware: {naive}. Annotate the field with "
        "sa_type=UTC_DATETIME (src/models/_timestamps.py)."
    )


def test_no_migration_declares_a_naive_timestamp() -> None:
    """The other half of the invariant — migrations must not drift either.

    Written as a source scan rather than a schema diff so it holds without a
    database, and so a new migration authored with a bare sa.DateTime() fails
    here rather than in production.
    """
    versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
    offenders: list[str] = []
    # sa.DateTime followed by ) or , with no timezone= argument
    naive_dt = re.compile(r"sa\.DateTime\(\s*\)")
    for path in sorted(versions.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if naive_dt.search(line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        f"migrations declaring a naive sa.DateTime(): {offenders}. "
        "Use sa.DateTime(timezone=True) — every other migration does."
    )


@pytest.mark.parametrize("table,column", [
    ("program_tuition_fee", "updated_at"),
    ("program", "updated_at"),
    ("program_deadline", "cutoff_date"),
    ("ingestion_task", "next_retry_at"),
    ("requirement_version", "valid_to"),
])
def test_named_columns_that_actually_drifted(table: str, column: str) -> None:
    """Spot-checks on the specific columns the drift was found on.

    program_tuition_fee.updated_at was the aware one; the rest were naive in
    the live database. Naming them keeps the regression concrete.
    """
    found = {(t, c): aware for t, c, aware in _model_datetime_columns()}
    assert (table, column) in found, f"{table}.{column} is no longer a datetime column"
    assert found[(table, column)] is True
