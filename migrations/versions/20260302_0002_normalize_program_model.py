"""Normalize program data model and backfill from legacy JSON fields.

Revision ID: 20260302_0002
Revises: 20260302_0001
Create Date: 2026-03-02 15:10:00
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable

from alembic import op
import sqlalchemy as sa


revision = "20260302_0002"
down_revision = "20260302_0001"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def _unique_exists(inspector: sa.Inspector, table_name: str, uq_name: str) -> bool:
    return uq_name in {uq["name"] for uq in inspector.get_unique_constraints(table_name)}


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


def _catalog_key(group_code: Any, name_en: Any) -> str:
    if isinstance(group_code, str) and group_code.strip():
        return f"group:{group_code.strip().lower()}"
    name = str(name_en or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    if not normalized:
        normalized = "unnamed-program"
    return f"name:{normalized}"


def _normalize_study_mode(mode: Any) -> str:
    value = str(mode or "").strip().lower()
    if value in {"fulltime", "full_time", "full time", "ft"}:
        return "FullTime"
    if value in {"parttime", "part_time", "part time", "pt"}:
        return "PartTime"
    if value in {"hybrid", "mixed", "blended"}:
        return "Hybrid"
    return "Unknown"


def _parse_cutoff_date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iter_requirement_candidates(extra_metadata: dict[str, Any]) -> Iterable[dict[str, Any]]:
    explicit = extra_metadata.get("requirements")
    if isinstance(explicit, list):
        for item in explicit:
            if isinstance(item, dict):
                yield item

    keywords = (
        "requirement",
        "entry",
        "subject",
        "grade",
        "ielts",
        "toefl",
        "sat",
        "act",
        "gre",
        "gmat",
    )
    for key, value in extra_metadata.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        if not any(word in key_text.lower() for word in keywords):
            continue
        value_text = str(value).strip()
        if not value_text:
            continue
        yield {
            "category": "academic_subject",
            "subject_name": key_text,
            "requirement_text": value_text,
        }


def _normalize_requirement(item: dict[str, Any], sort_order: int) -> dict[str, Any]:
    category = str(item.get("category") or "other").strip().lower()
    if category not in {
        "academic_subject",
        "language",
        "standardized_test",
        "portfolio",
        "experience",
        "other",
    }:
        category = "other"

    text = str(item.get("requirement_text") or item.get("text") or "").strip()
    if not text:
        text = str(item.get("minimum_value") or "").strip()

    return {
        "category": category,
        "subject_name": str(item.get("subject_name") or item.get("subject") or "").strip() or None,
        "framework": str(item.get("framework") or "").strip() or None,
        "minimum_value": str(item.get("minimum_value") or item.get("score") or "").strip() or None,
        "unit": str(item.get("unit") or "").strip() or None,
        "applicant_scope": str(item.get("applicant_scope") or "all").strip() or "all",
        "requirement_text": text or "",
        "evidence_url": str(item.get("evidence_url") or "").strip() or None,
        "sort_order": sort_order,
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "program_catalog"):
        op.create_table(
            "program_catalog",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("catalog_key", sa.String(), nullable=False),
            sa.Column("program_group_code", sa.String(), nullable=True),
            sa.Column("canonical_name_en", sa.String(), nullable=True),
            sa.Column("canonical_name_zh", sa.String(), nullable=True),
            sa.Column("faculty", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("university_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["university_id"], ["university.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("university_id", "catalog_key", name="uq_program_catalog_key"),
        )
    inspector = sa.inspect(bind)
    if not _index_exists(inspector, "program_catalog", "ix_program_catalog_catalog_key"):
        op.create_index(
            "ix_program_catalog_catalog_key",
            "program_catalog",
            ["catalog_key"],
            unique=False,
        )
    if not _index_exists(inspector, "program_catalog", "ix_program_catalog_program_group_code"):
        op.create_index(
            "ix_program_catalog_program_group_code",
            "program_catalog",
            ["program_group_code"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "program") and not _column_exists(inspector, "program", "program_catalog_id"):
        op.add_column("program", sa.Column("program_catalog_id", sa.Integer(), nullable=True))
    if _table_exists(inspector, "program") and not _column_exists(inspector, "program", "source_url"):
        op.add_column("program", sa.Column("source_url", sa.String(), nullable=True))

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "program") and not _index_exists(inspector, "program", "ix_program_program_catalog_id"):
        op.create_index("ix_program_program_catalog_id", "program", ["program_catalog_id"], unique=False)

    if _table_exists(inspector, "program"):
        existing_fks = inspector.get_foreign_keys("program")
        fk_exists = any(
            fk.get("referred_table") == "program_catalog"
            and set(fk.get("constrained_columns") or []) == {"program_catalog_id"}
            for fk in existing_fks
        )
        if not fk_exists:
            op.create_foreign_key(
                "fk_program_program_catalog_id",
                "program",
                "program_catalog",
                ["program_catalog_id"],
                ["id"],
            )

    if not _table_exists(inspector, "program_study_option"):
        op.create_table(
            "program_study_option",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column(
                "mode",
                sa.Enum(
                    "FullTime",
                    "PartTime",
                    "Hybrid",
                    "Unknown",
                    name="studymode",
                ),
                nullable=False,
            ),
            sa.Column("duration_months", sa.Integer(), nullable=True),
            sa.Column("notes", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("program_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["program_id"], ["program.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "program_id",
                "mode",
                "duration_months",
                name="uq_program_study_option",
            ),
        )

    if not _table_exists(inspector, "program_deadline"):
        op.create_table(
            "program_deadline",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("round", sa.Integer(), nullable=True),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("cutoff_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("program_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["program_id"], ["program.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "program_id",
                "round",
                "description",
                "cutoff_date",
                name="uq_program_deadline",
            ),
        )

    if not _table_exists(inspector, "program_requirement"):
        op.create_table(
            "program_requirement",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column(
                "category",
                sa.Enum(
                    "academic_subject",
                    "language",
                    "standardized_test",
                    "portfolio",
                    "experience",
                    "other",
                    name="requirementcategory",
                ),
                nullable=False,
            ),
            sa.Column("subject_name", sa.String(), nullable=True),
            sa.Column("framework", sa.String(), nullable=True),
            sa.Column("minimum_value", sa.String(), nullable=True),
            sa.Column("unit", sa.String(), nullable=True),
            sa.Column("applicant_scope", sa.String(), nullable=False),
            sa.Column("requirement_text", sa.String(), nullable=False),
            sa.Column("evidence_url", sa.String(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("program_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["program_id"], ["program.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "program_id",
                "category",
                "subject_name",
                "framework",
                "minimum_value",
                "unit",
                "applicant_scope",
                "requirement_text",
                name="uq_program_requirement_fingerprint",
            ),
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "program_study_option") and not _index_exists(
        inspector, "program_study_option", "ix_program_study_option_program_id"
    ):
        op.create_index(
            "ix_program_study_option_program_id",
            "program_study_option",
            ["program_id"],
            unique=False,
        )
    if _table_exists(inspector, "program_deadline") and not _index_exists(
        inspector, "program_deadline", "ix_program_deadline_program_id"
    ):
        op.create_index(
            "ix_program_deadline_program_id",
            "program_deadline",
            ["program_id"],
            unique=False,
        )
    if _table_exists(inspector, "program_requirement") and not _index_exists(
        inspector, "program_requirement", "ix_program_requirement_program_id"
    ):
        op.create_index(
            "ix_program_requirement_program_id",
            "program_requirement",
            ["program_id"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "program"):
        return

    catalog_table = sa.table(
        "program_catalog",
        sa.column("id", sa.Integer()),
        sa.column("catalog_key", sa.String()),
        sa.column("program_group_code", sa.String()),
        sa.column("canonical_name_en", sa.String()),
        sa.column("canonical_name_zh", sa.String()),
        sa.column("faculty", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("university_id", sa.Integer()),
    )
    program_table = sa.table(
        "program",
        sa.column("id", sa.Integer()),
        sa.column("university_id", sa.Integer()),
        sa.column("program_group_code", sa.String()),
        sa.column("name_en", sa.String()),
        sa.column("name_zh", sa.String()),
        sa.column("faculty", sa.String()),
        sa.column("study_options", sa.JSON()),
        sa.column("deadlines", sa.JSON()),
        sa.column("extra_metadata", sa.JSON()),
        sa.column("source_url", sa.String()),
        sa.column("program_catalog_id", sa.Integer()),
    )
    study_option_table = sa.table(
        "program_study_option",
        sa.column("id", sa.Integer()),
        sa.column("mode", sa.String()),
        sa.column("duration_months", sa.Integer()),
        sa.column("notes", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("program_id", sa.Integer()),
    )
    deadline_table = sa.table(
        "program_deadline",
        sa.column("id", sa.Integer()),
        sa.column("round", sa.Integer()),
        sa.column("description", sa.String()),
        sa.column("cutoff_date", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("program_id", sa.Integer()),
    )
    requirement_table = sa.table(
        "program_requirement",
        sa.column("id", sa.Integer()),
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
        sa.column("program_id", sa.Integer()),
    )

    rows = bind.execute(
        sa.select(
            program_table.c.id,
            program_table.c.university_id,
            program_table.c.program_group_code,
            program_table.c.name_en,
            program_table.c.name_zh,
            program_table.c.faculty,
            program_table.c.study_options,
            program_table.c.deadlines,
            program_table.c.extra_metadata,
            program_table.c.source_url,
            program_table.c.program_catalog_id,
        ).where(program_table.c.university_id.is_not(None))
    ).mappings()

    cached_catalog_id: dict[tuple[int, str], int] = {}

    for row in rows:
        university_id = row["university_id"]
        if university_id is None:
            continue

        catalog_key = _catalog_key(row["program_group_code"], row["name_en"])
        lookup = (int(university_id), catalog_key)
        catalog_id = cached_catalog_id.get(lookup)

        if catalog_id is None:
            existing_catalog = bind.execute(
                sa.select(catalog_table.c.id).where(
                    catalog_table.c.university_id == university_id,
                    catalog_table.c.catalog_key == catalog_key,
                )
            ).scalar_one_or_none()

            if existing_catalog is None:
                insert_result = bind.execute(
                    sa.insert(catalog_table).values(
                        university_id=university_id,
                        catalog_key=catalog_key,
                        program_group_code=row["program_group_code"],
                        canonical_name_en=row["name_en"],
                        canonical_name_zh=row["name_zh"],
                        faculty=row["faculty"],
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                if insert_result.inserted_primary_key:
                    catalog_id = int(insert_result.inserted_primary_key[0])
                else:
                    catalog_id = bind.execute(
                        sa.select(catalog_table.c.id).where(
                            catalog_table.c.university_id == university_id,
                            catalog_table.c.catalog_key == catalog_key,
                        )
                    ).scalar_one()
            else:
                catalog_id = int(existing_catalog)

            cached_catalog_id[lookup] = catalog_id

        bind.execute(
            sa.update(program_table)
            .where(program_table.c.id == row["id"])
            .values(program_catalog_id=catalog_id)
        )

        extra_metadata = _parse_json_value(row["extra_metadata"], {})
        source_url = row["source_url"]
        if not source_url and isinstance(extra_metadata, dict):
            source_url = extra_metadata.get("source_url")
        if source_url:
            bind.execute(
                sa.update(program_table)
                .where(program_table.c.id == row["id"])
                .values(source_url=str(source_url))
            )

        existing_option_count = bind.execute(
            sa.select(sa.func.count()).select_from(study_option_table).where(
                study_option_table.c.program_id == row["id"]
            )
        ).scalar_one()
        if existing_option_count == 0:
            options = _parse_json_value(row["study_options"], [])
            if isinstance(options, list):
                for option in options:
                    if not isinstance(option, dict):
                        continue
                    bind.execute(
                        sa.insert(study_option_table).values(
                            program_id=row["id"],
                            mode=_normalize_study_mode(option.get("mode")),
                            duration_months=(
                                int(option["duration_months"])
                                if str(option.get("duration_months", "")).strip().isdigit()
                                else None
                            ),
                            notes=(
                                str(option.get("notes") or option.get("description") or "").strip()
                                or None
                            ),
                            updated_at=datetime.now(timezone.utc),
                        )
                    )

        existing_deadline_count = bind.execute(
            sa.select(sa.func.count()).select_from(deadline_table).where(
                deadline_table.c.program_id == row["id"]
            )
        ).scalar_one()
        if existing_deadline_count == 0:
            deadlines = _parse_json_value(row["deadlines"], [])
            if isinstance(deadlines, list):
                for deadline in deadlines:
                    if not isinstance(deadline, dict):
                        continue
                    bind.execute(
                        sa.insert(deadline_table).values(
                            program_id=row["id"],
                            round=(
                                int(deadline["round"])
                                if str(deadline.get("round", "")).strip().isdigit()
                                else None
                            ),
                            description=(
                                str(deadline.get("description") or "").strip() or None
                            ),
                            cutoff_date=_parse_cutoff_date(deadline.get("cutoff_date")),
                            updated_at=datetime.now(timezone.utc),
                        )
                    )

        existing_requirement_count = bind.execute(
            sa.select(sa.func.count()).select_from(requirement_table).where(
                requirement_table.c.program_id == row["id"]
            )
        ).scalar_one()
        if existing_requirement_count == 0 and isinstance(extra_metadata, dict):
            sort_order = 0
            for item in _iter_requirement_candidates(extra_metadata):
                normalized = _normalize_requirement(item, sort_order)
                if not normalized["requirement_text"]:
                    continue
                bind.execute(
                    sa.insert(requirement_table).values(
                        program_id=row["id"],
                        category=normalized["category"],
                        subject_name=normalized["subject_name"],
                        framework=normalized["framework"],
                        minimum_value=normalized["minimum_value"],
                        unit=normalized["unit"],
                        applicant_scope=normalized["applicant_scope"],
                        requirement_text=normalized["requirement_text"],
                        evidence_url=normalized["evidence_url"],
                        sort_order=normalized["sort_order"],
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                sort_order += 1


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "program"):
        if _column_exists(inspector, "program", "program_catalog_id"):
            for fk in inspector.get_foreign_keys("program"):
                if (
                    fk.get("referred_table") == "program_catalog"
                    and set(fk.get("constrained_columns") or []) == {"program_catalog_id"}
                    and fk.get("name")
                ):
                    op.drop_constraint(fk["name"], "program", type_="foreignkey")
            if _index_exists(inspector, "program", "ix_program_program_catalog_id"):
                op.drop_index("ix_program_program_catalog_id", table_name="program")
            op.drop_column("program", "program_catalog_id")

        if _column_exists(inspector, "program", "source_url"):
            op.drop_column("program", "source_url")

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "program_requirement"):
        if _index_exists(inspector, "program_requirement", "ix_program_requirement_program_id"):
            op.drop_index("ix_program_requirement_program_id", table_name="program_requirement")
        op.drop_table("program_requirement")
    if _table_exists(inspector, "program_deadline"):
        if _index_exists(inspector, "program_deadline", "ix_program_deadline_program_id"):
            op.drop_index("ix_program_deadline_program_id", table_name="program_deadline")
        op.drop_table("program_deadline")
    if _table_exists(inspector, "program_study_option"):
        if _index_exists(inspector, "program_study_option", "ix_program_study_option_program_id"):
            op.drop_index("ix_program_study_option_program_id", table_name="program_study_option")
        op.drop_table("program_study_option")
    if _table_exists(inspector, "program_catalog"):
        if _index_exists(inspector, "program_catalog", "ix_program_catalog_program_group_code"):
            op.drop_index("ix_program_catalog_program_group_code", table_name="program_catalog")
        if _index_exists(inspector, "program_catalog", "ix_program_catalog_catalog_key"):
            op.drop_index("ix_program_catalog_catalog_key", table_name="program_catalog")
        op.drop_table("program_catalog")
