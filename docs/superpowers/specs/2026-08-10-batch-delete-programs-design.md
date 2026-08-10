# Batch Delete Programs by University/Year — Design

**Date:** 2026-08-10
**Status:** Approved (pending spec review)
**Part 1 of 3** in the "data management improvements" series (batch delete →
database migration/portability → deprecate Excel import).

## 1. Problem

Deletion currently has only two extremes:

- `DELETE /programs/{program_id}` (REST only) — deletes exactly one program
  snapshot, via `DatabaseManager.delete_program_snapshot`.
- `uni-admission db-reinit --yes` (CLI only) — drops and recreates the
  **entire** configured database. It also hard-requires `DATABASE_URL` to be
  set; for the SQLite default (no `DATABASE_URL`) it exits immediately with
  `❌ DATABASE_URL is not configured.` — an unrelated pre-existing gap, out
  of scope here, not fixed by this design.

There is no way to delete "everything for university X" or "everything for
university X in year Y" without either surgical per-row REST calls or
nuking the whole database.

## 2. Scope

Add batch deletion scoped by `university_slug` (required) and optionally
`academic_year`, available from both the CLI and REST API.

**Cascade scope (explicit decision):** only `Program` rows and their
existing child tables — `ProgramRequirement`, `RequirementVersion`,
`ProgramStudyOption`, `ProgramDeadline`, and `ProgramCatalog` cleanup when a
catalog has no remaining programs — the same cascade `delete_program_snapshot`
already performs for a single row. `University`, `Quarantine`, and
`ExtractionAudit` rows are **not** touched by this feature, confirmed safe:
neither `Quarantine` (`src/models/quarantine.py`) nor `ExtractionAudit`
(`src/models/extraction_audit.py`) holds a foreign key to `program.id` —
both key by `university_slug` + `academic_year` — so leaving them behind
creates no FK integrity risk, only benign historical diagnostic rows that a
re-crawl will naturally supersede.

**Explicitly out of scope:**
- MCP tool exposure (CLI + REST only, this round).
- Fixing `db-reinit`'s `DATABASE_URL` requirement for SQLite.
- Cleaning up `Quarantine`/`ExtractionAudit` rows (deliberately left alone).
- Cross-university batch delete by year alone (e.g. "delete every
  university's 2025 data in one call") — `university_slug` stays required,
  same principle as `quarantine clear`'s required `--university`.

## 3. Data layer — `src/storage/db_manager.py`

Two new `DatabaseManager` methods:

```python
def count_programs_by_scope(
    self, university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Read-only preview: count of matching Program rows + distinct years touched."""

def delete_programs_by_scope(
    self, university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Delete all matching Program rows and their children in one transaction."""
```

Where `ProgramDeleteScope` is a small dataclass/NamedTuple:
`{university_slug: str, count: int, years: list[int]}`.

`delete_programs_by_scope`:
1. Selects matching `Program.id`s (join `University` on slug; filter by
   `academic_year == year` when given).
2. If none match, returns `count=0, years=[]` — no-op, no error (mirrors
   `clear_quarantine`'s behavior for unknown/empty scopes).
3. Inside **one** session/transaction: bulk-delete child rows for the
   matched program IDs (`ProgramRequirement`, `RequirementVersion`,
   `ProgramStudyOption`, `ProgramDeadline`), then the `Program` rows
   themselves.
4. After all program rows in the batch are gone, compute which
   `ProgramCatalog` rows now have zero remaining `Program` references (across
   the whole batch, not per-row-mid-transaction — avoids a catalog with a
   surviving sibling elsewhere in the same batch being incorrectly
   evaluated before that sibling is deleted) and delete those catalogs.
5. Single commit. This is the same cascade `delete_program_snapshot` already
   performs per-row, applied batch-wide in one transaction instead of N
   transactions.

`count_programs_by_scope` runs the same matching query as step 1 with no
writes — used by both CLI preview and the REST `confirm=false` path.

## 4. CLI — `src/cmd/cli.py`

New `programs` sub-`Typer` app, following the existing `quarantine`/`audit`/
`diagnostics` sub-app convention:

```bash
uni-admission programs delete --university <slug> [--year Y]
```

- `--university` / `-u`: required (no whole-table nuke from this command,
  matching `quarantine clear`'s required `--university`).
- `--year` / `-y`: optional.
- `--yes`: skip the preview-only short-circuit and execute.

Behavior:
- **Without `--yes`:** calls `count_programs_by_scope`, prints e.g.
  `⚠️  This will delete 42 programs across years [2025, 2026] for "leeds". Re-run with --yes to confirm.`
  and exits `0` — no writes performed.
- **With `--yes`:** calls `delete_programs_by_scope`, prints
  `✅ Deleted 42 programs for "leeds".`

## 5. REST — `src/api/server.py`

```
DELETE /programs?univ_slug=<slug>&year=<year, optional>&confirm=<bool, default false>
```

- `confirm=false` (default): calls `count_programs_by_scope`, returns `200`
  with `{deleted: false, count, years, message}` — no writes.
- `confirm=true`: calls `delete_programs_by_scope`, returns `200` with
  `{deleted: true, count, years, message}`.

New schema in `src/api/schemas.py`, sibling to the existing
`DeleteProgramResponse`:

```python
class BatchDeleteProgramsResponse(BaseModel):
    """Response for `DELETE /programs?univ_slug=...`."""
    university_slug: str
    year: Optional[int]
    count: int
    years: list[int]
    deleted: bool
    message: str
```

This keeps CLI and REST symmetric — preview-then-confirm — but REST stays
stateless (no server-side "pending confirmation" token to track between two
calls; `confirm` is just a normal request parameter).

## 6. Testing

- `db_manager` unit tests (new, alongside existing storage tests): multi-year
  fixture for one university, verify `count_programs_by_scope` and
  `delete_programs_by_scope` only affect the targeted slug/year combination,
  verify catalog cleanup happens exactly when the last sibling program is
  removed, verify `Quarantine`/`ExtractionAudit`/`University` rows survive
  untouched.
- `tests/test_api_program_crud.py`: extend with `DELETE /programs` preview
  (`confirm=false`) and confirm (`confirm=true`) cases, including the
  zero-match case.
- New CLI test (alongside existing quarantine/audit CLI tests, likely
  `tests/test_cli_diagnostics.py` or a new file): preview-without-`--yes`
  prints count and performs no delete; `--yes` deletes and reports the
  count; missing `--university` errors per Typer's required-option handling.

## 7. Non-goals / deferred (tracked, not blocking)

- MCP tool for this operation.
- `db-reinit`'s SQLite/`DATABASE_URL` gap.
- Any change to `Quarantine`/`ExtractionAudit` retention.
