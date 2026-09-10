"""Make every timestamp column timezone-aware, repairing create_all() drift.

Every migration in this project declares its timestamp columns
``sa.DateTime(timezone=True)``. SQLModel, given a bare ``datetime``
annotation, mapped them to ``DateTime()`` — without a timezone — so the two
ways this schema comes into existence disagreed:

    created by a migration      -> timestamp with time zone
    created by create_all()     -> timestamp without time zone

``DatabaseManager`` calls ``create_all()`` on its first session, so a
database that predates Alembic tracking for a given table has that table's
timestamps naive. The live database showed 26 naive columns beside 3 aware
ones; the 3 belong to tables (extraction_audit, program_quarantine,
program_tuition_fee) added late enough that only a migration ever created
them. The model side is fixed in src/models/_timestamps.py; this migration
repairs databases already carrying the drift.

WHY THE CAST CARRIES NO ``USING`` CLAUSE
----------------------------------------
This was verified against the live database rather than reasoned about.
Writing an aware UTC value into a naive column does NOT store UTC: Postgres
converts timestamptz -> timestamp through the session TimeZone and then drops
the offset, so the stored wall-clock is LOCAL. Probe on a server set to
Asia/Taipei, writing 2026-01-02 03:04:05+00:00:

    naive column                              -> 2026-01-02 11:04:05
    ALTER ... TYPE timestamptz (plain)        -> 2026-01-02 11:04:05+08:00  ✔ same instant
    ALTER ... TYPE timestamptz USING x
        AT TIME ZONE 'UTC'                    -> 2026-01-02 19:04:05+08:00  ✘ off by 8h

The plain cast interprets the stored value in the session TimeZone, which is
the same TimeZone that wrote it — so it recovers the original instant. An
``AT TIME ZONE 'UTC'`` clause, the intuitive choice, corrupts every row.

The one assumption left is that this migration runs under the same TimeZone
the rows were written under. That holds for a normal deployment; a database
whose server TimeZone changed between writing and migrating would need its
old zone named explicitly instead.

Columns are discovered from information_schema rather than hardcoded, because
which ones are naive depends on how that particular database was bootstrapped
 — on a database built purely by migrations this migration finds nothing and
does nothing. Only tables this project owns are considered.

Revision ID: 20260911_0012
Revises: 20260908_0011
Create Date: 2026-09-11 00:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260911_0012"
down_revision = "20260908_0011"
branch_labels = None
depends_on = None


# Tables this project owns. A column outside these is left alone even if it is
# a naive timestamp — alembic_version, extensions and anything else sharing
# the schema are none of this migration's business.
_OWNED_TABLES = (
    "exam_dim",
    "extraction_audit",
    "extraction_audit_link",
    "framework_dim",
    "ingestion_job",
    "ingestion_task",
    "program",
    "program_catalog",
    "program_deadline",
    "program_quarantine",
    "program_requirement",
    "program_study_option",
    "program_tuition_fee",
    "requirement_evidence",
    "requirement_version",
    "subject_dim",
    "subject_taxonomy",
    "university",
)


def _timestamp_columns(target_type: str) -> list[tuple[str, str]]:
    """Owned columns whose data_type is exactly *target_type*."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            select table_name, column_name
            from information_schema.columns
            where table_schema = current_schema()
              and data_type = :dt
              and table_name = any(:tables)
            order by table_name, column_name
            """
        ),
        {"dt": target_type, "tables": list(_OWNED_TABLES)},
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _convert(target: str, from_type: str) -> None:
    for table, column in _timestamp_columns(from_type):
        # Plain cast on purpose — see the module docstring. Quoted identifiers
        # because they come from the catalogue, not from a literal.
        op.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE {target}'
        )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # SQLite stores timestamps as text and has no distinct type to alter;
        # its schema comes from create_all(), which the model fix now makes
        # aware. Nothing to repair.
        return
    _convert("timestamptz", "timestamp without time zone")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # The inverse is also a plain cast: timestamptz -> timestamp renders the
    # instant in the session TimeZone and drops the offset, which is exactly
    # what the pre-migration columns held.
    _convert("timestamp", "timestamp with time zone")
