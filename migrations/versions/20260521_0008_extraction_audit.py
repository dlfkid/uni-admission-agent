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
        sa.Column(
            "recovered_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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

    op.create_table(
        "extraction_audit_link",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "audit_id",
            sa.Integer(),
            sa.ForeignKey("extraction_audit.id"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("anchor_text", sa.String(length=512), nullable=True),
        sa.Column("stage_dropped", sa.String(length=32), nullable=False),
    )
    op.create_index(
        "ix_extraction_audit_link_audit_id",
        "extraction_audit_link",
        ["audit_id"],
    )
    op.create_index(
        "ix_extraction_audit_link_stage_dropped",
        "extraction_audit_link",
        ["stage_dropped"],
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_audit_link_stage_dropped", table_name="extraction_audit_link")
    op.drop_index("ix_extraction_audit_link_audit_id", table_name="extraction_audit_link")
    op.drop_table("extraction_audit_link")
    op.drop_index("ix_extraction_audit_created_at", table_name="extraction_audit")
    op.drop_index("ix_extraction_audit_academic_year", table_name="extraction_audit")
    op.drop_index("ix_extraction_audit_university_slug", table_name="extraction_audit")
    op.drop_table("extraction_audit")
