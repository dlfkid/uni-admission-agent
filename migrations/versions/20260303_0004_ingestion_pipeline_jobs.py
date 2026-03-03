"""Add ingestion job/task tables for Phase 2 execution pipeline.

Revision ID: 20260303_0004
Revises: 20260302_0003
Create Date: 2026-03-03 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260303_0004"
down_revision = "20260302_0003"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def _unique_exists(inspector: sa.Inspector, table_name: str, unique_name: str) -> bool:
    return unique_name in {uq["name"] for uq in inspector.get_unique_constraints(table_name)}


def _foreign_key_exists(
    inspector: sa.Inspector,
    table_name: str,
    local_column: str,
    referred_table: str,
) -> bool:
    for fk in inspector.get_foreign_keys(table_name):
        if (
            set(fk.get("constrained_columns") or []) == {local_column}
            and fk.get("referred_table") == referred_table
        ):
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    stage_enum = sa.Enum(
        "fetch_raw",
        "extract_structured",
        "validate_rules",
        "persist_versioned",
        name="ingestionstage",
    )
    job_status_enum = sa.Enum(
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "POISONED",
        "CANCELLED",
        name="ingestionjobstatus",
    )
    task_state_enum = sa.Enum(
        "PENDING",
        "RUNNING",
        "RETRY_SCHEDULED",
        "SUCCEEDED",
        "FAILED",
        "POISONED",
        "SKIPPED",
        name="ingestiontaskstate",
    )

    if not _table_exists(inspector, "ingestion_job"):
        op.create_table(
            "ingestion_job",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_uid", sa.String(), nullable=False),
            sa.Column("univ_slug", sa.String(), nullable=False),
            sa.Column("academic_year", sa.Integer(), nullable=False),
            sa.Column("source_url", sa.String(), nullable=False),
            sa.Column("continue_depth", sa.Integer(), nullable=False),
            sa.Column("page_type_hint", sa.String(), nullable=False),
            sa.Column("status", job_status_enum, nullable=False),
            sa.Column("current_stage", stage_enum, nullable=True),
            sa.Column("resume_from_stage", stage_enum, nullable=True),
            sa.Column("request_payload", sa.JSON(), nullable=False),
            sa.Column("context_payload", sa.JSON(), nullable=False),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("job_uid", name="uq_ingestion_job_uid"),
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "ingestion_job") and not _unique_exists(
        inspector,
        "ingestion_job",
        "uq_ingestion_job_uid",
    ):
        op.create_unique_constraint(
            "uq_ingestion_job_uid",
            "ingestion_job",
            ["job_uid"],
        )

    inspector = sa.inspect(bind)
    ingestion_job_indexes = (
        ("ix_ingestion_job_job_uid", ["job_uid"]),
        ("ix_ingestion_job_univ_slug", ["univ_slug"]),
        ("ix_ingestion_job_academic_year", ["academic_year"]),
        ("ix_ingestion_job_status", ["status"]),
        ("ix_ingestion_job_current_stage", ["current_stage"]),
        ("ix_ingestion_job_resume_from_stage", ["resume_from_stage"]),
        ("ix_ingestion_job_created_at", ["created_at"]),
        ("ix_ingestion_job_updated_at", ["updated_at"]),
        ("ix_ingestion_job_started_at", ["started_at"]),
        ("ix_ingestion_job_finished_at", ["finished_at"]),
    )
    for index_name, columns in ingestion_job_indexes:
        if _table_exists(inspector, "ingestion_job") and not _index_exists(
            inspector,
            "ingestion_job",
            index_name,
        ):
            op.create_index(index_name, "ingestion_job", columns, unique=False)

    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "ingestion_task"):
        op.create_table(
            "ingestion_task",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("stage", stage_enum, nullable=False),
            sa.Column("state", task_state_enum, nullable=False),
            sa.Column("idempotency_key", sa.String(), nullable=True),
            sa.Column("input_payload", sa.JSON(), nullable=False),
            sa.Column("output_payload", sa.JSON(), nullable=False),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_retries", sa.Integer(), nullable=False),
            sa.Column("backoff_seconds", sa.Integer(), nullable=False),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["job_id"], ["ingestion_job.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("job_id", "stage", name="uq_ingestion_task_job_stage"),
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "ingestion_task") and not _unique_exists(
        inspector,
        "ingestion_task",
        "uq_ingestion_task_job_stage",
    ):
        op.create_unique_constraint(
            "uq_ingestion_task_job_stage",
            "ingestion_task",
            ["job_id", "stage"],
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "ingestion_task") and not _foreign_key_exists(
        inspector,
        "ingestion_task",
        "job_id",
        "ingestion_job",
    ):
        op.create_foreign_key(
            "fk_ingestion_task_job_id",
            "ingestion_task",
            "ingestion_job",
            ["job_id"],
            ["id"],
        )

    inspector = sa.inspect(bind)
    ingestion_task_indexes = (
        ("ix_ingestion_task_job_id", ["job_id"]),
        ("ix_ingestion_task_stage", ["stage"]),
        ("ix_ingestion_task_state", ["state"]),
        ("ix_ingestion_task_idempotency_key", ["idempotency_key"]),
        ("ix_ingestion_task_next_retry_at", ["next_retry_at"]),
        ("ix_ingestion_task_created_at", ["created_at"]),
        ("ix_ingestion_task_updated_at", ["updated_at"]),
        ("ix_ingestion_task_started_at", ["started_at"]),
        ("ix_ingestion_task_finished_at", ["finished_at"]),
    )
    for index_name, columns in ingestion_task_indexes:
        if _table_exists(inspector, "ingestion_task") and not _index_exists(
            inspector,
            "ingestion_task",
            index_name,
        ):
            op.create_index(index_name, "ingestion_task", columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "ingestion_task"):
        op.drop_table("ingestion_task")

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "ingestion_job"):
        op.drop_table("ingestion_job")

    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP TYPE IF EXISTS ingestiontaskstate"))
        bind.execute(sa.text("DROP TYPE IF EXISTS ingestionjobstatus"))
        bind.execute(sa.text("DROP TYPE IF EXISTS ingestionstage"))
