"""Add subject_taxonomy table for canonical program-name matching.

Revision ID: 20260306_0004
Revises: 20260303_0004
Create Date: 2026-03-06 00:04:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260306_0004"
down_revision = "20260303_0004"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def _unique_exists(inspector: sa.Inspector, table_name: str, unique_name: str) -> bool:
    return unique_name in {uq["name"] for uq in inspector.get_unique_constraints(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "subject_taxonomy"):
        op.create_table(
            "subject_taxonomy",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name_en", sa.String(), nullable=False),
            sa.Column("normalized_name", sa.String(), nullable=False),
            sa.Column("aliases", sa.JSON(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("first_seen_url", sa.String(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "normalized_name",
                name="uq_subject_taxonomy_normalized_name",
            ),
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "subject_taxonomy") and not _unique_exists(
        inspector,
        "subject_taxonomy",
        "uq_subject_taxonomy_normalized_name",
    ):
        op.create_unique_constraint(
            "uq_subject_taxonomy_normalized_name",
            "subject_taxonomy",
            ["normalized_name"],
        )

    inspector = sa.inspect(bind)
    index_specs = (
        ("ix_subject_taxonomy_name_en", ["name_en"]),
        ("ix_subject_taxonomy_normalized_name", ["normalized_name"]),
        ("ix_subject_taxonomy_source", ["source"]),
        ("ix_subject_taxonomy_status", ["status"]),
    )
    for index_name, columns in index_specs:
        if _table_exists(inspector, "subject_taxonomy") and not _index_exists(
            inspector,
            "subject_taxonomy",
            index_name,
        ):
            op.create_index(index_name, "subject_taxonomy", columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "subject_taxonomy"):
        op.drop_table("subject_taxonomy")
