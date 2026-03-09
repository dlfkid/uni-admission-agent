# Changelog: Phase 1 Data-Layer Upgrade

## Date
- 2026-03-02

## LLM Resume-First Context (Read This First)
- Branch: `codex/upgrade-db-migrations`
- Target revision after Phase 1: `20260302_0003`
- Core migration chain:
  - `20260302_0001_initial_schema`
  - `20260302_0002_normalize_program_model`
  - `20260302_0003_requirement_dims_evidence_versioning`
- Canonical implementation entry points:
  - write path: `src/storage/db_manager.py`
  - read/query path: `src/services/crawler.py`
  - export path: `src/storage/exporter.py`
  - API schema: `src/api/schemas.py`
  - extension preview rendering: `extension/src/popup.ts`
- Validation commands used in this phase:
  - `uv run pytest --tb=short`
  - `uv run pylint src/ scripts/ --output-format=text --score=y`
  - `npm run build` (under `extension/`)

## Scope
Phase 1 focuses on migrating from a flat `program`-centric storage model to a requirement-centered model that can support:
- global subject/exam/framework normalization,
- source-evidence traceability,
- auditable requirement version history.

## Delivered in Phase 1

### 1. Requirement Fact + Dimension Model
- Added independent dimension tables:
  - `subject_dim` (canonical subject vocabulary + alias tracking)
  - `exam_dim` (standardized test dictionary, e.g. IELTS/TOEFL/SAT)
  - `framework_dim` (qualification framework dictionary, e.g. A-Level/IB)
- `program_requirement` now links to dimensions via:
  - `subject_dim_id`
  - `exam_dim_id`
  - `framework_dim_id`
- Fact table still keeps `subject_name/framework` text for compatibility output, while dimension keys become the normalization backbone.

### 1.1 Table Relationship Snapshot
- `program` (program snapshot by academic year)
  - 1:N `requirement_version`
- `requirement_version` (temporal snapshot node)
  - 1:N `program_requirement`
- `program_requirement` (requirement fact)
  - N:1 `subject_dim`
  - N:1 `exam_dim`
  - N:1 `framework_dim`
  - N:1 `requirement_evidence`

### 2. Evidence Layer
- Added `requirement_evidence` table with:
  - source URL
  - page snippet
  - locator type/value
  - capture/crawl timestamps
  - content hash for de-duplication
- `program_requirement.evidence_id` links each requirement fact to a concrete source evidence record.

### 3. Version System
- Added `requirement_version` table with:
  - `version_no`
  - `effective_at`
  - `valid_from` / `valid_to`
  - `change_summary`
  - `diff_payload` (JSON diff summary)
- `program_requirement.version_id` binds facts to a specific requirement snapshot.
- Upsert behavior changed:
  - old requirement snapshots remain immutable,
  - new snapshots are created only when fingerprint changes,
  - previous active snapshot gets `valid_to` closed automatically.
  - same payload re-upsert does not create a new version (idempotent behavior).

### 4. Migration + Backfill
- Added Alembic migration: `20260302_0003_requirement_dims_evidence_versioning`.
- Backfill strategy:
  - creates baseline requirement version for legacy rows,
  - populates dimension tables from legacy strings,
  - infers exam dimension where possible from requirement text/category,
  - creates evidence rows from requirement text + URL,
  - rewires legacy `program_requirement` rows with new FK links.

### 4.1 Backfill Guarantees
- Legacy `program_requirement` rows are not discarded; they are linked into a generated baseline `requirement_version` (`version_no=1`).
- Existing fields are preserved for compatibility while dimension/evidence FKs are appended.
- Migration is additive-forward and avoids destructive table drops in upgrade path.

### 5. Runtime Read/Write Adaptation
- Storage write path (`DatabaseManager`) now:
  - writes requirement versions incrementally,
  - computes diffs/fingerprints for idempotent updates,
  - persists dimension/evidence references.
- Query/export path now reads **latest requirement version** by default.
- API payload extends requirement metadata with:
  - exam name,
  - evidence metadata,
  - requirement version metadata.
- Extension preview supports requirement version tag (`req-vN`) display.

### 6. CI Migration Validation Strengthening
- CI legacy upgrade path now additionally verifies:
  - `requirement_version` backfilled,
  - `subject_dim` backfilled,
  - `requirement_evidence` backfilled.

## Explicitly Out Of Scope In Phase 1
- Async execution pipeline (`fetch/extract/validate/persist` queue decoupling) is not implemented yet.
- Quality scoring / golden set regression system is not implemented yet.
- Site adapter plugin framework is not implemented yet.
- Unified global normalization service layer (independent service boundary) is not implemented yet.

## Current Outcome
Phase 1 has moved the project from “single table + JSON fields” to a schema that can support:
- normalized global requirement ontology,
- source-auditable requirements,
- temporal version control of requirement changes.

This directly reduces future need for destructive reset/drop operations and improves upgrade survivability.

## Phase 1 Verification Summary
- Unit tests (non-integration): passed.
- Targeted integration tests for schema upsert + requirement versioning: passed.
- Pylint (`src/` + `scripts/`): passed with score 10.00/10.
- Extension build: passed.
- Fresh/legacy migration smoke tests: passed for revision head `20260302_0003`.

---

## Next Phases (Detailed Plan)

## Phase 2: Execution-Layer Decoupling (Async Pipeline + Idempotency)
### Goal
Split crawling/extraction/validation/persistence into queue-driven stages.

### Plan
- Introduce `ingestion_job` and `ingestion_task` tables.
- Define stage contracts:
  - `fetch_raw`
  - `extract_structured`
  - `validate_rules`
  - `persist_versioned`
- Add idempotency keys per stage (`source_url + content_hash + stage`).
- Add retry policy with bounded backoff and poison queue handling.
- Support resume-from-stage for failed jobs.

### Acceptance Criteria
- Retry does not duplicate requirement versions/facts.
- Stage failure can resume without rerunning successful upstream stages.
- End-to-end run has deterministic job trace.

### Suggested Implementation Order
1. Add `ingestion_job` and `ingestion_task` models + migration.
2. Add persistent stage state machine (`PENDING/RUNNING/FAILED/SUCCEEDED`).
3. Refactor current crawl entry (`crawl_url`) to enqueue stage tasks instead of direct sync flow.
4. Introduce idempotency key contract and retry semantics.
5. Add replay/resume API endpoints for failed tasks.

## Phase 3: Quality System (Gold Set + Scoring + Regression)
### Goal
Make extraction quality measurable and regressions blockable.

### Plan
- Build `golden_samples/` with representative universities and edge cases.
- Implement automatic scoring dimensions:
  - field completeness
  - value correctness
  - normalization consistency
  - evidence linkage coverage
- Add regression suite in CI with threshold gates.
- Add low-confidence queue for human review and replay.

### Acceptance Criteria
- Every release produces comparable quality score report.
- Regression beyond threshold fails CI.
- Low-confidence items are isolated for manual handling.

### Suggested Implementation Order
1. Freeze an initial `golden_samples` dataset from 5-10 representative universities.
2. Implement scorer producing machine-readable JSON report.
3. Add CI threshold gate and trend snapshot.
4. Add low-confidence queue and review workflow.

## Phase 4: Site Adapter Framework
### Goal
Support site-specific extraction strategies without polluting core pipeline.

### Plan
- Define adapter interface (`match`, `fetch`, `extract`, `normalize` hooks).
- Keep generic adapter as default.
- Add plugin loader and adapter registry.
- Start with top-priority university domains as official adapters.

### Acceptance Criteria
- New site adapter can be added without modifying core pipeline logic.
- Adapter fallback chain is deterministic and observable in logs.

### Suggested Implementation Order
1. Define adapter interface and default generic adapter.
2. Add adapter registry + domain matcher.
3. Extract first domain-specific adapter as proof.
4. Add adapter-level regression fixtures.

## Phase 5: Unified Standardization Services
### Goal
Centralize canonical normalization across subject/exam/framework/date/amount/duration.

### Plan
- Build standardization service layer:
  - subject vocabulary resolver
  - exam mapping resolver
  - framework resolver
  - money/date/duration normalizer
- Add versioned dictionaries and conflict resolution policy.
- Add change-impact checks for dictionary updates.

### Acceptance Criteria
- Same raw input normalizes to same canonical output across ingestion channels.

---

## 2026-03-10 Phase 3 Update: Program-Name Resolution Hardening

- Added deterministic `index -> detail` program-name resolver with source-priority ranking:
  - `selected_anchor_text` > URL slug > html title > markdown extraction.
- Added low-confidence/near-tie fallback path:
  - one-shot LLM fallback (default enabled per request/pipeline settings),
  - strict unresolved gate when confidence stays low.
- Added ingestion-stage no-pollution gate:
  - unresolved names are skipped from `program_candidates`,
  - unresolved diagnostics captured as `unresolved_urls`.
- Exposed request-level controls:
  - `name_resolution_llm_enabled`
  - `name_resolution_low_threshold`
  - `name_resolution_conflict_delta`
- Extended crawl/task result payloads with unresolved diagnostics for UI/operator visibility.
- Added Leeds regression coverage and supporting unit tests for noisy heading/requirements false positives.
- Dictionary update impact can be simulated before rollout.
- Standardization artifacts are testable independently from crawler logic.

### Suggested Implementation Order
1. Move subject/exam/framework normalization logic out of `db_manager` into dedicated service modules.
2. Introduce versioned dictionaries and migration-safe seeding.
3. Wire service into both import and crawl pipelines.
4. Add deterministic contract tests for each normalization dimension.

---

## Notes for Release Readiness
- Before GA, add:
  - migration dry-run check for production-like datasets,
  - automated rollback playbook validation,
  - contract tests for API/extension around new requirement version metadata.

## Handoff Checklist For Next Model/Engineer
- Read this file first, then inspect:
  - `src/storage/db_manager.py`
  - `migrations/versions/20260302_0003_requirement_dims_evidence_versioning.py`
  - `src/services/crawler.py`
- Confirm DB is at `20260302_0003` via:
  - `uv run src/cmd/cli.py db-version`
- Before Phase 2 coding, run:
  - `uv run pytest --tb=short`
  - `uv run pylint src/ scripts/ --output-format=text --score=y`
