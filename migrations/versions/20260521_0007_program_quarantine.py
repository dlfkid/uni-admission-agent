"""Add program_quarantine table for failed-extraction records.

Revision ID: 20260521_0007
Revises: 20260309_0005
Create Date: 2026-05-21 14:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260521_0007"
down_revision = "20260309_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "program_quarantine",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("university_slug", sa.String(length=120), nullable=False),
        sa.Column("academic_year", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("extracted_name", sa.String(length=512), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("quarantine_reason", sa.String(length=64), nullable=False),
        sa.Column("quarantine_signals", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_program_quarantine_university_slug",
        "program_quarantine",
        ["university_slug"],
    )
    op.create_index(
        "ix_program_quarantine_academic_year",
        "program_quarantine",
        ["academic_year"],
    )
    op.create_index(
        "ix_program_quarantine_source_url",
        "program_quarantine",
        ["source_url"],
    )
    op.create_index(
        "ix_program_quarantine_quarantine_reason",
        "program_quarantine",
        ["quarantine_reason"],
    )


def downgrade() -> None:
    op.drop_index("ix_program_quarantine_quarantine_reason", table_name="program_quarantine")
    op.drop_index("ix_program_quarantine_source_url", table_name="program_quarantine")
    op.drop_index("ix_program_quarantine_academic_year", table_name="program_quarantine")
    op.drop_index("ix_program_quarantine_university_slug", table_name="program_quarantine")
    op.drop_table("program_quarantine")
