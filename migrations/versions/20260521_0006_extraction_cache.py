"""Add extraction_cache table for persistent LLM cleaner output caching.

Revision ID: 20260521_0006
Revises: 20260309_0005
Create Date: 2026-05-21 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260521_0006"
down_revision = "20260309_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_cache",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("cache_key"),
    )


def downgrade() -> None:
    op.drop_table("extraction_cache")
