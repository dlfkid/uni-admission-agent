"""Add extraction_audit table for index→detail funnel tracking.

Revision ID: 20260521_0008
Revises: 20260521_0007
Create Date: 2026-05-21 16:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260521_0008"
down_revision = "20260521_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("university_slug", sa.String(length=120), nullable=False),
        sa.Column("academic_year", sa.Integer(), nullable=False),
        sa.Column("index_url", sa.String(length=1024), nullable=False),
        sa.Column("raw_link_count", sa.Integer(), nullable=False),
        sa.Column("llm_filtered_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("extracted_count", sa.Integer(), nullable=False),
        sa.Column("quarantined_count", sa.Integer(), nullable=False),
        sa.Column("job_uid", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_extraction_audit_university_slug",
        "extraction_audit",
        ["university_slug"],
    )
    op.create_index(
        "ix_extraction_audit_academic_year",
        "extraction_audit",
        ["academic_year"],
    )
    op.create_index(
        "ix_extraction_audit_created_at",
        "extraction_audit",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_audit_created_at", table_name="extraction_audit")
    op.drop_index("ix_extraction_audit_academic_year", table_name="extraction_audit")
    op.drop_index("ix_extraction_audit_university_slug", table_name="extraction_audit")
    op.drop_table("extraction_audit")
