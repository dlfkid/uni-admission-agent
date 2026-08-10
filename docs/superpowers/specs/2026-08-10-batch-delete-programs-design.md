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

**One addition found during design, confirmed with user:** the existing
single-delete path (`src/services/crawler.py::delete_program_snapshot`, the
module-level wrapper the REST/CLI single-delete entrypoints actually call —
not `DatabaseManager.delete_program_snapshot` directly) also calls
`SubjectTaxonomyService.prune_orphaned_learned_names([program_name])` after
a successful delete, to remove that name from the *learned taxonomy name
cache* if no other program still uses it. Batch delete must do the same for
every deleted program's `name_en`, in **one** call for the whole batch (the
method already accepts a list and does one bulk diff query internally) —
otherwise batch-deleting a whole university leaves stale learned taxonomy
names behind that silently degrade future crawl name-matching. This does
not contradict the "only program + child tables" decision above — Quarantine/
Audit/University are still untouched — it's parity with existing per-row
delete behavior at the same wrapper layer.

**Explicitly out of scope:**
- MCP tool exposure (CLI + REST only, this round).
- Fixing `db-reinit`'s `DATABASE_URL` requirement for SQLite.
- Cleaning up `Quarantine`/`ExtractionAudit` rows (deliberately left alone).
- Cross-university batch delete by year alone (e.g. "delete every
  university's 2025 data in one call") — `university_slug` stays required,
  same principle as `quarantine clear`'s required `--university`.

## 3. Data layer

### 3.1 `src/storage/db_manager.py` — DB-only, no side effects

Two new `DatabaseManager` methods:

```python
def count_programs_by_scope(
    self, university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Read-only preview: count + years + names of matching Program rows."""

def delete_programs_by_scope(
    self, university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Delete all matching Program rows and their children in one transaction."""
```

Where `ProgramDeleteScope` is a small dataclass/NamedTuple:
`{university_slug: str, count: int, years: list[int], deleted_names: list[str]}`.
`deleted_names` holds each matched program's `name_en` — collected by both
methods (before delete, in `delete_programs_by_scope`'s case) purely so the
`src/services/crawler.py` wrapper (§3.2) can feed them to the taxonomy
pruner; CLI/REST responses only read `count`/`years` and ignore
`deleted_names`.

`delete_programs_by_scope`:
1. Selects matching `Program` rows (join `University` on slug; filter by
   `academic_year == year` when given). Records `id` and `name_en` for each.
2. If none match, returns `count=0, years=[], deleted_names=[]` — no-op, no
   error (mirrors `clear_quarantine`'s behavior for unknown/empty scopes).
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
writes.

### 3.2 `src/services/crawler.py` — side effects, the layer CLI/REST call

CLI and REST do **not** call `DatabaseManager` directly for this feature —
they import from `src.services.crawler`, the same module that already hosts
`delete_program_snapshot`/`patch_program_snapshot`/`query_programs` for
exactly this reason (side effects + a stable call surface independent of
`DatabaseManager`'s internals). Two new module-level functions:

```python
def count_programs_by_scope(
    university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Thin passthrough to DatabaseManager — no side effects, used for preview."""

def delete_programs_by_scope(
    university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Delete + prune orphaned learned taxonomy names for the deleted batch."""
```

`delete_programs_by_scope` calls `DatabaseManager().delete_programs_by_scope(...)`,
and if `result.count > 0`, calls
`get_subject_taxonomy_service().prune_orphaned_learned_names(result.deleted_names)`
once for the whole batch, wrapped in the same `try`/`except Exception` +
`logger.warning` pattern `delete_program_snapshot` already uses (a taxonomy
prune failure must never fail or roll back the delete itself — the delete
has already committed by this point).

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

Behavior (both calls go through `src.services.crawler`, not `DatabaseManager`
directly — see §3.2):
- **Without `--yes`:** calls `count_programs_by_scope`, prints e.g.
  `⚠️  This will delete 42 programs across years [2025, 2026] for "leeds". Re-run with --yes to confirm.`
  and exits `0` — no writes performed.
- **With `--yes`:** calls `delete_programs_by_scope` (deletes + prunes
  orphaned taxonomy names), prints `✅ Deleted 42 programs for "leeds".`

## 5. REST — `src/api/server.py`

```
DELETE /programs?univ_slug=<slug>&year=<year, optional>&confirm=<bool, default false>
```

Both calls go through `src.services.crawler` (§3.2), same as the CLI.
- `confirm=false` (default): calls `count_programs_by_scope`, returns `200`
  with `{deleted: false, count, years, message}` — no writes.
- `confirm=true`: calls `delete_programs_by_scope` (deletes + prunes
  orphaned taxonomy names), returns `200` with
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
  untouched, verify `deleted_names` matches the deleted `name_en` values.
- `src/services/crawler.py` unit test (new): verify
  `delete_programs_by_scope` calls `prune_orphaned_learned_names` exactly
  once with the full deleted-name list (mock the taxonomy service — mirrors
  however the existing `delete_program_snapshot` taxonomy-prune call is
  already tested, if it is; otherwise this is new coverage), and that a
  taxonomy-prune exception is swallowed (logged, not raised) without
  affecting the already-committed delete's return value.
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
