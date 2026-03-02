"""Initial schema for university/program tables.

Revision ID: 20260302_0001
Revises:
Create Date: 2026-03-02 12:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260302_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "university" not in tables:
        op.create_table(
            "university",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    university_indexes = {idx["name"] for idx in inspector.get_indexes("university")}
    if "ix_university_name" not in university_indexes:
        op.create_index("ix_university_name", "university", ["name"], unique=True)
    if "ix_university_slug" not in university_indexes:
        op.create_index("ix_university_slug", "university", ["slug"], unique=True)

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "program" not in tables:
        op.create_table(
            "program",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("academic_year", sa.Integer(), nullable=False),
            sa.Column("name_zh", sa.String(), nullable=True),
            sa.Column("name_en", sa.String(), nullable=False),
            sa.Column("program_group_code", sa.String(), nullable=True),
            sa.Column("faculty", sa.String(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("is_discontinued", sa.Boolean(), nullable=False),
            sa.Column("tuition_amount", sa.Numeric(precision=12, scale=2), nullable=True),
            sa.Column("currency", sa.String(length=16), nullable=True),
            sa.Column("study_options", sa.JSON(), nullable=False),
            sa.Column("deadlines", sa.JSON(), nullable=False),
            sa.Column("extra_metadata", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("university_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["university_id"], ["university.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "university_id",
                "academic_year",
                "name_en",
                name="uq_program_year",
            ),
        )

    inspector = sa.inspect(bind)
    program_indexes = {idx["name"] for idx in inspector.get_indexes("program")}
    for index_name, columns in (
        ("ix_program_academic_year", ["academic_year"]),
        ("ix_program_name_zh", ["name_zh"]),
        ("ix_program_name_en", ["name_en"]),
        ("ix_program_program_group_code", ["program_group_code"]),
        ("ix_program_faculty", ["faculty"]),
    ):
        if index_name not in program_indexes:
            op.create_index(index_name, "program", columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "program" in tables:
        program_indexes = {idx["name"] for idx in inspector.get_indexes("program")}
        for index_name in (
            "ix_program_faculty",
            "ix_program_program_group_code",
            "ix_program_name_en",
            "ix_program_name_zh",
            "ix_program_academic_year",
        ):
            if index_name in program_indexes:
                op.drop_index(index_name, table_name="program")
        op.drop_table("program")

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "university" in tables:
        university_indexes = {idx["name"] for idx in inspector.get_indexes("university")}
        for index_name in ("ix_university_slug", "ix_university_name"):
            if index_name in university_indexes:
                op.drop_index(index_name, table_name="university")
        op.drop_table("university")
