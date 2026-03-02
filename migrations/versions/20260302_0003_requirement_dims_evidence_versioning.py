"""Add requirement dimensions, evidence, and versioning with backfill.

Revision ID: 20260302_0003
Revises: 20260302_0002
Create Date: 2026-03-02 20:20:00
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "20260302_0003"
down_revision = "20260302_0002"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


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


def _normalize_key(value: Any, prefix: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        return prefix
    return text


def _infer_exam(category: Any, subject_name: Any, framework: Any, requirement_text: Any) -> tuple[str, str, str] | None:
    merged = " ".join(
        str(x or "").lower().strip()
        for x in (subject_name, framework, requirement_text)
    )
    if not merged:
        return None

    patterns = (
        ("ielts", "IELTS", "language"),
        ("toefl", "TOEFL", "language"),
        ("sat", "SAT", "standardized"),
        ("act", "ACT", "standardized"),
        ("gre", "GRE", "standardized"),
        ("gmat", "GMAT", "standardized"),
        ("a-level", "A-Level", "curriculum"),
        ("a level", "A-Level", "curriculum"),
        ("ib", "IB", "curriculum"),
        ("ap", "AP", "curriculum"),
    )
    for token, display_name, family in patterns:
        if token in merged:
            return _normalize_key(display_name), display_name, family

    category_text = str(category or "").strip().lower()
    if category_text == "standardized_test":
        display_name = str(subject_name or framework or "Standardized Test").strip() or "Standardized Test"
        return _normalize_key(display_name), display_name, "standardized"

    return None


def _parse_json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return default
    return default


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "subject_dim"):
        op.create_table(
            "subject_dim",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("normalized_name", sa.String(), nullable=False),
            sa.Column("canonical_name", sa.String(), nullable=False),
            sa.Column("aliases", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("normalized_name", name="uq_subject_dim_normalized_name"),
        )

    if not _table_exists(inspector, "exam_dim"):
        op.create_table(
            "exam_dim",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("family", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_exam_dim_code"),
        )

    if not _table_exists(inspector, "framework_dim"):
        op.create_table(
            "framework_dim",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_framework_dim_code"),
        )

    if not _table_exists(inspector, "requirement_evidence"):
        op.create_table(
            "requirement_evidence",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source_url", sa.String(), nullable=True),
            sa.Column("page_title", sa.String(), nullable=True),
            sa.Column("page_snippet", sa.String(), nullable=True),
            sa.Column("locator_type", sa.String(), nullable=True),
            sa.Column("locator_value", sa.String(), nullable=True),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("content_hash", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _table_exists(inspector, "requirement_version"):
        op.create_table(
            "requirement_version",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
            sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
            sa.Column("change_summary", sa.String(), nullable=True),
            sa.Column("diff_payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("program_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["program_id"], ["program.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "program_id",
                "version_no",
                name="uq_requirement_version_program_no",
            ),
        )

    inspector = sa.inspect(bind)
    index_specs = (
        ("subject_dim", "ix_subject_dim_normalized_name", ["normalized_name"]),
        ("subject_dim", "ix_subject_dim_canonical_name", ["canonical_name"]),
        ("exam_dim", "ix_exam_dim_code", ["code"]),
        ("exam_dim", "ix_exam_dim_display_name", ["display_name"]),
        ("exam_dim", "ix_exam_dim_family", ["family"]),
        ("framework_dim", "ix_framework_dim_code", ["code"]),
        ("framework_dim", "ix_framework_dim_display_name", ["display_name"]),
        ("framework_dim", "ix_framework_dim_region", ["region"]),
        ("requirement_evidence", "ix_requirement_evidence_locator_type", ["locator_type"]),
        ("requirement_evidence", "ix_requirement_evidence_captured_at", ["captured_at"]),
        ("requirement_evidence", "ix_requirement_evidence_crawled_at", ["crawled_at"]),
        ("requirement_evidence", "ix_requirement_evidence_content_hash", ["content_hash"]),
        ("requirement_version", "ix_requirement_version_version_no", ["version_no"]),
        ("requirement_version", "ix_requirement_version_effective_at", ["effective_at"]),
        ("requirement_version", "ix_requirement_version_valid_from", ["valid_from"]),
        ("requirement_version", "ix_requirement_version_valid_to", ["valid_to"]),
        ("requirement_version", "ix_requirement_version_program_id", ["program_id"]),
    )
    for table_name, index_name, columns in index_specs:
        if _table_exists(inspector, table_name) and not _index_exists(inspector, table_name, index_name):
            op.create_index(index_name, table_name, columns, unique=False)

    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "program_requirement"):
        return

    for col_name, col_type in (
        ("version_id", sa.Integer()),
        ("subject_dim_id", sa.Integer()),
        ("exam_dim_id", sa.Integer()),
        ("framework_dim_id", sa.Integer()),
        ("evidence_id", sa.Integer()),
    ):
        if not _column_exists(inspector, "program_requirement", col_name):
            op.add_column("program_requirement", sa.Column(col_name, col_type, nullable=True))

    inspector = sa.inspect(bind)
    for index_name, column_name in (
        ("ix_program_requirement_version_id", "version_id"),
        ("ix_program_requirement_subject_dim_id", "subject_dim_id"),
        ("ix_program_requirement_exam_dim_id", "exam_dim_id"),
        ("ix_program_requirement_framework_dim_id", "framework_dim_id"),
        ("ix_program_requirement_evidence_id", "evidence_id"),
    ):
        if _table_exists(inspector, "program_requirement") and not _index_exists(
            inspector, "program_requirement", index_name
        ):
            op.create_index(index_name, "program_requirement", [column_name], unique=False)

    if not _foreign_key_exists(inspector, "program_requirement", "version_id", "requirement_version"):
        op.create_foreign_key(
            "fk_program_requirement_version_id",
            "program_requirement",
            "requirement_version",
            ["version_id"],
            ["id"],
        )
    if not _foreign_key_exists(inspector, "program_requirement", "subject_dim_id", "subject_dim"):
        op.create_foreign_key(
            "fk_program_requirement_subject_dim_id",
            "program_requirement",
            "subject_dim",
            ["subject_dim_id"],
            ["id"],
        )
    if not _foreign_key_exists(inspector, "program_requirement", "exam_dim_id", "exam_dim"):
        op.create_foreign_key(
            "fk_program_requirement_exam_dim_id",
            "program_requirement",
            "exam_dim",
            ["exam_dim_id"],
            ["id"],
        )
    if not _foreign_key_exists(inspector, "program_requirement", "framework_dim_id", "framework_dim"):
        op.create_foreign_key(
            "fk_program_requirement_framework_dim_id",
            "program_requirement",
            "framework_dim",
            ["framework_dim_id"],
            ["id"],
        )
    if not _foreign_key_exists(inspector, "program_requirement", "evidence_id", "requirement_evidence"):
        op.create_foreign_key(
            "fk_program_requirement_evidence_id",
            "program_requirement",
            "requirement_evidence",
            ["evidence_id"],
            ["id"],
        )

    inspector = sa.inspect(bind)
    if _unique_exists(inspector, "program_requirement", "uq_program_requirement_fingerprint"):
        op.drop_constraint(
            "uq_program_requirement_fingerprint",
            "program_requirement",
            type_="unique",
        )
    inspector = sa.inspect(bind)
    if not _unique_exists(inspector, "program_requirement", "uq_program_requirement_fingerprint"):
        op.create_unique_constraint(
            "uq_program_requirement_fingerprint",
            "program_requirement",
            [
                "version_id",
                "category",
                "subject_name",
                "framework",
                "minimum_value",
                "unit",
                "applicant_scope",
                "requirement_text",
            ],
        )

    subject_table = sa.table(
        "subject_dim",
        sa.column("id", sa.Integer()),
        sa.column("normalized_name", sa.String()),
        sa.column("canonical_name", sa.String()),
        sa.column("aliases", sa.JSON()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    exam_table = sa.table(
        "exam_dim",
        sa.column("id", sa.Integer()),
        sa.column("code", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("family", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    framework_table = sa.table(
        "framework_dim",
        sa.column("id", sa.Integer()),
        sa.column("code", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("region", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    evidence_table = sa.table(
        "requirement_evidence",
        sa.column("id", sa.Integer()),
        sa.column("source_url", sa.String()),
        sa.column("page_title", sa.String()),
        sa.column("page_snippet", sa.String()),
        sa.column("locator_type", sa.String()),
        sa.column("locator_value", sa.String()),
        sa.column("captured_at", sa.DateTime(timezone=True)),
        sa.column("crawled_at", sa.DateTime(timezone=True)),
        sa.column("content_hash", sa.String()),
    )
    version_table = sa.table(
        "requirement_version",
        sa.column("id", sa.Integer()),
        sa.column("version_no", sa.Integer()),
        sa.column("effective_at", sa.DateTime(timezone=True)),
        sa.column("valid_from", sa.DateTime(timezone=True)),
        sa.column("valid_to", sa.DateTime(timezone=True)),
        sa.column("change_summary", sa.String()),
        sa.column("diff_payload", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("program_id", sa.Integer()),
    )
    program_table = sa.table(
        "program",
        sa.column("id", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    requirement_table = sa.table(
        "program_requirement",
        sa.column("id", sa.Integer()),
        sa.column("program_id", sa.Integer()),
        sa.column("category", sa.String()),
        sa.column("subject_name", sa.String()),
        sa.column("framework", sa.String()),
        sa.column("minimum_value", sa.String()),
        sa.column("unit", sa.String()),
        sa.column("applicant_scope", sa.String()),
        sa.column("requirement_text", sa.String()),
        sa.column("evidence_url", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("version_id", sa.Integer()),
        sa.column("subject_dim_id", sa.Integer()),
        sa.column("exam_dim_id", sa.Integer()),
        sa.column("framework_dim_id", sa.Integer()),
        sa.column("evidence_id", sa.Integer()),
    )

    rows = bind.execute(
        sa.select(
            requirement_table.c.id,
            requirement_table.c.program_id,
            requirement_table.c.category,
            requirement_table.c.subject_name,
            requirement_table.c.framework,
            requirement_table.c.requirement_text,
            requirement_table.c.evidence_url,
            requirement_table.c.updated_at,
            requirement_table.c.version_id,
            requirement_table.c.subject_dim_id,
            requirement_table.c.exam_dim_id,
            requirement_table.c.framework_dim_id,
            requirement_table.c.evidence_id,
        )
    ).mappings()

    version_cache: dict[int, int] = {}
    subject_cache: dict[str, int] = {}
    exam_cache: dict[str, int] = {}
    framework_cache: dict[str, int] = {}
    evidence_cache: dict[str, int] = {}

    now = datetime.now(timezone.utc)
    baseline_diff = _parse_json_value(
        '{"event":"baseline_migration","source_revision":"20260302_0002"}',
        {},
    )

    def get_or_create_subject(name: Any) -> int | None:
        if not name:
            return None
        canonical = str(name).strip()
        if not canonical:
            return None
        normalized = _normalize_key(canonical, "subject")
        cached = subject_cache.get(normalized)
        if cached is not None:
            return cached
        existing = bind.execute(
            sa.select(subject_table.c.id).where(subject_table.c.normalized_name == normalized)
        ).scalar_one_or_none()
        if existing is not None:
            subject_cache[normalized] = int(existing)
            return int(existing)
        result = bind.execute(
            sa.insert(subject_table).values(
                normalized_name=normalized,
                canonical_name=canonical,
                aliases=[],
                updated_at=now,
            )
        )
        if result.inserted_primary_key:
            subject_id = int(result.inserted_primary_key[0])
        else:
            subject_id = bind.execute(
                sa.select(subject_table.c.id).where(subject_table.c.normalized_name == normalized)
            ).scalar_one()
        subject_cache[normalized] = int(subject_id)
        return int(subject_id)

    def get_or_create_framework(name: Any) -> int | None:
        if not name:
            return None
        display_name = str(name).strip()
        if not display_name:
            return None
        code = _normalize_key(display_name, "framework")
        cached = framework_cache.get(code)
        if cached is not None:
            return cached
        existing = bind.execute(
            sa.select(framework_table.c.id).where(framework_table.c.code == code)
        ).scalar_one_or_none()
        if existing is not None:
            framework_cache[code] = int(existing)
            return int(existing)
        result = bind.execute(
            sa.insert(framework_table).values(
                code=code,
                display_name=display_name,
                region=None,
                updated_at=now,
            )
        )
        if result.inserted_primary_key:
            framework_id = int(result.inserted_primary_key[0])
        else:
            framework_id = bind.execute(
                sa.select(framework_table.c.id).where(framework_table.c.code == code)
            ).scalar_one()
        framework_cache[code] = int(framework_id)
        return int(framework_id)

    def get_or_create_exam(category: Any, subject_name: Any, framework: Any, text_value: Any) -> int | None:
        inferred = _infer_exam(category, subject_name, framework, text_value)
        if inferred is None:
            return None
        code, display_name, family = inferred
        cached = exam_cache.get(code)
        if cached is not None:
            return cached
        existing = bind.execute(
            sa.select(exam_table.c.id).where(exam_table.c.code == code)
        ).scalar_one_or_none()
        if existing is not None:
            exam_cache[code] = int(existing)
            return int(existing)
        result = bind.execute(
            sa.insert(exam_table).values(
                code=code,
                display_name=display_name,
                family=family,
                updated_at=now,
            )
        )
        if result.inserted_primary_key:
            exam_id = int(result.inserted_primary_key[0])
        else:
            exam_id = bind.execute(
                sa.select(exam_table.c.id).where(exam_table.c.code == code)
            ).scalar_one()
        exam_cache[code] = int(exam_id)
        return int(exam_id)

    def get_or_create_evidence(url: Any, text_value: Any) -> int | None:
        source_url = str(url or "").strip() or None
        snippet = str(text_value or "").strip() or None
        if source_url is None and snippet is None:
            return None
        locator_type = "url" if source_url else "text"
        locator_value = source_url or (snippet[:128] if snippet else None)
        raw_key = f"{source_url or ''}|{snippet or ''}|{locator_value or ''}"
        content_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        cached = evidence_cache.get(content_hash)
        if cached is not None:
            return cached
        existing = bind.execute(
            sa.select(evidence_table.c.id).where(evidence_table.c.content_hash == content_hash)
        ).scalar_one_or_none()
        if existing is not None:
            evidence_cache[content_hash] = int(existing)
            return int(existing)
        result = bind.execute(
            sa.insert(evidence_table).values(
                source_url=source_url,
                page_title=None,
                page_snippet=snippet[:1000] if snippet else None,
                locator_type=locator_type,
                locator_value=locator_value,
                captured_at=now,
                crawled_at=now,
                content_hash=content_hash,
            )
        )
        if result.inserted_primary_key:
            evidence_id = int(result.inserted_primary_key[0])
        else:
            evidence_id = bind.execute(
                sa.select(evidence_table.c.id).where(evidence_table.c.content_hash == content_hash)
            ).scalar_one()
        evidence_cache[content_hash] = int(evidence_id)
        return int(evidence_id)

    def get_or_create_version(program_id: Any, updated_at: Any) -> int | None:
        if program_id is None:
            return None
        pid = int(program_id)
        cached = version_cache.get(pid)
        if cached is not None:
            return cached

        latest = bind.execute(
            sa.select(version_table.c.id)
            .where(version_table.c.program_id == pid)
            .order_by(version_table.c.version_no.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is not None:
            version_cache[pid] = int(latest)
            return int(latest)

        program_updated_at = bind.execute(
            sa.select(program_table.c.updated_at).where(program_table.c.id == pid)
        ).scalar_one_or_none()
        base_time = program_updated_at or updated_at or now
        result = bind.execute(
            sa.insert(version_table).values(
                program_id=pid,
                version_no=1,
                effective_at=base_time,
                valid_from=base_time,
                valid_to=None,
                change_summary="Baseline migrated from legacy requirement rows",
                diff_payload=baseline_diff,
                created_at=base_time,
            )
        )
        if result.inserted_primary_key:
            version_id = int(result.inserted_primary_key[0])
        else:
            version_id = bind.execute(
                sa.select(version_table.c.id)
                .where(version_table.c.program_id == pid, version_table.c.version_no == 1)
            ).scalar_one()
        version_cache[pid] = int(version_id)
        return int(version_id)

    for row in rows:
        subject_dim_id = row["subject_dim_id"] or get_or_create_subject(row["subject_name"])
        framework_dim_id = row["framework_dim_id"] or get_or_create_framework(row["framework"])
        exam_dim_id = row["exam_dim_id"] or get_or_create_exam(
            row["category"],
            row["subject_name"],
            row["framework"],
            row["requirement_text"],
        )
        evidence_id = row["evidence_id"] or get_or_create_evidence(
            row["evidence_url"],
            row["requirement_text"],
        )
        version_id = row["version_id"] or get_or_create_version(
            row["program_id"],
            row["updated_at"],
        )

        bind.execute(
            sa.update(requirement_table)
            .where(requirement_table.c.id == row["id"])
            .values(
                version_id=version_id,
                subject_dim_id=subject_dim_id,
                exam_dim_id=exam_dim_id,
                framework_dim_id=framework_dim_id,
                evidence_id=evidence_id,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "program_requirement"):
        if _unique_exists(inspector, "program_requirement", "uq_program_requirement_fingerprint"):
            op.drop_constraint(
                "uq_program_requirement_fingerprint",
                "program_requirement",
                type_="unique",
            )
        inspector = sa.inspect(bind)
        if not _unique_exists(inspector, "program_requirement", "uq_program_requirement_fingerprint"):
            op.create_unique_constraint(
                "uq_program_requirement_fingerprint",
                "program_requirement",
                [
                    "program_id",
                    "category",
                    "subject_name",
                    "framework",
                    "minimum_value",
                    "unit",
                    "applicant_scope",
                    "requirement_text",
                ],
            )

        inspector = sa.inspect(bind)
        for fk in inspector.get_foreign_keys("program_requirement"):
            local_columns = set(fk.get("constrained_columns") or [])
            referred_table = fk.get("referred_table")
            if (
                fk.get("name")
                and (
                    (local_columns == {"version_id"} and referred_table == "requirement_version")
                    or (local_columns == {"subject_dim_id"} and referred_table == "subject_dim")
                    or (local_columns == {"exam_dim_id"} and referred_table == "exam_dim")
                    or (local_columns == {"framework_dim_id"} and referred_table == "framework_dim")
                    or (local_columns == {"evidence_id"} and referred_table == "requirement_evidence")
                )
            ):
                op.drop_constraint(fk["name"], "program_requirement", type_="foreignkey")

        inspector = sa.inspect(bind)
        for index_name in (
            "ix_program_requirement_version_id",
            "ix_program_requirement_subject_dim_id",
            "ix_program_requirement_exam_dim_id",
            "ix_program_requirement_framework_dim_id",
            "ix_program_requirement_evidence_id",
        ):
            if _index_exists(inspector, "program_requirement", index_name):
                op.drop_index(index_name, table_name="program_requirement")

        inspector = sa.inspect(bind)
        for col_name in (
            "version_id",
            "subject_dim_id",
            "exam_dim_id",
            "framework_dim_id",
            "evidence_id",
        ):
            if _column_exists(inspector, "program_requirement", col_name):
                op.drop_column("program_requirement", col_name)

    inspector = sa.inspect(bind)
    for table_name, indexes in (
        (
            "requirement_version",
            (
                "ix_requirement_version_program_id",
                "ix_requirement_version_valid_to",
                "ix_requirement_version_valid_from",
                "ix_requirement_version_effective_at",
                "ix_requirement_version_version_no",
            ),
        ),
        (
            "requirement_evidence",
            (
                "ix_requirement_evidence_content_hash",
                "ix_requirement_evidence_crawled_at",
                "ix_requirement_evidence_captured_at",
                "ix_requirement_evidence_locator_type",
            ),
        ),
        (
            "framework_dim",
            (
                "ix_framework_dim_region",
                "ix_framework_dim_display_name",
                "ix_framework_dim_code",
            ),
        ),
        (
            "exam_dim",
            (
                "ix_exam_dim_family",
                "ix_exam_dim_display_name",
                "ix_exam_dim_code",
            ),
        ),
        (
            "subject_dim",
            (
                "ix_subject_dim_canonical_name",
                "ix_subject_dim_normalized_name",
            ),
        ),
    ):
        if not _table_exists(inspector, table_name):
            continue
        for index_name in indexes:
            if _index_exists(inspector, table_name, index_name):
                op.drop_index(index_name, table_name=table_name)
        op.drop_table(table_name)
        inspector = sa.inspect(bind)
