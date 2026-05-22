"""Add extraction_audit.pagination_stop_reason column.

Revision ID: 20260522_0009
Revises: 20260521_0008
Create Date: 2026-05-22 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260522_0009"
down_revision = "20260521_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_audit",
        sa.Column("pagination_stop_reason", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_audit", "pagination_stop_reason")
