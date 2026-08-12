"""Replace obsolete uq_program_year with uq_program_version_year.

The initial schema (20260302_0001) created `program` with
UNIQUE(university_id, academic_year, name_en) as "uq_program_year". No
later migration ever dropped it, even though the current model
(`src/models/admission.py::Program.__table_args__`) declares a narrower
UNIQUE(program_catalog_id, academic_year) as "uq_program_version_year"
instead. That narrower constraint was never added by any migration either
— it only ever existed on databases bootstrapped by
`SQLModel.metadata.create_all()` before Alembic tracking was introduced
(create_all() creates a table's columns/constraints straight from the
current model definition in one shot; it does not alter an
already-existing table to reconcile a constraint mismatch).

Net effect: a database that has only ever run `create_all()` (e.g. an
existing installation from before Alembic was introduced) has
uq_program_version_year and not uq_program_year. A database migrated from
scratch by today's Alembic history has the reverse. This migration
reconciles both onto the one the current model actually declares, and is
defensive about which constraint(s) are present so it's safe to run
against either starting state.

Found via a real end-to-end export docs/superpowers/specs/2026-08-11-db-export-import-design.md
manual test that imported a live database's export into a from-scratch-
migrated Postgres target: the old uq_program_year rejected legitimate
rows (two different programs, different program_catalog_id, sharing the
same university/year/name — an already-known, accepted case) that the
current uq_program_version_year allows.

Revision ID: 20260812_0010
Revises: 20260522_0009
Create Date: 2026-08-12 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260812_0010"
down_revision = "20260522_0009"
branch_labels = None
depends_on = None

_OLD_NAME = "uq_program_year"
_OLD_COLUMNS = ("university_id", "academic_year", "name_en")
_NEW_NAME = "uq_program_version_year"
_NEW_COLUMNS = ("program_catalog_id", "academic_year")


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _unique_exists(inspector: sa.Inspector, table_name: str, uq_name: str) -> bool:
    return uq_name in {uq["name"] for uq in inspector.get_unique_constraints(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "program"):
        return

    if _unique_exists(inspector, "program", _OLD_NAME):
        op.drop_constraint(_OLD_NAME, "program", type_="unique")

    inspector = sa.inspect(bind)
    if not _unique_exists(inspector, "program", _NEW_NAME):
        op.create_unique_constraint(_NEW_NAME, "program", list(_NEW_COLUMNS))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "program"):
        return

    if _unique_exists(inspector, "program", _NEW_NAME):
        op.drop_constraint(_NEW_NAME, "program", type_="unique")

    inspector = sa.inspect(bind)
    if not _unique_exists(inspector, "program", _OLD_NAME):
        op.create_unique_constraint(_OLD_NAME, "program", list(_OLD_COLUMNS))
