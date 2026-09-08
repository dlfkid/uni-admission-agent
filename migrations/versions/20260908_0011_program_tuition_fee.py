"""Add program_tuition_fee: one row per tuition statement on a programme page.

Program.tuition_amount holds one number, but pages publish several — by study
mode (CUHK), by applicant scope (EdUHK, Leeds, Manchester, UCL), or a
programme total alongside a per-credit rate (PolyU). Everything the single
column could not hold was dropped at extraction time. This table keeps each
statement; the coarse column stays and is derived from these rows in code.

No backfill: an existing tuition_amount cannot be decomposed into basis and
scope. Rows appear on the next crawl.

The `currency` column is a plain sa.String(16), not a Postgres enum type —
Program.currency has stored CurrencyCode the same way since the initial
schema (20260302_0001: `sa.Column("currency", sa.String(length=16))`), so
this table follows that precedent instead of introducing a native
`currencycode` enum type.

Revision ID: 20260908_0011
Revises: 20260812_0010
Create Date: 2026-09-08 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260908_0011"
down_revision = "20260812_0010"
branch_labels = None
depends_on = None

# create_type=False on all three: each type's CREATE/DROP is issued
# explicitly below (or, for studymode, already exists from 20260302_0002),
# so the column definitions must not also trigger an implicit CREATE TYPE
# during create_table — Postgres enum types auto-create by default when
# first used as a column type, which would otherwise collide with the
# explicit create() and fail with "type ... already exists".
#
# create_type is a postgresql.ENUM-only constructor argument — plain
# sa.Enum(..., create_type=False) silently drops it (generic Enum has no
# such parameter), leaving auto-create enabled regardless. Must use
# sqlalchemy.dialects.postgresql.ENUM here for the flag to take effect.
_BASIS = postgresql.ENUM(
    "per_programme", "per_annum", "per_semester", "per_credit",
    name="tuitionbasis", create_type=False,
)
_SCOPE = postgresql.ENUM("all", "local", "non_local", name="tuitionscope", create_type=False)
_MODE = postgresql.ENUM(
    "FullTime", "PartTime", "Hybrid", "Unknown", name="studymode", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _BASIS.create(bind, checkfirst=True)
    _SCOPE.create(bind, checkfirst=True)
    op.create_table(
        "program_tuition_fee",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("basis", _BASIS, nullable=False),
        sa.Column("study_mode", _MODE, nullable=False),
        sa.Column("applicant_scope", _SCOPE, nullable=False),
        sa.Column("scope_label", sa.String(), nullable=True),
        sa.Column("credits", sa.Integer(), nullable=True),
        sa.Column("is_derived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_text", sa.String(300), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["program_id"], ["program.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "program_id", "study_mode", "applicant_scope", "basis", name="uq_program_tuition_fee"
        ),
    )
    op.create_index("ix_program_tuition_fee_program_id", "program_tuition_fee", ["program_id"])
    op.create_index("ix_program_tuition_fee_basis", "program_tuition_fee", ["basis"])
    op.create_index("ix_program_tuition_fee_study_mode", "program_tuition_fee", ["study_mode"])
    op.create_index("ix_program_tuition_fee_applicant_scope", "program_tuition_fee", ["applicant_scope"])
    op.create_index(
        "ix_program_tuition_fee_filter", "program_tuition_fee",
        ["applicant_scope", "study_mode", "basis", "amount"],
    )


def downgrade() -> None:
    op.drop_index("ix_program_tuition_fee_filter", table_name="program_tuition_fee")
    op.drop_index("ix_program_tuition_fee_applicant_scope", table_name="program_tuition_fee")
    op.drop_index("ix_program_tuition_fee_study_mode", table_name="program_tuition_fee")
    op.drop_index("ix_program_tuition_fee_basis", table_name="program_tuition_fee")
    op.drop_index("ix_program_tuition_fee_program_id", table_name="program_tuition_fee")
    op.drop_table("program_tuition_fee")
    bind = op.get_bind()
    _SCOPE.drop(bind, checkfirst=True)
    _BASIS.drop(bind, checkfirst=True)
