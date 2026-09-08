# Tuition Fee Breakdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store every tuition figure a programme page publishes (per study mode, per applicant scope, per billing basis) in a filterable table, while `Program.tuition_amount` stays and is derived from those rows in code by a fixed priority.

**Architecture:** A new `program_tuition_fee` table (one row per fee statement, synced by key on re-crawl) sits beside `program_study_option`. The LLM copies every fee it sees into `ParsedProgramData.tuition_fees`; a pure function `derive_headline_tuition` picks the coarse value (non-local › all › local; full-time first; programme total, else per-annum × years, else per-credit × credits). The read side exposes the rows through `ProgramSummary` / `ProgramResponse`, adds `EXISTS`-based filters to `GET /programs` and the MCP `query` tool, exports a JSON-string column, and the popup shows a collapsible list.

**Tech Stack:** Python 3.12, SQLModel/SQLAlchemy, Alembic, Pydantic v2, FastAPI, pytest (in-memory SQLite), TypeScript/Vite popup.

**Spec:** `docs/superpowers/specs/2026-09-08-tuition-fee-breakdown-design.md`

## Global Constraints

- Both backends must work: SQLite (default) and Postgres (`DATABASE_URL`). Enums follow the `STUDY_MODE_ENUM` pattern (`SqlEnum(..., values_callable=_enum_values)`).
- `Program.tuition_amount` / `Program.currency` are kept; every one of their existing readers is untouched.
- No backfill of existing rows.
- Coarse-value priority: scope `non_local` → `all` → `local`; mode `FullTime` → `Unknown` → `PartTime` → `Hybrid`; basis `per_programme` → `per_annum × years (derived)` → `per_credit × credits (derived)`; `per_semester` never feeds the headline.
- Export: one JSON-string column `Tuition Fees (JSON)`; no per-dimension columns, no second sheet.
- CI lints every tracked `.py` file: run `uv run pylint $(git ls-files '*.py')` before each commit, not `pylint src/`.
- Run the full suite both normally and with the repo `.env` moved aside and `DATABASE_URL` unset (`mv .env /tmp/.env.bak; env -u DATABASE_URL uv run pytest -q; mv /tmp/.env.bak .env`) before the final commit — the CI has no `.env`.
- Commit after every task with the message given in the task.

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `src/models/admission.py` | `TuitionBasis`, `TuitionScope` enums (next to `StudyMode`); `Program.tuition_fee_records` relationship | 1 |
| `src/models/requirement.py` | `ProgramTuitionFee` table model, `TUITION_BASIS_ENUM`, `TUITION_SCOPE_ENUM` | 1 |
| `migrations/versions/20260908_0011_program_tuition_fee.py` | create table, constraints, indexes, enum types | 1 |
| `src/agents/tuition_headline.py` (new) | `normalize_applicant_scope`, `derive_headline_tuition`, `FeeRow` protocol | 2, 3 |
| `src/agents/cleaner_agent.py` | `ParsedTuitionFee`, `ParsedProgramData.tuition_fees`, dedup, headline population; delete `_reconcile_per_credit_tuition` | 4 |
| `src/agents/prompts/clean_chunk.txt` | tuition section: copy-all instructions | 4 |
| `src/storage/db_manager.py` | `_sync_tuition_fee_records`, call from `upsert_program`, cascade delete | 5 |
| `src/scrapers/page_processor.py`, `src/storage/importer.py` | put `tuition_fees` into `program_data` | 5 |
| `src/services/crawler.py` | `ProgramSummary.tuition_fees`, `query_programs` rows + filters | 6 |
| `src/api/schemas.py`, `src/api/server.py` | `ProgramResponse.tuition_fees`, `QueryRequest` filters, `GET /programs` params, MCP `query` params | 6 |
| `src/storage/exporter.py` | `Tuition Fees (JSON)` column | 7 |
| `frontend/src/shared/popup/types.ts`, `previewFlow.ts` | `tuition_fees` type; collapsible list on the card | 8 |
| `tests/test_tuition_fee_model.py`, `tests/test_tuition_headline.py`, `tests/test_tuition_fee_persistence.py`, `tests/test_tuition_fee_query.py` | new tests | 1–7 |

---

### Task 1: Table model, enums, migration

**Files:**
- Modify: `src/models/admission.py` (after `class StudyMode`, line 25; `Program` relationships at line 109–112)
- Modify: `src/models/requirement.py` (enum SqlEnums after line 37; new class after `ProgramDeadline`, line ~168)
- Create: `migrations/versions/20260908_0011_program_tuition_fee.py`
- Modify: `tests/test_db_portability.py:72` (comment says 17 tables → 18)
- Test: `tests/test_tuition_fee_model.py`

**Interfaces:**
- Produces: `src.models.admission.TuitionBasis` (`PER_PROGRAMME="per_programme"`, `PER_ANNUM="per_annum"`, `PER_SEMESTER="per_semester"`, `PER_CREDIT="per_credit"`), `src.models.admission.TuitionScope` (`ALL="all"`, `LOCAL="local"`, `NON_LOCAL="non_local"`), `src.models.requirement.ProgramTuitionFee` (columns as below), `Program.tuition_fee_records`.

- [ ] **Step 1: Write the failing model test**

```python
# tests/test_tuition_fee_model.py
"""program_tuition_fee: one row per fee statement, keyed by (mode, scope, basis)."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from src.models.admission import (
    CurrencyCode, Program, ProgramCatalog, StudyMode, TuitionBasis, TuitionScope, University,
)
from src.models.requirement import ProgramTuitionFee
from src.storage.db_manager import _attach_sqlite_pragmas


def _engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _attach_sqlite_pragmas(engine)
    SQLModel.metadata.create_all(engine)
    return engine


def _program(session: Session) -> Program:
    uni = University(name="CUHK", slug="cuhk")
    session.add(uni); session.flush()
    catalog = ProgramCatalog(university_id=uni.id, name_en="MA in Anthropology", catalog_key="url:x")
    session.add(catalog); session.flush()
    prog = Program(name_en="MA in Anthropology", academic_year=2027,
                   university_id=uni.id, program_catalog_id=catalog.id)
    session.add(prog); session.flush()
    return prog


def test_a_fee_row_round_trips_with_all_dimensions() -> None:
    with Session(_engine()) as s:
        prog = _program(s)
        s.add(ProgramTuitionFee(
            program_id=prog.id, amount=Decimal("198000.00"), currency=CurrencyCode.HKD,
            basis=TuitionBasis.PER_ANNUM, study_mode=StudyMode.FULL_TIME,
            applicant_scope=TuitionScope.ALL, scope_label=None, credits=None,
            is_derived=False, source_text="HK$198,000 per annum",
        ))
        s.commit()
        row = s.exec(select(ProgramTuitionFee)).one()
        assert row.amount == Decimal("198000.00")
        assert row.basis is TuitionBasis.PER_ANNUM
        assert row.study_mode is StudyMode.FULL_TIME
        assert row.applicant_scope is TuitionScope.ALL
        assert row.is_derived is False


def test_the_sync_key_is_unique_per_program() -> None:
    with Session(_engine()) as s:
        prog = _program(s)
        for amount in ("1", "2"):
            s.add(ProgramTuitionFee(
                program_id=prog.id, amount=Decimal(amount), currency=CurrencyCode.HKD,
                basis=TuitionBasis.PER_PROGRAMME, study_mode=StudyMode.FULL_TIME,
                applicant_scope=TuitionScope.NON_LOCAL,
            ))
        with pytest.raises(IntegrityError):
            s.commit()


def test_program_exposes_its_fee_rows() -> None:
    with Session(_engine()) as s:
        prog = _program(s)
        s.add(ProgramTuitionFee(program_id=prog.id, amount=Decimal("1"), currency=CurrencyCode.HKD,
                                basis=TuitionBasis.PER_PROGRAMME))
        s.commit(); s.refresh(prog)
        assert len(prog.tuition_fee_records) == 1


def test_defaults_are_all_and_unknown_mode() -> None:
    row = ProgramTuitionFee(program_id=1, amount=Decimal("1"), currency=CurrencyCode.HKD,
                            basis=TuitionBasis.PER_PROGRAMME)
    assert row.study_mode is StudyMode.UNKNOWN
    assert row.applicant_scope is TuitionScope.ALL
    assert row.is_derived is False
```

Check `ProgramCatalog`'s required fields first: `grep -n "class ProgramCatalog" -A 20 src/models/admission.py`. If `catalog_key` is not a column, construct with whatever non-null columns it has (mirror `tests/test_db_manager.py::TestProgramDeleteScope._seed`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tuition_fee_model.py -q`
Expected: `ImportError: cannot import name 'TuitionBasis'`

- [ ] **Step 3: Add the enums to `src/models/admission.py`**

Directly after `class StudyMode` (line 25):

```python
class TuitionBasis(str, Enum):
    """What one tuition figure is charged per."""
    PER_PROGRAMME = "per_programme"
    PER_ANNUM = "per_annum"
    PER_SEMESTER = "per_semester"
    PER_CREDIT = "per_credit"


class TuitionScope(str, Enum):
    """Which applicants one tuition figure applies to. Three values so it can
    be filtered; the page's own wording is kept separately in scope_label."""
    ALL = "all"
    LOCAL = "local"
    NON_LOCAL = "non_local"
```

In `class Program`, next to line 109–110:

```python
    tuition_fee_records: List["ProgramTuitionFee"] = Relationship(back_populates="program")
```

Add `"ProgramTuitionFee"` to whatever forward-reference/TYPE_CHECKING import block the file uses for `ProgramStudyOption` (grep `ProgramStudyOption` in `admission.py` and mirror it).

- [ ] **Step 4: Add the SqlEnums and the table to `src/models/requirement.py`**

Change the import on line 8 to `from src.models.admission import StudyMode, TuitionBasis, TuitionScope, CurrencyCode` (check whether `CurrencyCode` is already imported elsewhere in the file; import once). After `REQUIREMENT_CATEGORY_ENUM` (line 37):

```python
TUITION_BASIS_ENUM = SqlEnum(TuitionBasis, name="tuitionbasis", values_callable=_enum_values)
TUITION_SCOPE_ENUM = SqlEnum(TuitionScope, name="tuitionscope", values_callable=_enum_values)
CURRENCY_CODE_ENUM = SqlEnum(CurrencyCode, name="currencycode", values_callable=_enum_values)
```

Check how `Program.currency` is stored today: `grep -n "currency" src/models/admission.py` and `grep -rn "currencycode" migrations/versions/*.py`. If Postgres already has an enum type named `currencycode`, reuse that exact name so the migration does not try to create it twice; if `currency` is a plain string column, drop `CURRENCY_CODE_ENUM` and store `currency` as `Field(sa_column=Column(String(8), nullable=False))` with a `CurrencyCode` Python type. Record which one you did in the commit message.

After `class ProgramDeadline` (ends ~line 168):

```python
class ProgramTuitionFee(SQLModel, table=True):
    """One tuition figure as the page states it.

    A programme page rarely publishes a single fee: CUHK prices by study mode,
    EdUHK and the UK universities by applicant scope, PolyU gives a programme
    total and a per-credit rate. Each such statement is one row here; the
    coarse Program.tuition_amount is derived from these rows in code
    (src/agents/tuition_headline.py). Synced by (mode, scope, basis) on
    re-crawl, like program_study_option — not versioned.
    """

    __tablename__ = "program_tuition_fee"
    __table_args__ = (
        UniqueConstraint(
            "program_id", "study_mode", "applicant_scope", "basis",
            name="uq_program_tuition_fee",
        ),
        Index(
            "ix_program_tuition_fee_filter",
            "applicant_scope", "study_mode", "basis", "amount",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    amount: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    currency: CurrencyCode = Field(sa_column=Column(CURRENCY_CODE_ENUM, nullable=False))
    basis: TuitionBasis = Field(sa_column=Column(TUITION_BASIS_ENUM, nullable=False, index=True))
    study_mode: StudyMode = Field(
        default=StudyMode.UNKNOWN,
        sa_column=Column(STUDY_MODE_ENUM, nullable=False, index=True),
    )
    applicant_scope: TuitionScope = Field(
        default=TuitionScope.ALL,
        sa_column=Column(TUITION_SCOPE_ENUM, nullable=False, index=True),
    )
    scope_label: Optional[str] = Field(default=None)
    credits: Optional[int] = Field(default=None)
    is_derived: bool = Field(default=False)
    source_text: Optional[str] = Field(default=None, max_length=300)
    updated_at: datetime = Field(default_factory=_utc_now)

    program_id: int = Field(foreign_key="program.id", index=True)
    program: "Program" = Relationship(back_populates="tuition_fee_records")
```

Add `Index`, `Numeric` to the `sqlalchemy` import and `from decimal import Decimal` at the top.

- [ ] **Step 5: Run the model test**

Run: `uv run pytest tests/test_tuition_fee_model.py -q`
Expected: 4 passed.

- [ ] **Step 6: Write the migration**

```python
# migrations/versions/20260908_0011_program_tuition_fee.py
"""Add program_tuition_fee: one row per tuition statement on a programme page.

Program.tuition_amount holds one number, but pages publish several — by study
mode (CUHK), by applicant scope (EdUHK, Leeds, Manchester, UCL), or a
programme total alongside a per-credit rate (PolyU). Everything the single
column could not hold was dropped at extraction time. This table keeps each
statement; the coarse column stays and is derived from these rows in code.

No backfill: an existing tuition_amount cannot be decomposed into basis and
scope. Rows appear on the next crawl.

Revision ID: 20260908_0011
Revises: 20260812_0010
Create Date: 2026-09-08 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0011"
down_revision = "20260812_0010"
branch_labels = None
depends_on = None

_BASIS = sa.Enum("per_programme", "per_annum", "per_semester", "per_credit", name="tuitionbasis")
_SCOPE = sa.Enum("all", "local", "non_local", name="tuitionscope")
# studymode already exists (20260302_0002); create_type=False stops Postgres
# from trying to create it again while still using it as the column type.
_MODE = sa.Enum("FullTime", "PartTime", "Hybrid", "Unknown", name="studymode", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    _BASIS.create(bind, checkfirst=True)
    _SCOPE.create(bind, checkfirst=True)
    op.create_table(
        "program_tuition_fee",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", <CURRENCY COLUMN TYPE — see Step 4 decision>, nullable=False),
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
```

Replace the `<CURRENCY COLUMN TYPE>` placeholder with the type decided in Step 4 (`sa.Enum(..., name="currencycode", create_type=False)` if the type already exists, `sa.Enum(..., name="currencycode")` created with `checkfirst=True` if it does not, or `sa.String(8)`). Index names must match what SQLModel generates for `index=True` columns so `repair --auto` sees no drift: check with the drift step below and rename if needed.

- [ ] **Step 7: Verify the migration on both backends**

```bash
rm -f /tmp/tf.db && DATABASE_URL=sqlite:////tmp/tf.db uv run python -m src.cmd.cli db-version
DATABASE_URL=sqlite:////tmp/tf.db uv run python -m src.cmd.cli repair --auto
```
Expected: version `20260908_0011`, repair reports nothing to do. Then against the local Postgres (the repo `.env` supplies `DATABASE_URL`): `uv run python -m src.cmd.cli db-version` → migration applied, then `uv run python -m src.cmd.cli repair --auto` → no drift. If drift is reported on index names, align the names in the migration with the reported ones.

- [ ] **Step 8: Update the portability comment and run the suite**

`tests/test_db_portability.py:72` and the `get_portable_tables` docstring in `src/storage/db_portability.py:36` say 17 tables; make both say 18. Run `uv run pytest -q` → all pass. Run `uv run pylint $(git ls-files '*.py')` → 10.00, no messages.

- [ ] **Step 9: Commit**

```bash
git add src/models/admission.py src/models/requirement.py migrations/versions/20260908_0011_program_tuition_fee.py tests/test_tuition_fee_model.py tests/test_db_portability.py src/storage/db_portability.py
git commit -m "feat(model): program_tuition_fee table — one row per fee statement, keyed by mode/scope/basis"
```

---

### Task 2: Applicant-scope normalisation

**Files:**
- Create: `src/agents/tuition_headline.py`
- Test: `tests/test_tuition_headline.py`

**Interfaces:**
- Produces: `normalize_applicant_scope(label: Optional[str]) -> TuitionScope`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tuition_headline.py
"""Scope normalisation and headline derivation for tuition rows.

The page's wording for who pays what varies (UK / Home / International /
Overseas / EU / 本地); the filterable column has three values. The mapping
lives here, not in the LLM.
"""

import pytest

from src.models.admission import TuitionScope
from src.agents.tuition_headline import normalize_applicant_scope


@pytest.mark.parametrize("label", [
    "Local", "local students", "Home", "UK", "UK/EU", "UK and EU", "Domestic", "本地", "本地学生",
])
def test_local_wordings(label: str) -> None:
    assert normalize_applicant_scope(label) is TuitionScope.LOCAL


@pytest.mark.parametrize("label", [
    "Non-local", "Non-local Students", "International", "International, including EU",
    "Overseas", "EU", "非本地", "國際學生",
])
def test_non_local_wordings(label: str) -> None:
    assert normalize_applicant_scope(label) is TuitionScope.NON_LOCAL


@pytest.mark.parametrize("label", [None, "", "  ", "for local and non-local students", "all applicants"])
def test_undistinguished_is_all(label) -> None:
    assert normalize_applicant_scope(label) is TuitionScope.ALL


def test_unknown_wording_maps_to_all_and_warns(caplog) -> None:
    with caplog.at_level("WARNING"):
        assert normalize_applicant_scope("Martian residents") is TuitionScope.ALL
    assert "Martian residents" in caplog.text


def test_uk_eu_before_brexit_is_local_but_bare_eu_is_non_local() -> None:
    """UK pages price EU with International today; the older 'UK/EU' pairing
    was one home band."""
    assert normalize_applicant_scope("UK/EU") is TuitionScope.LOCAL
    assert normalize_applicant_scope("EU") is TuitionScope.NON_LOCAL
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tuition_headline.py -q`
Expected: `ModuleNotFoundError: No module named 'src.agents.tuition_headline'`

- [ ] **Step 3: Implement**

```python
# src/agents/tuition_headline.py
"""Turn the tuition rows a page publishes into the one coarse number the rest
of the system reads.

Pages price tuition along axes the single Program.tuition_amount column cannot
hold — by study mode (CUHK), by applicant scope (EdUHK, Leeds, Manchester,
UCL), or as a programme total next to a per-credit rate (PolyU). The LLM now
copies every statement into ParsedProgramData.tuition_fees; this module
decides which one becomes the headline, by a fixed priority, in code.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional, Protocol, Sequence

from src.models.admission import CurrencyCode, StudyMode, TuitionBasis, TuitionScope

logger = logging.getLogger(__name__)

# Explicit "both" wordings win before any single-scope keyword is looked for:
# PolyU writes "for local and non-local students" under one figure.
_BOTH_RE = re.compile(r"\b(local|home|uk)\b.{0,12}\b(and|&|/)\b.{0,12}\b(non-?local|international|overseas)\b", re.I)
_NON_LOCAL_RE = re.compile(r"non-?local|international|overseas|\bEU\b|非本地|國際|国际", re.I)
_LOCAL_RE = re.compile(r"\blocal\b|\bhome\b|\bUK\b|domestic|本地", re.I)
_UK_EU_RE = re.compile(r"\bUK\s*(/|and|&)\s*EU\b", re.I)


def normalize_applicant_scope(label: Optional[str]) -> TuitionScope:
    """Map the page's applicant wording onto the three filterable values.

    Order matters: an explicit both-scopes phrase is ALL; the pre-Brexit
    "UK/EU" pairing is one home band (LOCAL); any non-local keyword is
    NON_LOCAL (bare "EU" included — UK pages now price EU with International);
    any local keyword is LOCAL. Anything else is ALL with a warning so the
    vocabulary can be extended.
    """
    text = " ".join(str(label or "").split())
    if not text:
        return TuitionScope.ALL
    if _BOTH_RE.search(text) or re.search(r"\ball\b", text, re.I):
        return TuitionScope.ALL
    if _UK_EU_RE.search(text):
        return TuitionScope.LOCAL
    if _NON_LOCAL_RE.search(text):
        return TuitionScope.NON_LOCAL
    if _LOCAL_RE.search(text):
        return TuitionScope.LOCAL
    logger.warning("Unrecognised tuition applicant wording %r — stored as scope=all", text)
    return TuitionScope.ALL
```

(`FeeRow`, `HeadlineResult` and `derive_headline_tuition` are added in Task 3; leave the imports of `math`, `dataclass`, `Protocol`, `Sequence`, `Iterable`, `Decimal`, `CurrencyCode`, `StudyMode`, `TuitionBasis` out until then, or pylint will flag them unused — add only what this step uses.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_tuition_headline.py -q`
Expected: all pass. Run `uv run pylint src/agents/tuition_headline.py tests/test_tuition_headline.py`.

- [ ] **Step 5: Commit**

```bash
git add src/agents/tuition_headline.py tests/test_tuition_headline.py
git commit -m "feat(tuition): normalise applicant wording onto all/local/non_local"
```

---

### Task 3: Headline derivation

**Files:**
- Modify: `src/agents/tuition_headline.py`
- Test: `tests/test_tuition_headline.py` (append)

**Interfaces:**
- Consumes: `TuitionScope`, `TuitionBasis`, `StudyMode`, `CurrencyCode`.
- Produces:
  ```python
  class FeeRow(Protocol):          # structural type; ParsedTuitionFee (Task 4) satisfies it
      amount: Decimal
      currency: CurrencyCode
      basis: TuitionBasis
      study_mode: StudyMode
      applicant_scope: TuitionScope
      credits: Optional[int]

  @dataclass(frozen=True)
  class DerivedFee:                # a computed row to add to the detail table
      amount: Decimal
      currency: CurrencyCode
      basis: TuitionBasis          # always PER_PROGRAMME
      study_mode: StudyMode
      applicant_scope: TuitionScope
      source_text: str             # e.g. "derived: 198000 per annum × 1 year"

  @dataclass(frozen=True)
  class HeadlineResult:
      amount: Optional[Decimal]
      currency: Optional[CurrencyCode]
      derived: tuple[DerivedFee, ...]   # empty when the headline came straight from a row

  def derive_headline_tuition(
      fees: Sequence[FeeRow],
      study_options: Sequence[tuple[StudyMode, Optional[int]]],   # (mode, duration_months)
  ) -> HeadlineResult
  ```

- [ ] **Step 1: Append the failing tests**

```python
# append to tests/test_tuition_headline.py
from decimal import Decimal
from dataclasses import dataclass
from typing import Optional

from src.models.admission import CurrencyCode, StudyMode, TuitionBasis
from src.agents.tuition_headline import derive_headline_tuition


@dataclass
class _Fee:
    amount: Decimal
    basis: TuitionBasis
    study_mode: StudyMode = StudyMode.UNKNOWN
    applicant_scope: TuitionScope = TuitionScope.ALL
    credits: Optional[int] = None
    currency: CurrencyCode = CurrencyCode.HKD


FT, PT, ANY = StudyMode.FULL_TIME, StudyMode.PART_TIME, StudyMode.UNKNOWN
ALL, LOCAL, NON_LOCAL = TuitionScope.ALL, TuitionScope.LOCAL, TuitionScope.NON_LOCAL
PROG, ANNUM, SEM, CREDIT = (TuitionBasis.PER_PROGRAMME, TuitionBasis.PER_ANNUM,
                            TuitionBasis.PER_SEMESTER, TuitionBasis.PER_CREDIT)


def _amt(result) -> Optional[int]:
    return int(result.amount) if result.amount is not None else None


# ── scope priority: non_local › all › local ───────────────────────────

def test_non_local_beats_local_when_both_present() -> None:            # EdUHK
    fees = [_Fee(Decimal(47000), ANNUM, ANY, LOCAL), _Fee(Decimal(198000), ANNUM, ANY, NON_LOCAL)]
    assert _amt(derive_headline_tuition(fees, [(FT, 12)])) == 198000


def test_all_is_used_when_no_non_local_row() -> None:                   # PolyU wording
    fees = [_Fee(Decimal(495000), PROG, ANY, ALL), _Fee(Decimal(1), PROG, ANY, LOCAL)]
    assert _amt(derive_headline_tuition(fees, [])) == 495000


def test_local_only_page_still_yields_a_headline() -> None:
    assert _amt(derive_headline_tuition([_Fee(Decimal(9790), ANNUM, ANY, LOCAL)], [])) == 9790


# ── mode priority: FullTime › Unknown › PartTime › Hybrid ─────────────

def test_full_time_beats_part_time() -> None:                            # CUHK
    fees = [_Fee(Decimal(198000), ANNUM, FT), _Fee(Decimal(99000), ANNUM, PT)]
    r = derive_headline_tuition(fees, [(FT, 12), (PT, 24)])
    assert _amt(r) == 198000


def test_undistinguished_mode_beats_part_time() -> None:
    fees = [_Fee(Decimal(300000), PROG, ANY), _Fee(Decimal(150000), PROG, PT)]
    assert _amt(derive_headline_tuition(fees, [])) == 300000


def test_part_time_only_page_yields_the_part_time_figure() -> None:
    assert _amt(derive_headline_tuition([_Fee(Decimal(99000), ANNUM, PT)], [(PT, 24)])) == 198000


# ── basis: programme total, else per annum × years, else per credit × credits ──

def test_programme_total_is_used_as_is_and_nothing_is_derived() -> None:  # HKBU
    r = derive_headline_tuition([_Fee(Decimal(180000), PROG)], [(FT, 12)])
    assert _amt(r) == 180000 and r.derived == ()


def test_programme_total_beats_a_per_credit_rate_on_the_same_page() -> None:  # PolyU
    fees = [_Fee(Decimal(495000), PROG), _Fee(Decimal(16500), CREDIT, credits=30)]
    assert _amt(derive_headline_tuition(fees, [])) == 495000


def test_per_annum_is_multiplied_by_whole_years_of_the_same_mode() -> None:   # Manchester 2yr PT
    r = derive_headline_tuition([_Fee(Decimal(15800), ANNUM, FT)], [(FT, 12), (PT, 24)])
    assert _amt(r) == 15800
    r = derive_headline_tuition([_Fee(Decimal(99000), ANNUM, PT)], [(FT, 12), (PT, 24)])
    assert _amt(r) == 198000
    assert len(r.derived) == 1 and r.derived[0].basis is PROG and r.derived[0].study_mode is PT
    assert "per annum" in r.derived[0].source_text


def test_per_annum_rounds_duration_up_to_whole_years() -> None:
    r = derive_headline_tuition([_Fee(Decimal(10000), ANNUM, FT)], [(FT, 18)])
    assert _amt(r) == 20000


def test_per_annum_with_undistinguished_mode_uses_the_full_time_duration() -> None:  # UCL
    r = derive_headline_tuition([_Fee(Decimal(39200), ANNUM, ANY, NON_LOCAL)], [(FT, 36)])
    assert _amt(r) == 117600


def test_per_annum_without_any_duration_is_used_unconverted() -> None:
    r = derive_headline_tuition([_Fee(Decimal(198000), ANNUM, FT)], [])
    assert _amt(r) == 198000 and r.derived == ()


def test_per_credit_times_credits_when_no_total() -> None:
    r = derive_headline_tuition([_Fee(Decimal(9500), CREDIT, credits=30)], [])
    assert _amt(r) == 285000
    assert r.derived[0].basis is PROG and "per credit" in r.derived[0].source_text


def test_per_credit_without_a_credit_count_is_skipped() -> None:
    assert derive_headline_tuition([_Fee(Decimal(9500), CREDIT)], []).amount is None


def test_per_semester_never_feeds_the_headline() -> None:
    assert derive_headline_tuition([_Fee(Decimal(50000), SEM)], [(FT, 12)]).amount is None


def test_no_rows_gives_no_headline() -> None:
    r = derive_headline_tuition([], [])
    assert r.amount is None and r.currency is None and r.derived == ()


def test_currency_travels_with_the_chosen_row() -> None:
    r = derive_headline_tuition([_Fee(Decimal(17500), PROG, ANY, NON_LOCAL, currency=CurrencyCode.GBP)], [])
    assert r.currency is CurrencyCode.GBP


# ── the nine golden pages, as rows a correct extraction would produce ──

GOLDEN = {
    "hkbu": ([_Fee(Decimal(180000), PROG)], [(FT, 12)], 180000),
    "cuhk": ([_Fee(Decimal(180000), ANNUM, FT), _Fee(Decimal(90000), ANNUM, PT)], [(FT, 12), (PT, 24)], 180000),
    "eduhk": ([_Fee(Decimal(47000), ANNUM, ANY, LOCAL), _Fee(Decimal(198000), ANNUM, ANY, NON_LOCAL)], [(FT, 12)], 198000),
    "leeds": ([_Fee(Decimal(17500), PROG, ANY, LOCAL, currency=CurrencyCode.GBP),
               _Fee(Decimal(33000), PROG, ANY, NON_LOCAL, currency=CurrencyCode.GBP)], [(FT, 12)], 33000),
    "manchester": ([_Fee(Decimal(15800), ANNUM, ANY, LOCAL, currency=CurrencyCode.GBP),
                    _Fee(Decimal(31000), ANNUM, ANY, NON_LOCAL, currency=CurrencyCode.GBP)], [(FT, 12)], 31000),
    "ucl": ([_Fee(Decimal(9790), ANNUM, ANY, LOCAL, currency=CurrencyCode.GBP),
             _Fee(Decimal(39200), ANNUM, ANY, NON_LOCAL, currency=CurrencyCode.GBP)], [(FT, 36)], 117600),
    "polyu": ([_Fee(Decimal(495000), PROG), _Fee(Decimal(16500), CREDIT, credits=30)], [(FT, 12), (PT, 24)], 495000),
    "cityu_link_only": ([], [(FT, 12)], None),
    "edinburgh_living_costs_excluded": ([], [(FT, 48)], None),
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_pages_produce_the_expected_headline(name: str) -> None:
    fees, options, expected = GOLDEN[name]
    assert _amt(derive_headline_tuition(fees, options)) == expected
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tuition_headline.py -q`
Expected: `ImportError: cannot import name 'derive_headline_tuition'`

- [ ] **Step 3: Implement**

Append to `src/agents/tuition_headline.py` (add the imports listed in the Task 3 interface block):

```python
_SCOPE_ORDER = (TuitionScope.NON_LOCAL, TuitionScope.ALL, TuitionScope.LOCAL)
_MODE_ORDER = (StudyMode.FULL_TIME, StudyMode.UNKNOWN, StudyMode.PART_TIME, StudyMode.HYBRID)


class FeeRow(Protocol):
    """What derive_headline_tuition needs from a fee; ParsedTuitionFee satisfies it."""
    amount: Decimal
    currency: CurrencyCode
    basis: TuitionBasis
    study_mode: StudyMode
    applicant_scope: TuitionScope
    credits: Optional[int]


@dataclass(frozen=True)
class DerivedFee:
    """A programme total computed from a per-annum or per-credit row. Written to
    the detail table with is_derived=True so the headline stays traceable."""
    amount: Decimal
    currency: CurrencyCode
    basis: TuitionBasis
    study_mode: StudyMode
    applicant_scope: TuitionScope
    source_text: str


@dataclass(frozen=True)
class HeadlineResult:
    amount: Optional[Decimal]
    currency: Optional[CurrencyCode]
    derived: tuple[DerivedFee, ...] = ()


_EMPTY = HeadlineResult(amount=None, currency=None)


def _years_for(mode: StudyMode, study_options: Sequence[tuple[StudyMode, Optional[int]]]) -> Optional[int]:
    """Whole years for *mode*; an undistinguished fee uses the full-time duration."""
    wanted = StudyMode.FULL_TIME if mode is StudyMode.UNKNOWN else mode
    for opt_mode, months in study_options:
        if opt_mode is wanted and months:
            return max(1, math.ceil(months / 12))
    return None


def _programme_total(fee: FeeRow, study_options) -> Optional[tuple[Decimal, Optional[DerivedFee]]]:
    """The programme-total reading of one fee row, deriving if the basis needs it."""
    if fee.basis is TuitionBasis.PER_PROGRAMME:
        return fee.amount, None
    if fee.basis is TuitionBasis.PER_ANNUM:
        years = _years_for(fee.study_mode, study_options)
        if years is None:
            return fee.amount, None          # no duration: the per-annum figure, unconverted
        if years == 1:
            return fee.amount, None
        total = fee.amount * years
        return total, DerivedFee(
            amount=total, currency=fee.currency, basis=TuitionBasis.PER_PROGRAMME,
            study_mode=fee.study_mode, applicant_scope=fee.applicant_scope,
            source_text=f"derived: {fee.amount} per annum × {years} years",
        )
    if fee.basis is TuitionBasis.PER_CREDIT:
        if not fee.credits:
            return None
        total = fee.amount * fee.credits
        return total, DerivedFee(
            amount=total, currency=fee.currency, basis=TuitionBasis.PER_PROGRAMME,
            study_mode=fee.study_mode, applicant_scope=fee.applicant_scope,
            source_text=f"derived: {fee.amount} per credit × {fee.credits} credits",
        )
    return None                                # per_semester never feeds the headline


def derive_headline_tuition(
    fees: Sequence[FeeRow],
    study_options: Sequence[tuple[StudyMode, Optional[int]]],
) -> HeadlineResult:
    """Pick the coarse tuition from the page's fee rows by fixed priority.

    Scope non_local › all › local (the product's users are non-local
    applicants); mode FullTime › Unknown › PartTime › Hybrid; basis: a stated
    programme total, else per-annum × whole years of the same mode, else
    per-credit × credits. Per-semester rows are never used. Within one
    (scope, mode) cell a stated total beats a derived one.
    """
    for scope in _SCOPE_ORDER:
        for mode in _MODE_ORDER:
            cell = [f for f in fees if f.applicant_scope is scope and f.study_mode is mode]
            if not cell:
                continue
            cell.sort(key=lambda f: (f.basis is not TuitionBasis.PER_PROGRAMME,
                                     f.basis is not TuitionBasis.PER_ANNUM))
            for fee in cell:
                reading = _programme_total(fee, study_options)
                if reading is None:
                    continue
                amount, derived = reading
                return HeadlineResult(amount=amount, currency=fee.currency,
                                      derived=(derived,) if derived else ())
    return _EMPTY
```

Note on `test_per_annum_is_multiplied_by_whole_years_of_the_same_mode`: the first assertion is a 1-year full-time fee, so no derived row is produced (`years == 1` returns the figure as-is). Only multi-year conversions produce a `DerivedFee`; the spec's "per_annum × years" with years = 1 is identity and would only duplicate the page's own row under a different basis.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_tuition_headline.py -q`
Expected: all pass (including the 9 golden cases). Run pylint on both files.

- [ ] **Step 5: Commit**

```bash
git add src/agents/tuition_headline.py tests/test_tuition_headline.py
git commit -m "feat(tuition): derive the headline figure from fee rows by fixed priority"
```

---

### Task 4: LLM schema, prompt, dedup, headline population

**Files:**
- Modify: `src/agents/cleaner_agent.py` (`ParsedTuition` at 41; `ParsedProgramData` at 105; `_normalize_parsed_data` 175–328; `clean_markdown` 502–531; delete `_PER_CREDIT_RE`/`_PER_PROGRAMME_RE`/`_CREDIT_COUNT_RE`/`_reconcile_per_credit_tuition` at 348–~400; `_merge_parsed_data` at 329)
- Modify: `src/agents/prompts/clean_chunk.txt` (item 2)
- Modify: `tests/test_cleaner_agent.py` (delete the `_reconcile_per_credit_tuition` block at 608–645 and its import at line 24)
- Test: `tests/test_cleaner_tuition_fees.py`

**Interfaces:**
- Produces: `ParsedTuitionFee` (fields: `amount: Decimal`, `currency: CurrencyCode`, `basis: TuitionBasis`, `study_mode: StudyMode = UNKNOWN`, `scope_label: Optional[str]`, `applicant_scope: TuitionScope` (computed from `scope_label`), `credits: Optional[int]`, `source_text: Optional[str]`, `is_derived: bool = False`); `ParsedProgramData.tuition_fees: List[ParsedTuitionFee]`. After `clean_markdown`, `parsed.tuition` is the derived headline and `parsed.tuition_fees` includes derived rows.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cleaner_tuition_fees.py
"""The cleaner copies every fee the page states and derives the headline in code."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from src.agents.cleaner_agent import (
    LLMCleanerAgent, ParsedProgramData, ParsedStudyOption, ParsedTuitionFee, _normalize_parsed_data,
)
from src.models.admission import CurrencyCode, StudyMode, TuitionBasis, TuitionScope


def _fee(amount, basis, mode=StudyMode.UNKNOWN, label=None, credits=None, text=None):
    return ParsedTuitionFee(amount=Decimal(amount), currency=CurrencyCode.HKD, basis=basis,
                            study_mode=mode, scope_label=label, credits=credits, source_text=text)


def test_scope_is_computed_from_the_label() -> None:
    assert _fee(1, TuitionBasis.PER_ANNUM, label="Non-local Students").applicant_scope is TuitionScope.NON_LOCAL
    assert _fee(1, TuitionBasis.PER_ANNUM, label="Local").applicant_scope is TuitionScope.LOCAL
    assert _fee(1, TuitionBasis.PER_ANNUM).applicant_scope is TuitionScope.ALL


def test_amount_accepts_the_same_shorthand_as_the_old_field() -> None:
    assert ParsedTuitionFee(amount="198k", currency="HKD", basis="per_annum").amount == Decimal(198000)


def test_llm_output_without_tuition_fees_parses_to_an_empty_list() -> None:
    assert ParsedProgramData.model_validate({"faculty": "Arts", "tuition_fees": None}).tuition_fees == []


def test_dedup_keeps_the_row_with_the_longer_source_text() -> None:
    short = _fee(198000, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME, text="HK$198,000")
    long_ = _fee(198000, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME, text="Tuition Fee HK$198,000 per annum (full-time)")
    out = _normalize_parsed_data(ParsedProgramData(tuition_fees=[short, long_]))
    assert len(out.tuition_fees) == 1 and out.tuition_fees[0].source_text == long_.source_text


def test_dedup_keeps_rows_that_differ_in_any_key_dimension() -> None:
    rows = [_fee(1, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME), _fee(2, TuitionBasis.PER_ANNUM, StudyMode.PART_TIME),
            _fee(3, TuitionBasis.PER_PROGRAMME, StudyMode.FULL_TIME), _fee(4, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME, label="Local")]
    assert len(_normalize_parsed_data(ParsedProgramData(tuition_fees=rows)).tuition_fees) == 4


def _agent_returning(parsed: ParsedProgramData) -> LLMCleanerAgent:
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    agent.router = MagicMock()
    with patch.object(LLMCleanerAgent, "_parse_single_pass", return_value=parsed):
        yield agent


def test_clean_markdown_sets_the_headline_from_the_rows_and_ignores_what_the_llm_put_in_tuition() -> None:
    llm = ParsedProgramData(
        tuition={"amount": 1, "currency": "HKD"},                 # stale guess from the model
        tuition_fees=[_fee(198000, TuitionBasis.PER_ANNUM, StudyMode.FULL_TIME),
                      _fee(99000, TuitionBasis.PER_ANNUM, StudyMode.PART_TIME)],
        study_options=[ParsedStudyOption(mode=StudyMode.FULL_TIME, duration_months=12),
                       ParsedStudyOption(mode=StudyMode.PART_TIME, duration_months=24)],
    )
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    with patch.object(LLMCleanerAgent, "_parse_single_pass", return_value=llm):
        out = agent.clean_markdown("short page", "https://x")
    assert out.tuition.amount == Decimal(198000)
    assert out.tuition.currency is CurrencyCode.HKD


def test_clean_markdown_appends_derived_rows_to_the_detail() -> None:
    llm = ParsedProgramData(
        tuition_fees=[_fee(9500, TuitionBasis.PER_CREDIT, credits=30, text="HK$9,500 per credit")],
    )
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    with patch.object(LLMCleanerAgent, "_parse_single_pass", return_value=llm):
        out = agent.clean_markdown("short page", "https://x")
    assert out.tuition.amount == Decimal(285000)
    derived = [f for f in out.tuition_fees if f.is_derived]
    assert len(derived) == 1 and derived[0].basis is TuitionBasis.PER_PROGRAMME
    assert derived[0].amount == Decimal(285000)


def test_clean_markdown_with_no_fee_rows_leaves_tuition_none() -> None:
    agent = LLMCleanerAgent.__new__(LLMCleanerAgent)
    with patch.object(LLMCleanerAgent, "_parse_single_pass", return_value=ParsedProgramData(faculty="Arts")):
        out = agent.clean_markdown("short page", "https://x")
    assert out.tuition is None and out.tuition_fees == []


def test_the_prompt_asks_for_every_fee_not_a_choice() -> None:
    from src.agents.cleaner_agent import _load_prompt
    text = _load_prompt("clean_chunk.txt")
    assert "one row per" in text.lower() or "one entry per" in text.lower()
    assert "extract the TOTAL" not in text
    assert "living" in text.lower() and "deposit" in text.lower()
```

Remove the unused `_agent_returning` helper before committing (it is shown to make the two patched tests readable; the inline `patch.object` is what the tests use).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cleaner_tuition_fees.py -q`
Expected: `ImportError: cannot import name 'ParsedTuitionFee'`

- [ ] **Step 3: Add `ParsedTuitionFee` and the list field**

In `src/agents/cleaner_agent.py`, after `class ParsedTuition` (its `_parse_amount` validator stays), add:

```python
class ParsedTuitionFee(BaseModel):
    """One tuition figure exactly as the page states it. The LLM copies; code
    normalises the applicant wording and derives the headline."""

    amount: Decimal = Field(..., description="Amount as a number, e.g. 198000")
    currency: CurrencyCode = Field(..., description="ISO code: HKD, GBP, USD, ...")
    basis: TuitionBasis = Field(..., description="per_programme | per_annum | per_semester | per_credit")
    study_mode: StudyMode = Field(default=StudyMode.UNKNOWN,
                                  description="FullTime / PartTime / Hybrid; Unknown if the page does not say")
    scope_label: Optional[str] = Field(default=None,
                                       description="The page's own wording for who pays this: 'Local', 'Non-local Students', 'International, including EU'. Null if not distinguished")
    applicant_scope: TuitionScope = Field(default=TuitionScope.ALL, exclude=True)
    credits: Optional[int] = Field(default=None, description="Credit count, per_credit rows only")
    source_text: Optional[str] = Field(default=None, max_length=300, description="The sentence on the page")
    is_derived: bool = Field(default=False, exclude=True)

    _parse_amount = field_validator("amount", mode="before")(ParsedTuition._parse_amount.__func__)

    @model_validator(mode="after")
    def _scope_from_label(self) -> "ParsedTuitionFee":
        if not self.is_derived:
            self.applicant_scope = normalize_applicant_scope(self.scope_label)
        return self
```

Import `TuitionBasis`, `TuitionScope` from `src.models.admission` and `normalize_applicant_scope`, `derive_headline_tuition`, `DerivedFee` from `src.agents.tuition_headline`; add `model_validator` to the pydantic import. `exclude=True` keeps `applicant_scope` and `is_derived` out of the JSON schema shown to the LLM (`model_json_schema()` honours it via `json_schema_extra`; if it does not in this pydantic version, set `json_schema_extra={"exclude_from_llm": True}` and strip such properties in `_build_schema_prompt` — check how the schema is rendered into the system prompt first: `grep -n "model_json_schema" src/agents/cleaner_agent.py`).

If reusing `ParsedTuition._parse_amount.__func__` is awkward with pydantic's decorator wrapping, move the body into a module-level `def _coerce_amount(v: object) -> object` and reference it from both classes.

In `ParsedProgramData`, after `tuition`:

```python
    tuition_fees: List[ParsedTuitionFee] = Field(default_factory=list,
                                                 description="Every tuition figure stated on the page, one per statement")
```

and add `"tuition_fees"` to the `_none_to_list` validator's field list.

- [ ] **Step 4: Dedup in `_normalize_parsed_data`; carry the list through `_merge_parsed_data`**

In `_normalize_parsed_data`, after the study-options block:

```python
    # Tuition fees: key on (mode, scope, basis); keep the longer evidence.
    fees_by_key: dict = {}
    for fee in parsed.tuition_fees:
        key = (fee.study_mode, fee.applicant_scope, fee.basis)
        kept = fees_by_key.get(key)
        if kept is None or len(fee.source_text or "") > len(kept.source_text or ""):
            fees_by_key[key] = fee
```

and in the final `return ParsedProgramData(...)` add `tuition_fees=list(fees_by_key.values())`. In `_merge_parsed_data` (line 329) add `tuition_fees=list(existing.tuition_fees) + list(new.tuition_fees)` to the `combined` constructor.

- [ ] **Step 5: Populate the headline in `clean_markdown`; delete the regex reconcile**

Replace the body of `clean_markdown` after the parse branch (lines 526–531) with:

```python
        if parsed is None:
            return None
        parsed = _normalize_parsed_data(parsed)
        headline = derive_headline_tuition(
            parsed.tuition_fees,
            [(opt.mode, opt.duration_months) for opt in parsed.study_options],
        )
        for row in headline.derived:
            parsed.tuition_fees.append(ParsedTuitionFee(
                amount=row.amount, currency=row.currency, basis=row.basis,
                study_mode=row.study_mode, scope_label=None, credits=None,
                source_text=row.source_text, is_derived=True,
            ))
            parsed.tuition_fees[-1].applicant_scope = row.applicant_scope
        parsed.tuition = (
            ParsedTuition(amount=headline.amount, currency=headline.currency)
            if headline.amount is not None else None
        )
        return parsed
```

Delete `_PER_CREDIT_RE`, `_PER_PROGRAMME_RE`, `_CREDIT_COUNT_RE`, `_reconcile_per_credit_tuition` and their call; delete the test block `tests/test_cleaner_agent.py:608–645` and the import on line 24. Update the docstring of `_normalize_parsed_data` ("Preserves scalar fields (faculty, tuition) untouched" → tuition is now set by `clean_markdown` after normalisation).

- [ ] **Step 6: Rewrite the prompt's tuition item**

Replace item 2 in `src/agents/prompts/clean_chunk.txt` with:

```
2. **Tuition fees** (`tuition_fees`): copy EVERY tuition figure on the page, one entry per
   statement. Do not merge, choose between, or convert figures — code does that.
   For each entry give: amount (number), currency code, basis
   (per_programme | per_annum | per_semester | per_credit), the study mode it applies to
   (FullTime / PartTime / Hybrid, or Unknown if the page does not say), the page's own
   wording for who pays it in scope_label (e.g. "Local", "Non-local Students",
   "International, including EU"; null if not distinguished), credits for per_credit
   rows only, and the sentence it came from in source_text.
   - "HK$495,000 per programme (HK$16,500 per credit for 30 credits)" → TWO entries:
     495000 per_programme, and 16500 per_credit with credits=30.
   - "Full-time HK$198,000 per annum / Part-time HK$99,000 per annum" → two entries with
     study_mode FullTime and PartTime.
   - EXCLUDE living costs, accommodation, application fees, deposits, confirmation fees,
     credit-transfer fees and scholarship amounts. None of these is tuition.
   - If the page only links to a fee schedule and states no figure, return an empty list.
     Do not guess. Leave the old `tuition` field null; it is filled in by code.
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_cleaner_tuition_fees.py tests/test_cleaner_agent.py tests/test_tuition_headline.py -q` → all pass. Then the whole suite: `uv run pytest -q`. Any test asserting on the exact JSON schema string or the old prompt wording is updated to the new wording. Pylint on changed files.

- [ ] **Step 8: Commit**

```bash
git add src/agents/cleaner_agent.py src/agents/prompts/clean_chunk.txt tests/test_cleaner_tuition_fees.py tests/test_cleaner_agent.py
git commit -m "feat(extract): the LLM copies every tuition figure; the headline is derived in code"
```

---

### Task 5: Persistence

**Files:**
- Modify: `src/storage/db_manager.py` (`_sync_study_option_records` at 386 — add sibling after `_sync_deadline_records`; `upsert_program` child sync at 1233–1241; cascade delete at 824–833; imports at 18–34)
- Modify: `src/scrapers/page_processor.py:324–327`, `src/storage/importer.py:122–124`
- Test: `tests/test_tuition_fee_persistence.py`

**Interfaces:**
- Consumes: `ProgramTuitionFee`, `TuitionBasis`, `TuitionScope`, `ParsedTuitionFee`.
- Produces: `DatabaseManager._sync_tuition_fee_records(session, program_id, payload: list[dict])`; `upsert_program` consumes `program_data["tuition_fees"]` — a list of dicts with keys `amount, currency, basis, study_mode, applicant_scope, scope_label, credits, is_derived, source_text` (the `model_dump(mode="json")` of `ParsedTuitionFee` plus the two excluded fields added explicitly).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tuition_fee_persistence.py
"""Fee rows are synced by (mode, scope, basis) on re-crawl and removed with the programme."""

from decimal import Decimal

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from src.models.admission import University
from src.models.requirement import ProgramTuitionFee
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas


def _row(amount, basis="per_annum", mode="FullTime", scope="all", derived=False, text=None):
    return {"amount": amount, "currency": "HKD", "basis": basis, "study_mode": mode,
            "applicant_scope": scope, "scope_label": None, "credits": None,
            "is_derived": derived, "source_text": text}


class TestTuitionFeeSync:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        _attach_sqlite_pragmas(self.engine)
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager()
        self.dm.engine = self.engine
        with Session(self.engine) as s:
            s.add(University(name="CUHK", slug="cuhk")); s.commit()

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def _upsert(self, fees, **extra):
        data = {"name_en": "MA in Anthropology", "academic_year": 2027,
                "source_url": "https://gs.cuhk.edu.hk/programmes/arts/ma-anthropology",
                "tuition_fees": fees, **extra}
        program, _ = self.dm.upsert_program(data, "cuhk", enable_auto_translation=False)
        return program

    def _rows(self, program_id):
        with Session(self.engine) as s:
            return s.exec(select(ProgramTuitionFee).where(ProgramTuitionFee.program_id == program_id)
                          .order_by(ProgramTuitionFee.amount)).all()

    def test_rows_are_inserted(self) -> None:
        p = self._upsert([_row(198000, mode="FullTime"), _row(99000, mode="PartTime")])
        rows = self._rows(p.id)
        assert [int(r.amount) for r in rows] == [99000, 198000]
        assert {r.study_mode.value for r in rows} == {"FullTime", "PartTime"}

    def test_recrawl_updates_amount_on_the_same_key(self) -> None:
        p = self._upsert([_row(198000)])
        self._upsert([_row(205000, text="HK$205,000 per annum")])
        rows = self._rows(p.id)
        assert len(rows) == 1 and int(rows[0].amount) == 205000 and rows[0].source_text == "HK$205,000 per annum"

    def test_recrawl_deletes_rows_no_longer_on_the_page(self) -> None:
        p = self._upsert([_row(198000, mode="FullTime"), _row(99000, mode="PartTime")])
        self._upsert([_row(198000, mode="FullTime")])
        assert len(self._rows(p.id)) == 1

    def test_derived_flag_and_credits_round_trip(self) -> None:
        p = self._upsert([{**_row(285000, basis="per_programme", mode="Unknown", derived=True,
                                  text="derived: 9500 per credit × 30 credits")},
                         {**_row(9500, basis="per_credit", mode="Unknown"), "credits": 30}])
        rows = {r.basis.value: r for r in self._rows(p.id)}
        assert rows["per_programme"].is_derived is True
        assert rows["per_credit"].credits == 30

    def test_payload_without_tuition_fees_leaves_existing_rows_alone(self) -> None:
        p = self._upsert([_row(198000)])
        data = {"name_en": "MA in Anthropology", "academic_year": 2027,
                "source_url": "https://gs.cuhk.edu.hk/programmes/arts/ma-anthropology", "faculty": "Arts"}
        self.dm.upsert_program(data, "cuhk", enable_auto_translation=False)
        assert len(self._rows(p.id)) == 1

    def test_deleting_the_program_removes_its_rows(self) -> None:
        p = self._upsert([_row(198000)])
        self.dm.delete_program_snapshot(p.id)
        with Session(self.engine) as s:
            assert s.exec(select(ProgramTuitionFee)).all() == []
```

Confirm the delete method's name: `grep -n "def delete_program" src/storage/db_manager.py`. Use whichever method contains the cascade block at line 810–836.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tuition_fee_persistence.py -q`
Expected: `test_rows_are_inserted` fails — no rows (the key is silently ignored today).

- [ ] **Step 3: Implement the sync**

Add `ProgramTuitionFee` to the `src.models.requirement` import and `TuitionBasis, TuitionScope` to the `src.models.admission` import in `db_manager.py`. After `_sync_deadline_records`:

```python
    def _sync_tuition_fee_records(
        self,
        session: Session,
        program_id: int,
        payload: list[dict[str, Any]],
    ) -> None:
        """Make the fee rows match *payload* — insert, update, delete stale.

        Same shape as _sync_study_option_records: the page is the source of
        truth for the current academic year, so rows it no longer states go.
        """
        existing = session.exec(
            select(ProgramTuitionFee).where(ProgramTuitionFee.program_id == program_id)
        ).all()
        key_of = lambda row: (row.study_mode, row.applicant_scope, row.basis)  # noqa: E731
        existing_by_key: dict[tuple, ProgramTuitionFee] = {}
        for row in existing:
            if key_of(row) in existing_by_key:
                session.delete(row)
                continue
            existing_by_key[key_of(row)] = row

        payload_by_key: dict[tuple, dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict) or item.get("amount") in (None, ""):
                continue
            try:
                basis = TuitionBasis(str(item.get("basis") or "").strip().lower())
                scope = TuitionScope(str(item.get("applicant_scope") or "all").strip().lower())
            except ValueError:
                logger.warning("Skipping tuition row with unknown basis/scope: %r", item)
                continue
            key = (parse_study_mode(item.get("study_mode")), scope, basis)
            payload_by_key.setdefault(key, item)

        now = datetime.now(timezone.utc)
        for key, item in payload_by_key.items():
            mode, scope, basis = key
            try:
                currency = CurrencyCode(str(item.get("currency") or "").upper())
            except ValueError:
                logger.warning("Skipping tuition row with unknown currency: %r", item)
                continue
            fields = {
                "amount": Decimal(str(item["amount"])),
                "currency": currency,
                "scope_label": (str(item.get("scope_label") or "").strip() or None),
                "credits": int(item["credits"]) if str(item.get("credits") or "").isdigit() else None,
                "is_derived": bool(item.get("is_derived")),
                "source_text": (str(item.get("source_text") or "").strip()[:300] or None),
                "updated_at": now,
            }
            row = existing_by_key.get(key)
            if row is not None:
                for name, value in fields.items():
                    setattr(row, name, value)
                session.add(row)
                continue
            session.add(ProgramTuitionFee(program_id=program_id, study_mode=mode,
                                          applicant_scope=scope, basis=basis, **fields))

        for key, row in existing_by_key.items():
            if key not in payload_by_key:
                session.delete(row)
```

In `upsert_program` after the deadlines sync (line ~1241):

```python
            if "tuition_fees" in full_data:
                self._sync_tuition_fee_records(
                    session, program.id, full_data.get("tuition_fees") or []
                )
```

Check whether `upsert_program` commits after the child syncs (it does for study options — follow the same commit point). In the cascade-delete block after the deadline rows (line ~833):

```python
            fee_rows = session.exec(
                select(ProgramTuitionFee).where(ProgramTuitionFee.program_id == program.id)
            ).all()
            for row in fee_rows:
                session.delete(row)
```

Also add the same loop to the scoped batch delete at line ~920 (`delete_programs_by_scope`, where `ProgramStudyOption` rows are deleted with `in_(program_ids)`).

- [ ] **Step 4: Feed the payload from extraction and import**

`src/scrapers/page_processor.py`, after the `parsed.tuition` block (line 327):

```python
        if parsed.tuition_fees:
            program_data["tuition_fees"] = [
                {**fee.model_dump(mode="json"),
                 "applicant_scope": fee.applicant_scope.value,
                 "is_derived": fee.is_derived}
                for fee in parsed.tuition_fees
            ]
```

Same block in `src/storage/importer.py` after line 124 (and after line 374 in the LLM-fallback merge, guarded by `"tuition_fees" not in data and res.tuition_fees`). For the Excel `_tuition_raw` path (line 282–286), when `amount` is parsed add a single row so imported programmes also have detail:

```python
                data.setdefault("tuition_fees", [{
                    "amount": amount, "currency": currency, "basis": "per_programme",
                    "study_mode": "Unknown", "applicant_scope": "all", "scope_label": None,
                    "credits": None, "is_derived": False, "source_text": str(data["_tuition_raw"])[:300],
                }])
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tuition_fee_persistence.py tests/test_db_manager.py tests/test_schema_upsert.py tests/test_ingestion_pipeline.py -q` → pass. Whole suite, pylint.

- [ ] **Step 6: Commit**

```bash
git add src/storage/db_manager.py src/scrapers/page_processor.py src/storage/importer.py tests/test_tuition_fee_persistence.py
git commit -m "feat(storage): sync program_tuition_fee rows on upsert; cascade on delete"
```

---

### Task 6: Read side — summaries, API filters, MCP query

**Files:**
- Modify: `src/services/crawler.py` (`ProgramSummary` at 127; `query_programs` at 1003–1140)
- Modify: `src/api/schemas.py` (`QueryRequest` at 328; `ProgramResponse` at 523)
- Modify: `src/api/server.py` (`api_programs` at 1503; MCP `query` tool at ~2560)
- Test: `tests/test_tuition_fee_query.py`

**Interfaces:**
- Produces: `query_programs(univ_slug, year=None, *, tuition_scope: Optional[str]=None, tuition_study_mode: Optional[str]=None, tuition_basis: Optional[str]=None, tuition_max: Optional[float]=None)`; `ProgramSummary.tuition_fees: list[dict]`; `ProgramResponse.tuition_fees: list`. Filter semantics: keep a programme iff **one row** satisfies every given condition (`basis` defaults to `per_programme` when any filter is given).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tuition_fee_query.py
"""Programmes list their fee rows, and can be filtered by one row that matches."""

from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from src.models.admission import University
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas
from src.services.crawler import query_programs


def _row(amount, basis="per_programme", mode="FullTime", scope="non_local"):
    return {"amount": amount, "currency": "HKD", "basis": basis, "study_mode": mode,
            "applicant_scope": scope, "scope_label": None, "credits": None, "is_derived": False,
            "source_text": None}


class TestQuery:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        _attach_sqlite_pragmas(self.engine)
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager(); self.dm.engine = self.engine
        with Session(self.engine) as s:
            s.add(University(name="X", slug="x")); s.commit()
        self._add("Cheap FT",  [_row(150000), _row(300000, mode="PartTime")])
        self._add("Pricey FT", [_row(350000)])
        self._add("Local only", [_row(90000, scope="local")])
        self._add("No fees", [])

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def _add(self, name, fees):
        self.dm.upsert_program({"name_en": name, "academic_year": 2027,
                                "source_url": f"https://x.edu/{name.replace(' ', '-')}",
                                "tuition_fees": fees}, "x", enable_auto_translation=False)

    def test_summaries_carry_the_rows(self) -> None:
        by_name = {p.name_en: p for p in query_programs("x", 2027)}
        assert len(by_name["Cheap FT"].tuition_fees) == 2
        assert by_name["Cheap FT"].tuition_fees[0]["basis"] == "per_programme"
        assert by_name["No fees"].tuition_fees == []

    def test_no_filter_returns_everything(self) -> None:
        assert len(query_programs("x", 2027)) == 4

    def test_tuition_max_keeps_programmes_with_one_matching_row(self) -> None:
        names = {p.name_en for p in query_programs("x", 2027, tuition_max=200000)}
        assert names == {"Cheap FT", "Local only"}

    def test_conditions_must_hold_on_the_same_row(self) -> None:
        """Cheap FT has a non_local row under 200k (FullTime) and a PartTime row
        over it — asking for PartTime under 200k must exclude it."""
        names = {p.name_en for p in query_programs("x", 2027, tuition_study_mode="PartTime", tuition_max=200000)}
        assert names == set()

    def test_scope_filter(self) -> None:
        names = {p.name_en for p in query_programs("x", 2027, tuition_scope="local")}
        assert names == {"Local only"}

    def test_tuition_max_is_inclusive(self) -> None:
        names = {p.name_en for p in query_programs("x", 2027, tuition_max=150000)}
        assert "Cheap FT" in names


def test_get_programs_forwards_the_filters() -> None:
    from src.api import server as server_mod
    with patch.object(server_mod, "query_programs", return_value=[]) as spy:
        resp = TestClient(server_mod.app).get(
            "/programs", params={"univ_slug": "x", "year": 2027, "tuition_scope": "non_local",
                                 "tuition_study_mode": "FullTime", "tuition_max": 200000})
    assert resp.status_code == 200
    assert spy.call_args.kwargs["tuition_scope"] == "non_local"
    assert spy.call_args.kwargs["tuition_study_mode"] == "FullTime"
    assert spy.call_args.kwargs["tuition_max"] == 200000


def test_get_programs_rejects_an_unknown_scope() -> None:
    from src.api import server as server_mod
    resp = TestClient(server_mod.app).get("/programs", params={"univ_slug": "x", "tuition_scope": "martian"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tuition_fee_query.py -q`
Expected: `TypeError: query_programs() got an unexpected keyword argument 'tuition_max'` and `AttributeError: tuition_fees`.

- [ ] **Step 3: Extend `ProgramSummary` and `query_programs`**

In `src/services/crawler.py`, `ProgramSummary` gains `tuition_fees: list = Field(default_factory=list)`. Change the `query_programs` signature and the statement:

```python
def query_programs(
    univ_slug: str,
    year: Optional[int] = None,
    *,
    tuition_scope: Optional[str] = None,
    tuition_study_mode: Optional[str] = None,
    tuition_basis: Optional[str] = None,
    tuition_max: Optional[float] = None,
) -> List[ProgramSummary]:
```

After the `year` filter:

```python
        if any(v is not None for v in (tuition_scope, tuition_study_mode, tuition_basis, tuition_max)):
            fee = ProgramTuitionFee
            conditions = [fee.program_id == Program.id]
            basis = TuitionBasis((tuition_basis or "per_programme").strip().lower())
            conditions.append(fee.basis == basis)
            if tuition_scope is not None:
                conditions.append(fee.applicant_scope == TuitionScope(tuition_scope.strip().lower()))
            if tuition_study_mode is not None:
                conditions.append(fee.study_mode == parse_study_mode(tuition_study_mode))
            if tuition_max is not None:
                conditions.append(fee.amount <= Decimal(str(tuition_max)))
            stmt = stmt.where(select(fee.id).where(*conditions).exists())
```

Inside the per-programme loop, after `deadline_rows`:

```python
            fee_rows = session.exec(
                select(ProgramTuitionFee)
                .where(ProgramTuitionFee.program_id == program.id)
                .order_by(col(ProgramTuitionFee.applicant_scope), col(ProgramTuitionFee.study_mode),
                          col(ProgramTuitionFee.basis), col(ProgramTuitionFee.id))
            ).all()
            tuition_fees = [
                {
                    "amount": float(f.amount), "currency": f.currency.value if f.currency else None,
                    "basis": f.basis.value, "study_mode": f.study_mode.value,
                    "applicant_scope": f.applicant_scope.value, "scope_label": f.scope_label,
                    "credits": f.credits, "is_derived": f.is_derived, "source_text": f.source_text,
                }
                for f in fee_rows
            ]
```

and `tuition_fees=tuition_fees` in the `ProgramSummary(...)` constructor. Import `ProgramTuitionFee`, `TuitionBasis`, `TuitionScope`, `parse_study_mode`, `Decimal`.

- [ ] **Step 4: API and MCP**

`src/api/schemas.py`: `ProgramResponse` gains `tuition_fees: list = Field(default_factory=list)`; `QueryRequest` gains the four optional fields with descriptions (`tuition_scope: Optional[str]` — "all | local | non_local"; `tuition_study_mode: Optional[str]` — "FullTime | PartTime | Hybrid | Unknown"; `tuition_basis: Optional[str]` — "per_programme (default) | per_annum | per_semester | per_credit"; `tuition_max: Optional[float]` — "inclusive upper bound on one fee row").

`src/api/server.py` `api_programs`:

```python
@app.get("/programs", response_model=List[ProgramResponse])
async def api_programs(
    univ_slug: str = Query(..., description="University slug"),
    year: Optional[int] = Query(None, description="Academic year filter"),
    tuition_scope: Optional[Literal["all", "local", "non_local"]] = Query(None),
    tuition_study_mode: Optional[Literal["FullTime", "PartTime", "Hybrid", "Unknown"]] = Query(None),
    tuition_basis: Optional[Literal["per_programme", "per_annum", "per_semester", "per_credit"]] = Query(None),
    tuition_max: Optional[float] = Query(None, ge=0, description="Keep programmes with one fee row at or below this"),
) -> List[ProgramResponse]:
    """Query programs for a university. Tuition filters must all hold on the same fee row."""
    programs = query_programs(univ_slug=univ_slug, year=year, tuition_scope=tuition_scope,
                              tuition_study_mode=tuition_study_mode, tuition_basis=tuition_basis,
                              tuition_max=tuition_max)
    return [ProgramResponse(**p.model_dump()) for p in programs]
```

MCP `query` tool (line ~2560): add the same four optional parameters with the same docstring lines and pass them through to `query_programs`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tuition_fee_query.py -q` → pass; whole suite; pylint. `tests/test_tool_trimming.py` or any test that asserts the exact MCP tool signature list may need the new parameters added.

- [ ] **Step 6: Commit**

```bash
git add src/services/crawler.py src/api/schemas.py src/api/server.py tests/test_tuition_fee_query.py
git commit -m "feat(api): expose tuition fee rows and filter programmes by one matching row"
```

---

### Task 7: Export column

**Files:**
- Modify: `src/storage/exporter.py` (child loading at 112–122; row dict at 203–215)
- Test: `tests/test_exporter_tuition_fees.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exporter_tuition_fees.py
"""Export keeps Tuition/Currency and adds one JSON-string column of fee rows."""

import io
import json

import pandas as pd
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from src.models.admission import University
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas
from src.storage.exporter import ExcelExporter


def test_fee_rows_are_exported_as_a_json_string_column() -> None:
    DatabaseManager._instance = None
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _attach_sqlite_pragmas(engine); SQLModel.metadata.create_all(engine)
    dm = DatabaseManager(); dm.engine = engine
    with Session(engine) as s:
        s.add(University(name="CUHK", slug="cuhk")); s.commit()
    dm.upsert_program({
        "name_en": "MA in Anthropology", "academic_year": 2027, "source_url": "https://x/a",
        "tuition_amount": 198000, "currency": "HKD",
        "tuition_fees": [
            {"amount": 198000, "currency": "HKD", "basis": "per_annum", "study_mode": "FullTime",
             "applicant_scope": "all", "scope_label": None, "credits": None, "is_derived": False,
             "source_text": "HK$198,000 per annum"},
            {"amount": 99000, "currency": "HKD", "basis": "per_annum", "study_mode": "PartTime",
             "applicant_scope": "all", "scope_label": None, "credits": None, "is_derived": False,
             "source_text": None},
        ]}, "cuhk", enable_auto_translation=False)

    buf = io.BytesIO()
    assert ExcelExporter(output_stream=buf).export_data("cuhk", 2027) == 1
    buf.seek(0)
    df = pd.read_excel(buf)
    assert df.loc[0, "Tuition"] == 198000
    rows = json.loads(df.loc[0, "Tuition Fees (JSON)"])
    assert {r["study_mode"] for r in rows} == {"FullTime", "PartTime"}
    assert rows[0]["basis"] == "per_annum"
    DatabaseManager._instance = None
```

Check the exporter's constructor: `grep -n "def __init__" -A 6 src/storage/exporter.py`; pass the stream the way it expects.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_exporter_tuition_fees.py -q`
Expected: `KeyError: 'Tuition Fees (JSON)'`.

- [ ] **Step 3: Implement**

In `export_data`, after `deadline_rows` (line ~122):

```python
                fee_rows = session.exec(
                    select(ProgramTuitionFee)
                    .where(ProgramTuitionFee.program_id == p.id)
                    .order_by(col(ProgramTuitionFee.applicant_scope), col(ProgramTuitionFee.study_mode),
                              col(ProgramTuitionFee.basis), col(ProgramTuitionFee.id))
                ).all()
                tuition_fees_json = json.dumps(
                    [
                        {
                            "amount": float(f.amount), "currency": f.currency.value if f.currency else None,
                            "basis": f.basis.value, "study_mode": f.study_mode.value,
                            "applicant_scope": f.applicant_scope.value, "scope_label": f.scope_label,
                            "credits": f.credits, "is_derived": f.is_derived, "source_text": f.source_text,
                        }
                        for f in fee_rows
                    ],
                    ensure_ascii=False,
                )
```

and in the row dict, directly after `"Currency"`: `"Tuition Fees (JSON)": tuition_fees_json,`. Import `json` and `ProgramTuitionFee`. If the exporter also has a CSV path (`grep -n "to_csv" src/storage/exporter.py`), it writes the same `data_rows`, so the column is carried automatically.

- [ ] **Step 4: Run, lint, commit**

`uv run pytest tests/test_exporter_tuition_fees.py tests/test_exporter*.py -q`; pylint.

```bash
git add src/storage/exporter.py tests/test_exporter_tuition_fees.py
git commit -m "feat(export): Tuition Fees (JSON) column with every fee row"
```

---

### Task 8: Popup — show the rows

**Files:**
- Modify: `frontend/src/shared/popup/types.ts:97` (`ProgramRecord`)
- Modify: `frontend/src/shared/popup/previewFlow.ts:430–436` (card meta)
- Verify: `cd frontend && npm run build` (runs `tsc --noEmit` then Vite)

- [ ] **Step 1: Type**

In `ProgramRecord`, after `currency`:

```ts
    tuition_fees?: {
        amount: number;
        currency: string | null;
        basis: string;
        study_mode: string;
        applicant_scope: string;
        scope_label: string | null;
        credits: number | null;
        is_derived: boolean;
        source_text: string | null;
    }[];
```

- [ ] **Step 2: Render**

Directly after the `if (p.tuition_amount != null) { ... }` block, add:

```ts
            if (p.tuition_fees?.length) {
                const details = document.createElement("details");
                details.className = "program-fees";
                const summary = document.createElement("summary");
                summary.textContent = `${p.tuition_fees.length} fee line${p.tuition_fees.length > 1 ? "s" : ""}`;
                details.appendChild(summary);
                const list = document.createElement("ul");
                for (const fee of p.tuition_fees) {
                    const li = document.createElement("li");
                    const who = fee.scope_label ?? fee.applicant_scope;
                    const mode = fee.study_mode === "Unknown" ? "any mode" : fee.study_mode;
                    const basis = fee.basis.replace("per_", "per ");
                    const credits = fee.credits ? ` × ${fee.credits} credits` : "";
                    const derived = fee.is_derived ? " (derived)" : "";
                    li.textContent = `${who} · ${mode} · ${basis}${credits}: ${fee.currency ?? ""} ${fee.amount.toLocaleString()}${derived}`;
                    if (fee.source_text) li.title = fee.source_text;
                    list.appendChild(li);
                }
                details.appendChild(list);
                card.appendChild(details);
            }
```

Place the `details` element on `card` (not `meta`) so it sits under the chip row. Add minimal styling next to `.program-card-meta` in the popup stylesheet (`grep -rn "program-card-meta" frontend/src --include='*.css'`): `.program-fees { margin-top: 6px; font-size: 12px; } .program-fees ul { margin: 4px 0 0 14px; padding: 0; }`.

- [ ] **Step 3: Build**

```bash
cd frontend && npm run build
```
Expected: `tsc` clean, Vite bundle written to `frontend/dist`. Open `http://localhost:8910/ui/` → Preview Database → `cuhk` / `2027` after Task 9's crawl and confirm the collapsible list renders.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/shared/popup/types.ts frontend/src/shared/popup/previewFlow.ts frontend/src/shared/popup/*.css
git commit -m "feat(popup): collapsible tuition fee lines under the headline chip"
```

---

### Task 9: End-to-end acceptance

**Files:** none new; this task verifies and records.

- [ ] **Step 1: Migrations on both backends**

```bash
rm -f /tmp/tf.db && DATABASE_URL=sqlite:////tmp/tf.db uv run python -m src.cmd.cli db-version && DATABASE_URL=sqlite:////tmp/tf.db uv run python -m src.cmd.cli repair --auto
uv run python -m src.cmd.cli db-version && uv run python -m src.cmd.cli repair --auto
```
Expected: `20260908_0011` on both; no drift on either.

- [ ] **Step 2: CUHK — two rows by study mode**

```bash
uv run python -m src.cmd.cli crawl --name cuhk --year 2027 --url "https://www.gs.cuhk.edu.hk/programme-filter?programme_type=12&study_mode=All&keys=" --limit 1
```
Then check:

```bash
uv run python - <<'EOF'
import os
from src.storage.db_helpers import load_database_env; load_database_env()
from sqlalchemy import create_engine, text
e = create_engine(os.environ["DATABASE_URL"])
with e.connect() as c:
    for r in c.execute(text("""SELECT p.name_en, p.tuition_amount, f.study_mode, f.applicant_scope, f.basis, f.amount, f.is_derived
        FROM program p JOIN university u ON u.id=p.university_id LEFT JOIN program_tuition_fee f ON f.program_id=p.id
        WHERE u.slug='cuhk' AND p.academic_year=2027 AND p.name_en LIKE 'MA in Anthropology%' ORDER BY f.amount""")):
        print(r)
EOF
```
Expected: two rows — `FullTime / all / per_annum / 198000` and `PartTime / all / per_annum / 99000` — and `tuition_amount = 198000`. (The CUHK index is alphabetical; `--limit 1` is MA in Anthropology. If the first programme changes, use `--limit 5` and read the Anthropology row.)

- [ ] **Step 3: EdUHK — two rows by scope**

```bash
uv run python -m src.cmd.cli crawl --name eduhk --year 2027 --url "https://www.eduhk.hk/acadprog/postgrad/index.html" --limit 3
```
Run the same query with `u.slug='eduhk'` and no name filter. Expected for MA in Educational Psychology (or whichever of the three carries the Local/Non-local split): `all/Unknown-or-FullTime / local / per_annum / 47000` and `.../non_local/per_annum/198000`, headline `198000`. If EdUHK's index order does not include a split-price programme in the first three, raise `--limit` until one appears; record which programme was checked.

- [ ] **Step 4: Full CUHK taught index**

```bash
uv run python -m src.cmd.cli crawl --name cuhk --year 2027 --url "https://www.gs.cuhk.edu.hk/programme-filter?programme_type=12&study_mode=All&keys=" --all > /tmp/cuhk-full.log 2>&1
```
Expected on completion: `✅ Crawl complete: 138 programs imported`; then

```sql
SELECT count(*) FROM program p JOIN university u ON u.id=p.university_id WHERE u.slug='cuhk' AND p.academic_year=2027;                 -- 138
SELECT count(DISTINCT program_id) FROM program_tuition_fee f JOIN program p ON p.id=f.program_id WHERE p.academic_year=2027;         -- ≈138 (pages that state a fee)
SELECT count(*) FROM program p JOIN university u ON u.id=p.university_id WHERE u.slug='cuhk' AND p.academic_year=2027 AND p.tuition_amount IS NULL;  -- expect 0 or a handful, each explained by a link-only page
```

- [ ] **Step 5: Full suite under CI conditions, then push**

```bash
uv run pytest -q
mv .env /tmp/.env.bak; env -u DATABASE_URL uv run pytest -q; mv /tmp/.env.bak .env
uv run pylint $(git ls-files '*.py')
```
All green → push the branch and open the PR (title: "Tuition fee breakdown: one row per fee statement, headline derived in code"), body linking the spec and listing the Step 2–4 numbers.

---

## Self-review against the spec

- §1 data model → Task 1 (table, unique key, composite index, enums, migration, no backfill, `Program` relationship). Scope normalisation rules → Task 2.
- §2.1 schema → Task 4 Step 3. §2.2 prompt → Task 4 Step 6. §2.3 derivation → Task 3 (module `src/agents/tuition_headline.py`; derived rows written to detail via Task 4 Step 5 + Task 5). `_reconcile_per_credit_tuition` deleted → Task 4 Step 5. §2.4 dedup → Task 4 Step 4. §2.5 legacy paths → Task 5 Step 4 (importer single row).
- §3.1 persistence → Task 5. §3.2 API and MCP → Task 6. §3.3 export JSON column → Task 7. §3.4 popup → Task 8. §3.5 tests → Tasks 1–7 each carry theirs; migration/drift → Task 1 Step 7 and Task 9 Step 1. §3.6 acceptance → Task 9.
- Deviation recorded: a 1-year per-annum fee is used as-is without writing a derived row (identity conversion would only duplicate the page's own row under another basis); the spec's acceptance numbers (198,000 for both CUHK and EdUHK) are unchanged by this.
- Types: `ParsedTuitionFee.applicant_scope: TuitionScope`, `HeadlineResult.derived: tuple[DerivedFee, ...]`, `query_programs(..., tuition_scope, tuition_study_mode, tuition_basis, tuition_max)`, payload dict keys `amount, currency, basis, study_mode, applicant_scope, scope_label, credits, is_derived, source_text` — used identically in Tasks 4, 5, 6, 7.
