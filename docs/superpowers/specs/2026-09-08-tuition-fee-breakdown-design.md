# Tuition fee breakdown — design

**Date:** 2026-09-08  **Status:** approved in discussion, pending written review
**Branch:** `feat/battle-test-hk-2026-27` (follow-up PR after #58)

## Problem

A programme page rarely publishes one tuition figure. Across the nine golden
detail pages the figure varies along three axes that today's model cannot
express:

| University | Page wording | Axis |
|---|---|---|
| CUHK | HK$198,000 per annum (full-time) / HK$99,000 per annum (part-time) | study mode |
| EdUHK | Local $47,000 per annum / Non-local $198,000 per annum | applicant scope |
| Leeds | UK £17,500 (Total) / International £33,000 (Total) | scope + basis = whole programme |
| Manchester, UCL | UK £15,800 per annum / International £31,000; UCL labels "2026/27" | scope |
| PolyU | HK$495,000 per programme (HK$16,500 per credit × 30) for local and non-local | basis = programme + credit |
| HKBU | HK$180,000/programme | single value |
| CityU | tuition is a link; no figure in the body | none extractable (thin-page supplement) |
| Edinburgh | only living costs, £1,023–£2,043 per month | noise that must be excluded |

Today `ParsedTuition` is `amount + currency`, `Program.tuition_amount` is one
number, and the extraction prompt tells the LLM to *pick* one figure by a
rule ("prefer the programme total; else multiply per-credit; else per-year").
Everything not picked is lost at extraction time. The CUHK 2027 crawl stored
198,000 and silently dropped the part-time 99,000.

## Decisions taken in discussion

1. **Both humans and machines filter** the detail (Web UI / export for
   people; MCP and REST query parameters for agents). Hence an indexed table
   with enum columns, not a JSON column.
2. **The coarse `Program.tuition_amount` stays and is derived in code** from
   the detail rows by a fixed priority. Applicant scope priority is
   **non-local first**: the product's users are mainland applicants to HK and
   UK, and the headline figure should be "what I would pay".
3. **Detail rows sync by key on re-crawl** (like `program_study_option` and
   `program_deadline`), not versioned like requirements. `program` is already
   scoped by `academic_year`, so cross-year history is separate rows; within a
   year a re-crawl should reflect the page as it is.
4. **Export stays flexible**: one JSON-string column per programme. Wide
   per-dimension columns were considered and rejected — they hard-code the
   dimensions, and a basis or scope we have not seen yet would have nowhere to
   go.
5. **No backfill.** Existing `tuition_amount` values cannot be decomposed into
   basis and scope; they are kept as-is and detail rows appear on the next
   crawl.

## 1. Data model

New table `program_tuition_fee` — one row per fee statement on the page.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `program_id` | FK → `program.id`, indexed | cascades on programme delete, as `program_study_option` does |
| `amount` | Numeric(12,2), not null | |
| `currency` | `CurrencyCode` enum, not null | existing enum |
| `basis` | enum `TuitionBasis`, not null, indexed | `per_programme` / `per_annum` / `per_semester` / `per_credit` |
| `study_mode` | existing `STUDY_MODE_ENUM`, not null, indexed | `Unknown` = the page did not distinguish; read as "applies to every mode" |
| `applicant_scope` | enum `TuitionScope`, not null, indexed | `all` / `local` / `non_local` — three values so it can be filtered |
| `scope_label` | str, nullable | the page's own word: "International, including EU", "Overseas", "Non-local Students". Display only |
| `credits` | int, nullable | `per_credit` rows only |
| `is_derived` | bool, default false | computed rows (per-credit × credits, per-annum × years) — distinguished from what the page says |
| `source_text` | str(300), nullable | the sentence on the page; evidence |
| `updated_at` | datetime | |

**Unique key** `(program_id, study_mode, applicant_scope, basis)` — the sync
key for re-crawls.

**Composite index** `(applicant_scope, study_mode, basis, amount)` for the
filter queries ("non-local, full-time, per-annum under 200,000").

**Scope normalisation** (`scope_label` → `applicant_scope`) lives in code,
not in the LLM:

- `local`: Local, Home, UK, UK/EU (pre-Brexit wording), Domestic, 本地
- `non_local`: Non-local, International, Overseas, EU (post-Brexit UK pages
  price EU with International), 非本地
- `all`: not distinguished, or explicitly "for local and non-local students"
  (PolyU)

Unrecognised labels map to `all` and keep the label in `scope_label`; a
warning is logged so the vocabulary can be extended.

**Enums** follow the `STUDY_MODE_ENUM` pattern
(`SqlEnum(..., values_callable=_enum_values)`): native enum type on Postgres,
string on SQLite; both verified.

**Migration** `20260908_0011_program_tuition_fee`: creates the table, its
unique constraint and indexes, and the two enum types on Postgres. Nothing
else. `downgrade` drops them.

**Unchanged:** `Program.tuition_amount` / `currency`; `program_study_option`
gains no column (EdUHK prices by scope independently of mode, so tuition does
not belong on the study-option row).

## 2. Extraction

### 2.1 LLM output schema (`src/agents/cleaner_agent.py`)

New `ParsedTuitionFee`: `amount` (reuses the existing "14k / 1.5m / commas"
parser), `currency`, `basis`, `study_mode` (default `Unknown`), `scope_label`
(nullable), `credits` (nullable), `source_text` (≤300 chars).

`ParsedProgramData` gains `tuition_fees: List[ParsedTuitionFee]`.
`tuition: Optional[ParsedTuition]` **stays** but is populated by code (2.3);
anything the LLM puts there is overwritten. Every reader of `parsed.tuition`
— chunk merge, dedup, thin-page detection, quality gate — is untouched.

### 2.2 Prompt (`src/agents/prompts/clean_chunk.txt`, tuition section)

From "pick one" to "copy all":

- One row per fee statement on the page. Do not merge, choose, or convert.
- Each row records amount, currency, basis (per programme / per annum / per
  semester / per credit), the study mode it applies to, the applicant wording
  it applies to, the credit count (per-credit rows only), and the sentence.
- A page giving both a programme total and a per-credit rate (PolyU) yields
  two rows with their own basis.
- **Exclude** living costs, application fees, deposits, confirmation fees,
  credit-transfer fees and scholarship amounts — none is tuition.
  Edinburgh's "£1,023 to £2,043 each month" is this class.
- A link with no figure (CityU) yields an empty list. Do not guess.

### 2.3 Headline derivation — `derive_headline_tuition(fees, study_options)`

Pure function in a new module `src/agents/tuition_headline.py`. Returns
`Optional[ParsedTuition]` plus any derived rows to add to the detail.

| Step | Rule | If nothing matches |
|---|---|---|
| Scope | `non_local` → `all` → `local` | next tier |
| Mode | `FullTime` → `Unknown` → `PartTime` → `Hybrid` | next tier |
| Basis | `per_programme` stated on the page → use as-is | |
| | else `per_annum` × programme years (from the `study_options` row with the same mode; `duration_months` rounded up to whole years) → use, mark `is_derived` | no duration → use the `per_annum` figure unconverted |
| | else `per_credit` × `credits` → use, mark `is_derived` | no credit count → skip this row |
| | `per_semester` never feeds the headline (semester counts are unreliable) | |

Derived rows are **also written to the detail table** with `is_derived=true`,
so the headline is traceable to a row the UI can show.

`_reconcile_per_credit_tuition` (regex over the markdown to multiply
per-credit rates) is **deleted**; step three of the table replaces it and no
longer guesses from the page text.

### 2.4 Dedup

`_normalize_parsed_data` dedups `tuition_fees` by
`(study_mode, applicant_scope, basis)`, keeping the row with the longer
`source_text` (a chunk merge can copy the same fee twice).

### 2.5 Legacy paths

`src/scrapers/schema_extractor.py` and the Excel path in
`src/storage/importer.py` still produce a single value. Unchanged this round;
a single imported figure is stored as one row `all / Unknown / per_programme`.

## 3. Persistence, read side, verification

### 3.1 Persistence (`src/storage/db_manager.py`)

`_sync_tuition_fees(session, program_id, fees)` mirrors
`_sync_study_options`: match existing rows on the unique key, update amount /
source / `is_derived`, insert new, delete rows no longer on the page.
`derive_headline_tuition`'s result is written to `Program.tuition_amount` /
`currency` — **the only change to where the coarse field comes from**; its
fourteen readers see the same column.

`page_processor.py` and `importer.py` place `parsed.tuition_fees` into
`program_data["tuition_fees"]`; persistence consumes it. Programme deletion
cascades to the detail rows alongside study options.

### 3.2 API (`src/api/server.py`, `schemas.py`)

- `ProgramResponse.tuition_fees: list` — each item carries amount, currency,
  basis, study_mode, applicant_scope, scope_label, credits, is_derived,
  source_text. Additive; old clients unaffected.
- `GET /programs` gains optional filters `tuition_scope`,
  `tuition_study_mode`, `tuition_basis` (default `per_programme`) and
  `tuition_max`. Semantics: *there exists* a detail row satisfying all given
  conditions — an `EXISTS` subquery on the composite index. `QueryRequest`
  gains the same four fields, which gives the MCP `query` tool the filter.
- `PATCH /programs/{id}` `tuition_amount` still edits the coarse field only.

### 3.3 Export (`src/storage/exporter.py`)

`Tuition` / `Currency` columns unchanged. One new column
`Tuition Fees (JSON)`: the programme's detail rows as a JSON array string
(same fields as the API item). No per-dimension columns, no second sheet —
the dimensions are not fixed and a JSON string absorbs a new basis or scope
without a schema change. CSV export carries the same column.

### 3.4 Frontend (`frontend/src/shared/popup`, display only)

The preview card keeps its `HKD 1,500,000` chip and adds a collapsible
"N fee lines" list beneath it rendering the detail rows. The edit dialog is
unchanged: a manual edit changes the coarse value only.

### 3.5 Tests

- `derive_headline_tuition`: one case per cell of the priority matrix,
  including per-annum × duration conversion, per-credit × credits,
  "only per_semester → no headline", "no rows → None".
- Scope normalisation: UK / Home / International / Overseas / EU / 本地 /
  非本地 / unrecognised-with-warning.
- The nine golden `detail.md` files, **without the LLM**: hand-written
  expected detail rows per page, asserting `derive_headline_tuition`'s
  result. The table in *Problem* becomes the test.
- `_sync_tuition_fees`: insert, update, delete-stale, cascade on programme
  delete — in-memory SQLite as in `test_db_portability.py`.
- Migration: `alembic upgrade head` on SQLite and Postgres; `repair --auto`
  reports no drift.
- API: one `EXISTS` semantics case (a programme matches on scope but not on
  mode is excluded); one `tuition_max` boundary case.

### 3.6 Acceptance

Run `--limit` crawls on CUHK MA in Anthropology (FT 198,000 / PT 99,000 per
annum) and EdUHK MA Educational Psychology (Local 47,000 / Non-local 198,000
per annum). Expect two detail rows each with the right scope and mode, and
headline values of **198,000** for CUHK (`all → FullTime → per_annum 198,000
× 1 year`, derived: a 1-year full-time programme, so the derived
`per_programme` row is stored with `is_derived=true` alongside the page's
own `per_annum` row — every headline is traceable to a `per_programme` row,
which also keeps a 1-year programme visible to the default `per_programme`
filter) and **396,000** for EdUHK (`non_local → FullTime → per_annum 198,000
× 2 years`, derived: the page states a two-year normative full-time period,
and the derived row is stored with `is_derived=true`). If a page's study
option carries no duration, the per-annum figure is used unconverted with no
derived row. Then run the CUHK
taught-programme index (138 programmes) in full.

## Out of scope

- Backfilling detail rows for programmes crawled before this change.
- Per-dimension export columns or a second export sheet.
- Editing detail rows from the frontend.
- Versioning tuition history within an academic year.
- Currency conversion.
