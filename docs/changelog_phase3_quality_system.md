# Changelog: Phase 3 Quality System (Seed)

## Date
- 2026-03-03

## Scope
Phase 3 seed establishes a reusable golden-sample workflow for offline quality scoring and CI regression gating.

## Delivered in this increment

### 1. Golden sample dataset scaffold
- Added manifest: `golden_samples/manifest.json`.
- Seeded 3 benchmark cases:
  - UCL (undergraduate)
  - Manchester (masters)
  - Leeds (masters)
- Added per-case expectation files under `golden_samples/cases/*/expected.json`.

### 2. Snapshot collection tooling
- Added collection service: `src/services/golden_samples.py`.
- Added script: `scripts/collect_golden_samples.py`.
- Added CLI command: `adm-agent golden-collect`.

### 3. Offline scoring + regression gate
- Added scoring service: `src/services/quality_scoring.py`.
- Added script: `scripts/score_golden_samples.py`.
- Added CLI command: `adm-agent quality-score`.
- Hardened offline tuition extraction with context-aware candidate ranking to avoid false matches (e.g. IELTS numeric scores).
- Scoring dimensions:
  - completeness
  - correctness (name similarity + keyword coverage)
  - normalization consistency
  - evidence coverage

### 4. CI threshold enforcement
- CI now runs quality scoring against golden samples.
- Quality report is emitted to `golden_samples/reports/ci_quality_report.json`.
- CI fails when global threshold or per-case pass conditions are not met.

## Usage
```bash
# Collect or refresh snapshots from manifest URLs
uv run python scripts/collect_golden_samples.py --manifest golden_samples/manifest.json --overwrite

# Run quality scoring manually
uv run python scripts/score_golden_samples.py --manifest golden_samples/manifest.json --threshold 0.60

# Equivalent CLI commands
./adm-agent golden-collect --overwrite
./adm-agent quality-score --threshold 0.60
```

## Notes
- This is a Phase 3 seed implementation to establish repeatable quality gates.
- Additional universities and stricter expected outputs should be added incrementally.

---

## 2026-03-06: Taxonomy-Guided Name Accuracy (PolyU)

### Scope
- Added a canonical `subject_taxonomy` data model and migration.
- Added runtime taxonomy service with seed sync, in-memory token index, and fuzzy matching.
- Added per-request taxonomy override controls from API and Chrome extension.
- Added online learning and export path for taxonomy maintenance.
- Added PolyU golden case: `polyu_masters_asset_wealth`.

### Runtime behavior
- Server startup now attempts taxonomy bootstrap from:
  - `golden_samples/program_names/cleaned_programs_names.json`
- During detail extraction, pipeline now:
  - builds signals in priority: selected anchor text → URL tokens → heading fallback
  - injects name hints only when match score ≥ low threshold
  - applies canonical-name override only when score ≥ high threshold and override is enabled
  - records matching trace under `extra_metadata.taxonomy_match`

### New interfaces
- `POST /crawl` request fields:
  - `taxonomy_enabled`
  - `taxonomy_low_threshold`
  - `taxonomy_high_threshold`
  - `taxonomy_hint_top_k`
  - `taxonomy_override_enabled`
  - `selected_link_texts`
- New CLI command:
  - `adm-agent taxonomy-export --output <path> --include-learned --min-confidence 0.9`

### Quality updates
- Golden manifest extended to 4 benchmark cases (added PolyU).
- Manual regression checks:
  - `uv run python scripts/collect_golden_samples.py --manifest golden_samples/manifest.json --overwrite`
  - `uv run python scripts/score_golden_samples.py --manifest golden_samples/manifest.json --threshold 0.60`
