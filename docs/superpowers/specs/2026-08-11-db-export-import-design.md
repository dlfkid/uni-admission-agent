# Whole-Database Export/Import (DB-Agnostic Migration) — Design

**Date:** 2026-08-11
**Status:** Approved (pending spec review)
**Part 2 of 3** in the "data management improvements" series (batch delete
[done, PR #49] → database migration/portability [this spec] → deprecate
Excel import).

## 1. Problem

The project supports two database backends (SQLite default, Postgres
opt-in via `DATABASE_URL`), and the typical deployment is one person/one
machine running the whole stack — there's no built-in way to hand off
accumulated crawl results to someone else running their own instance.
Today's only portability options are:

- Manually copying the SQLite `.db` file — works only when both sides use
  SQLite, and copying a live WAL-mode file is unsafe without stopping the
  server first.
- Per-university Excel export (`export`/`POST /export`) — scoped to one
  `univ_slug` at a time, loses referential structure (IDs, FK relationships,
  version history), and isn't meant to be re-imported as a database snapshot
  at all (it's a human-readable spreadsheet, consumed by `ExcelImporter`'s
  best-effort re-parsing, not a lossless round-trip).

Neither works for "export everything, hand the file to someone else, they
import it into their own fresh install regardless of which backend either
side uses."

## 2. Scope

Two new CLI commands: `uni-admission db-export --output <file.zip>` and
`uni-admission db-import --file <file.zip> [--yes] [--force]`.

**Export scope: all 17 `table=True` models** (confirmed with user — not a
hand-maintained whitelist of "core" tables). Rationale: a generic,
metadata-driven mechanism (§3) costs the same to implement for 17 tables as
for 5, requires no per-table whitelist to keep in sync as new tables are
added, and matches what the user actually asked for ("整体导出" — the
*whole* database). This includes the "core" admission-data tables
(`University`, `ProgramCatalog`, `Program` + its four child tables), the
dimension/learned-cache tables (`SubjectDim`, `ExamDim`, `FrameworkDim`,
`RequirementEvidence`, `SubjectTaxonomy`), and the diagnostic/pipeline-state
tables (`ProgramQuarantine`, `ExtractionAudit`, `ExtractionAuditLink`,
`IngestionJob`, `IngestionTask`) that batch-delete (part 1) deliberately
leaves alone — this feature is a full snapshot, not a curated one.

**Import scenario (confirmed with user): target is always a fresh, empty
database.** This is the stated real-world use case — handing crawl results
to someone who just installed `adm-agent` and hasn't run anything yet. This
assumption eliminates the need for upsert/merge logic or foreign-key
remapping: original primary keys are preserved verbatim on import (safe
precisely because the target is empty, so there's nothing to collide with).
`db-import` verifies this assumption at runtime (§5) rather than trusting
it blindly.

**Explicitly out of scope:**
- REST/Web UI entry points (confirmed with user — CLI only, matching
  `db-reinit`'s precedent; import inherently needs local filesystem access
  to the target machine's database, which a browser-driven flow doesn't
  give cleanly).
- Merging/upserting into an already-populated target database.
- Cross-version schema migration of the *data itself* — see §6 assumptions.
- Streaming/chunked I/O for very large datasets — see §6 assumptions.

## 3. Architecture — generic, metadata-driven, not a per-table whitelist

Both commands iterate `SQLModel.metadata.sorted_tables` — SQLAlchemy's
topologically-sorted table list (parents before children, computed
automatically from foreign keys). This is the same ordering principle
already relied on implicitly by `_sync_schema`'s use of
`SQLModel.metadata.sorted_tables` in `src/storage/db_manager.py`. Concretely:

- **Export** (`src/storage/db_portability.py`, new module — sibling to the
  existing `db_manager.py`/`exporter.py`/`importer.py`): for each table in
  sorted order, `SELECT *` all rows via SQLAlchemy Core (`session.exec(select(table))`
  is SQLModel-model-shaped; exporting needs raw column values across
  arbitrary tables, so this reads via `session.connection().execute(table.select())`
  and serializes each row via column-type-aware conversion, §4), write one
  `<table_name>.json` (a JSON array of row-dicts) into a zip, plus a
  `manifest.json`.
- **Import**: after the pre-flight checks (§5), for each table in the same
  sorted order, read `<table_name>.json`, reconstruct native Python types
  per column (§4), and bulk-insert via `session.execute(table.insert(), rows)`
  — one shared `Session`/transaction for the entire import, so any failure
  on any table rolls back everything already inserted in this run.

No table is named in the implementation beyond the generic loop — a future
new model automatically participates in both export and import without
this feature's code changing.

## 4. Type round-tripping (export → JSON → import)

JSON only natively represents strings/numbers/bools/null/arrays/objects.
Several column types need explicit conversion in both directions, driven by
inspecting each column's SQLAlchemy type (`isinstance(column.type, ...)`) —
not per-model special-casing, so new columns of an already-handled type
work automatically:

| Column type | Export (Python → JSON) | Import (JSON → Python) |
|---|---|---|
| `DateTime` / `Date` | `.isoformat()` string | `datetime.fromisoformat(...)` / `date.fromisoformat(...)` |
| `Numeric` (`Program.tuition_amount`) | `str(value)` (preserves precision) | `Decimal(value)` |
| `Enum`-backed columns (`StudyMode`, `RequirementCategory`, `CurrencyCode`, `IngestionJobStatus`, `IngestionStage`, etc. — all `str, Enum` subclasses) | the string value itself (already JSON-safe) | passed through as-is — SQLAlchemy's `Enum` type accepts the raw string value on bind |
| `JSON` columns (`Program.deadlines`/`study_options`/`extra_metadata`, `RequirementVersion.diff_payload`, `SubjectDim.aliases`) | already JSON-compatible structures — no conversion | already JSON-compatible structures — no conversion |
| Everything else (str, int, bool, None) | passed through | passed through |

## 5. `db-import` pre-flight and execution order

1. **Emptiness check.** Count rows across all 17 tables in the target
   database. If the total is nonzero and `--force` was not passed, abort
   with an error naming the check and telling the user to pass `--force` if
   they really want to proceed (which does **not** enable merge/upsert
   semantics — it only skips this guard; if the target genuinely has
   conflicting data, the subsequent inserts fail on unique/PK constraint
   violations and the transaction rolls back, per §2's "explicitly out of
   scope: merging").
2. **Confirmation.** Without `--yes`, prompt for interactive confirmation
   (`typer.confirm`, matching `db-reinit`'s pattern) naming the total row
   count about to be written and the resolved `DATABASE_URL`/default SQLite
   path.
3. **Schema migration.** Run `run_db_migrations(revision="head")` (the same
   helper `db-reinit`/`db-migrate` already use in
   `src/services/migrations.py`) so the target schema is at head before any
   data is written.
4. **Bulk insert.** For each table in `SQLModel.metadata.sorted_tables`
   order, reconstruct rows (§4) and `session.execute(table.insert(), rows)`.
   One shared transaction for the whole run — `session.commit()` only after
   every table succeeds; any exception rolls back everything via the
   session's context-manager `__exit__`.
5. **Postgres sequence fix-up (Postgres targets only).** Because original
   primary keys are inserted explicitly, Postgres's `serial`/identity
   sequence counters do not advance to match. After the transaction
   commits, for every table that had ≥1 row imported and has an integer
   primary key column, run
   `SELECT setval(pg_get_serial_sequence('"<table>"', '<pk_column>'), (SELECT MAX(<pk_column>) FROM "<table>"))`.
   SQLite needs no equivalent step — its rowid-based autoincrement already
   continues from the actual max rowid present, with no separate counter to
   desync. This is an engine-aware *finishing* step, not a violation of the
   "export format is engine-agnostic" requirement — `pg_dump` performs the
   same kind of step for exactly the same reason.
6. **Report.** Print per-table inserted-row counts and a final success
   message.

## 6. Explicit assumptions / non-goals (to prevent later "wasn't this supposed to..." confusion)

- **Export and import are assumed to run against the same codebase / same
  Alembic head.** `manifest.json` records the *source* database's Alembic
  revision at export time purely as human-readable metadata (printed during
  import for reference) — it is **not** version-checked or enforced. If the
  export was taken on an older schema than the target migrates to, the
  import is not guaranteed to succeed (a new required column with no
  default, for instance, would need a value this feature does not
  synthesize). Out of scope for this version; a hard version check could be
  a later addition if it becomes a real problem.
- **No streaming/chunking.** The whole dataset is loaded into memory as
  Python dicts during both export and import. Appropriate at this project's
  actual scale (a handful of universities, low thousands of rows total) —
  not designed for arbitrarily large datasets.
- **No REST/Web UI surface**, no merge/upsert semantics — see §2.

## 7. Testing

- `src/storage/db_portability.py` unit tests, real in-memory SQLite (same
  pattern established for part 1's `db_manager.py` tests — not mocks, since
  correctness here is about actual type round-tripping and FK-ordered
  insert/export, which mocking a `session.exec` chain can't meaningfully
  verify):
  - Round-trip test: seed a small multi-table dataset (University →
    ProgramCatalog → Program → child rows, including at least one row with
    a `DateTime`, one with a `Numeric`, one with an `Enum`-backed column,
    one with a JSON column), export to a temp zip, import into a second
    fresh in-memory engine, assert the reconstructed rows are equal to the
    originals (including type, not just string representation — e.g.
    `tuition_amount` must come back as a `Decimal`, not a string).
  - Export produces a `manifest.json` with correct per-table row counts and
    a non-empty `alembic_revision` field.
  - Export of an empty database succeeds and produces a zip with empty
    per-table JSON arrays (not an error).
  - Import into a non-empty target without `--force` refuses and makes no
    changes; import into a non-empty target with `--force` proceeds to
    attempt inserts (and surfaces the resulting constraint-violation error
    if one occurs, rather than silently succeeding).
  - Import runs table inserts in FK-dependency order (a test that seeds an
    export with a child row referencing a parent row not yet present in the
    target, and confirms import succeeds because parents are inserted
    first — this is really testing that `sorted_tables` ordering is used
    correctly, not re-testing SQLAlchemy itself).
- CLI tests (`typer.testing.CliRunner`, mocking the `db_portability`
  functions by name — matching `tests/test_diagnostics_clear.py`'s CLI-test
  convention): `db-export` writes to `--output`; `db-import` prompts
  without `--yes`, skips the prompt with `--yes`; `db-import` refuses on a
  non-empty target without `--force` and proceeds with `--force`.
- Postgres sequence fix-up (§5 step 5) has **no automated test** — this
  repo's test suite has no live Postgres fixture anywhere today (checked:
  every existing `postgresql`/`psycopg2` reference in `tests/` mocks a URL
  string for connection-string logic, never opens a real connection), and
  SQLite has no equivalent behavior to verify against. The plan documents
  this step as manually-verified-only for this version; the gap is named
  here rather than silently skipped or falsely claimed as tested.
