"""Align legacy enum labels to canonical values used by current models.

Revision ID: 20260309_0005
Revises: 20260306_0004
Create Date: 2026-03-09 20:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260309_0005"
down_revision = "20260306_0004"
branch_labels = None
depends_on = None


def _enum_label_exists(conn: sa.Connection, enum_name: str, label: str) -> bool:
    row = conn.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_type t
            JOIN pg_enum e ON e.enumtypid = t.oid
            WHERE t.typname = :enum_name
              AND e.enumlabel = :label
            LIMIT 1
            """
        ),
        {"enum_name": enum_name, "label": label},
    ).first()
    return row is not None


def _rename_enum_label(conn: sa.Connection, enum_name: str, old_label: str, new_label: str) -> None:
    if not _enum_label_exists(conn, enum_name, old_label):
        return
    if _enum_label_exists(conn, enum_name, new_label):
        return
    conn.execute(
        sa.text(
            f"ALTER TYPE {enum_name} RENAME VALUE :old_label TO :new_label"  # nosec B608
        ),
        {"old_label": old_label, "new_label": new_label},
    )


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    for old_label, new_label in (
        ("FETCH_RAW", "fetch_raw"),
        ("EXTRACT_STRUCTURED", "extract_structured"),
        ("VALIDATE_RULES", "validate_rules"),
        ("PERSIST_VERSIONED", "persist_versioned"),
    ):
        _rename_enum_label(conn, "ingestionstage", old_label, new_label)

    for old_label, new_label in (
        ("FULL_TIME", "FullTime"),
        ("PART_TIME", "PartTime"),
        ("HYBRID", "Hybrid"),
        ("UNKNOWN", "Unknown"),
    ):
        _rename_enum_label(conn, "studymode", old_label, new_label)

    for old_label, new_label in (
        ("ACADEMIC_SUBJECT", "academic_subject"),
        ("LANGUAGE", "language"),
        ("STANDARDIZED_TEST", "standardized_test"),
        ("PORTFOLIO", "portfolio"),
        ("EXPERIENCE", "experience"),
        ("OTHER", "other"),
    ):
        _rename_enum_label(conn, "requirementcategory", old_label, new_label)


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    for old_label, new_label in (
        ("fetch_raw", "FETCH_RAW"),
        ("extract_structured", "EXTRACT_STRUCTURED"),
        ("validate_rules", "VALIDATE_RULES"),
        ("persist_versioned", "PERSIST_VERSIONED"),
    ):
        _rename_enum_label(conn, "ingestionstage", old_label, new_label)

    for old_label, new_label in (
        ("FullTime", "FULL_TIME"),
        ("PartTime", "PART_TIME"),
        ("Hybrid", "HYBRID"),
        ("Unknown", "UNKNOWN"),
    ):
        _rename_enum_label(conn, "studymode", old_label, new_label)

    for old_label, new_label in (
        ("academic_subject", "ACADEMIC_SUBJECT"),
        ("language", "LANGUAGE"),
        ("standardized_test", "STANDARDIZED_TEST"),
        ("portfolio", "PORTFOLIO"),
        ("experience", "EXPERIENCE"),
        ("other", "OTHER"),
    ):
        _rename_enum_label(conn, "requirementcategory", old_label, new_label)
