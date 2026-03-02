# Change Log (Consolidated)

## 2026-03-03

### Overall Progress
- Phase 1 (Data Layer): completed.
- Phase 2 (Execution Layer): completed.
- Phase 3 (Quality System Seed): completed and gated in CI.

### Phase 2 Highlights
- Added staged ingestion orchestration with persisted `ingestion_job` / `ingestion_task`.
- Added retry scheduling, poison handling, and resume-from-stage.
- Unified `continue_depth > 0` flow into the Phase 2 pipeline (legacy fallback removed).
- Exposed job operations via API and CLI:
  - `ingestion-jobs`
  - `ingestion-resume`

### Phase 3 Highlights
- Added golden sample framework:
  - `golden_samples/manifest.json`
  - per-case snapshot directories
  - expected outputs per case
- Added tooling:
  - `scripts/collect_golden_samples.py`
  - `scripts/score_golden_samples.py`
  - CLI commands: `golden-collect`, `quality-score`
- Added CI quality gate using offline snapshots and threshold checks.
- Improved offline scoring robustness:
  - context-aware tuition candidate ranking
  - reduced false positives from non-tuition numeric signals (e.g. IELTS scores)

### Current Seed Benchmark
- Golden cases: 3 (UCL, Manchester, Leeds)
- Quality report: `golden_samples/reports/quality_report.json`
- Latest seed run: global pass at threshold `0.60`

### References
- `docs/changelog_phase1_data_layer.md`
- `docs/changelog_phase2_execution_layer.md`
- `docs/changelog_phase3_quality_system.md`
