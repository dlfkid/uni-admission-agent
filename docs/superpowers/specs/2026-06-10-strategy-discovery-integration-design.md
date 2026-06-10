# Strategy Discovery → Ingestion Integration — Design Spec

**Date:** 2026-06-10
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/strategy-discovery-integration`
**Builds on:** crawl-strategy backbone (#36), pagination/range axis (#37), NUS api strategy (#38) — all merged but currently reachable only via the names-only `crawl-index` CLI command.

## Problem

The project now has **two disconnected crawl paths**:

| | Persists to DB → web UI → export? | Uses the new crawl-strategy system? | Range control |
|---|---|---|---|
| Full pipeline (`/agent/run`, CLI `crawl` → `IngestionPipeline`) | ✅ yes | ❌ no — discovery is LLM scout + link_parser | `max_pages` (pages) |
| Strategy system (`crawl-index`) | ❌ no — prints names as JSON | ✅ yes (registry/classifier/NUS-API/CrawlRange) | `--limit/--all` (programmes) |

So all the recent accuracy work (deterministic name extraction, registry-pinned
universities, the NUS 207-programme API, range control) is invisible to the
ordinary user, whose mental model is: *"I give an index URL + year + slug +
range → the project does its thing → programmes land in the DB with tuition /
requirements / study options → I see them in the web UI and can export."*

This spec connects the two: the strategy system becomes the **discovery layer**
of the full pipeline, so `/agent/run` and CLI `crawl` produce accurate,
range-controlled, DB-persisted results.

## Key insight (empirical)

`IngestionPipeline.run_new_job` **already accepts** `selected_urls` +
`selected_link_texts` ("crawl these detail URLs; these anchor texts are the
authoritative names"). The internal LLM scout is only the fallback when they
are not provided. And `crawl_strategy.crawl_index()` outputs exactly
`[{name_en, detail_url}]`. The seam already exists — no pipeline surgery needed.

Both entry points converge on one call site: `src/services/crawler.py:crawl_url`
→ `run_new_job` (used by CLI `crawl` and the `/agent/run` agent tools), so one
integration point upgrades both.

## Core decisions (from brainstorming)

- **Strategy-first, LLM-scout fallback.** Known/classifiable pages go through
  the strategy system (accurate names + URLs + NUS API). `unsupported` (or any
  discovery exception) falls back to the existing scout flow, byte-for-byte
  unchanged — arbitrary universities keep working.
- **`crawl-index` stays as-is** (a names-only preview command); ordinary users
  no longer need to know it exists.
- **Range unifies on `CrawlRange`** (first-N programmes / all), applied to the
  discovered list before detail crawling. Default `CrawlRange.default()`
  (first batch ∧ ≤30) keeps casual runs cheap.
- **NUS this round goes through the unified path**: API yields 207 × {accurate
  name, `Program_Page_Link__c`} → detail pages crawled like any other
  university. Direct field-mapping from the API payload (Type__c etc.) is a
  deferred optimization, NOT in scope.

## Architecture

### New module — `src/services/crawl_strategy/discovery.py`

```python
@dataclass
class DiscoveryResult:
    matched: bool                      # strategy produced a usable list
    link_texts: Dict[str, str]         # {detail_url: authoritative name}
    nameless_count: int                # items with a name but no detail_url
    names_total: int                   # all names found (incl. nameless)
    strategy_used: Optional[str]       # e.g. "api×json_api"
    stopped_reason: str                # reached_limit|exhausted|unusable|safety_cap
    pages_fetched: int
    report_zip: Optional[str]          # set when unsupported

def discover_candidates(
    index_url: str, crawl_range: CrawlRange, *,
    server_fetch, client_fetch, api_fetch, report_out, timestamp,
) -> DiscoveryResult:
    """Run crawl_strategy.crawl_index(); map its outcome to a DiscoveryResult.

    ok          → matched=True, link_texts from items WITH a detail_url
                  (items lacking one are counted in nameless_count, reported,
                  never persisted as empty records).
    unsupported → matched=False (report_zip carried through).
    ANY exception → caught, logged, matched=False — discovery must never
                  break a crawl that would have worked via scout.
    """
```

### Integration point — `src/services/crawler.py:crawl_url`

When the request targets an index page (`page_type_hint == "index"`, or
`"auto"` resolving to index) and discovery is enabled:

```
1. result = discover_candidates(url, crawl_range, ...)
2. result.matched:
     run_new_job(selected_urls=list(result.link_texts),
                 selected_link_texts=result.link_texts, ...)
     # pipeline crawls each detail URL; injected anchor texts are the
     # authoritative names. The page_type_hint value follows the EXISTING
     # selected_urls calling convention (the Chrome-extension flow already
     # injects this way — mirror it, verified in the implementing task).
3. not matched:
     run_new_job(... unchanged ...)                # today's scout path,
                                                   # byte-for-byte identical
```

Discovery metadata (`strategy_used`, `stopped_reason`, `pages_fetched`,
`nameless_count`, `report_zip`) is attached to the job result/summary so the
agent can relay it.

Downstream stages (`extract_structured` → `validate` → `persist`) are
untouched: the existing name-authority machinery (`selected_anchor_text` +
taxonomy resolution) already treats injected anchor texts as the source of
truth — the strategy's accurate names flow through it naturally.

### Surface — API + CLI range parameters

- `/agent/run` and `/crawl` payloads gain optional `limit: int` and
  `crawl_all: bool` (mutually exclusive; neither → default range). They map to
  `CrawlRange.of(n)` / `CrawlRange.all_()` / `CrawlRange.default()` via the
  same resolver pattern the CLI already uses.
- CLI `crawl` gains `--limit N` / `--all` (same semantics as `crawl-index`).
- Existing `max_pages`/`auto_paginate` parameters stay for the scout-fallback
  path (they govern its pagination); they are ignored on the strategy path.

### Skill — `skills/uni-admission-crawl/SKILL.md`

Rewritten user-facing flow: "give an index URL + slug + year + range → full
crawl lands in the DB → check the web UI / export". The agent passes
`limit`/`crawl_all` through `/agent/run`. Cost confirmation is restated in
**programme count** (≈ one LLM extraction per programme; `--all` on NUS ≈ 207
detail pages — always quote before launching). `crawl-index` remains documented
as the names-only preview.

## Cost guard

The range unit changed from pages to programmes — each discovered programme is
one detail-page fetch + LLM extraction. Guards:
- Default range (≤30) bounds any un-parameterized run.
- The skill must quote the estimated cost (N programmes → ~N extraction calls)
  and get a yes before `--all` or large `--limit`.
- The pipeline's existing batching/quality gates apply unchanged.

## Error handling

| Failure | Behaviour |
|---|---|
| Strategy `unsupported` | Fall back to scout; attach `report_zip` to summary (developer can still add a strategy later). |
| Discovery raises | Catch + log → scout fallback. Discovery can only upgrade a crawl, never break it. |
| Item without detail_url | Excluded from detail batch; counted in `nameless_count`; summary reports "N programmes had names but no detail link". |
| Detail page fails extraction | Existing pipeline behaviour (quarantine) — unchanged. |

## Testing

**Deterministic unit tests** (no network/LLM; injected fakes):
- `discover_candidates`: ok→matched + link_texts (URL-less items counted, not
  included); unsupported→fallback; exception→fallback; range truncation
  applied before detail crawling.
- `crawl_url` branch test: matched → `run_new_job` receives the injected
  `selected_urls`/`selected_link_texts` and scout is NOT invoked; not matched
  → `run_new_job` called with today's exact arguments (regression pin).
- API schema: `limit`/`crawl_all` validation (mutual exclusion) and mapping to
  `CrawlRange`.
- CLI: `crawl --limit/--all` resolver reuse.

**Integration / acceptance** (manual, live):
- Leeds `--limit 20`: end-to-end → 20 accurately-named programmes in the DB,
  visible in the web UI, exportable with detail fields.
- NUS `--limit 10`: API-discovered names → detail pages crawled → DB/web/export.
- An unknown university (not in registry, unclassifiable): behaves exactly as
  today (scout), confirming the fallback.

**CI gate:** full suite green + pylint 10/10.

## Out of scope

- Direct field-mapping from the NUS API payload (skip detail crawl) — deferred
  optimization.
- LLM classify/extract tier of the strategy system (parent-spec Plan 2's other
  half).
- Web UI changes — existing views already render whatever the pipeline persists.
- Removing `max_pages`/scout pagination — they remain the fallback path's
  controls.
