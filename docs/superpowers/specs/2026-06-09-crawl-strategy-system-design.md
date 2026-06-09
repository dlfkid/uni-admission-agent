# Crawl Strategy System — Design Spec

**Date:** 2026-06-09
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/crawl-strategy-system`

## Problem

Given a university programme **index URL**, the project must:

1. **Known universities** (those with a golden sample) → stably produce
   high-quality crawl results.
2. **Unknown universities** → use an LLM to classify the index page's
   layout type and apply an appropriate strategy to crawl both program
   **names** and **details**.
3. **Un-crawlable pages** → faithfully capture the *phenomenon* (params +
   logs + raw page) and export it as a zip, so a senior developer LLM can
   later analyze/reproduce it offline and author a new strategy + golden
   sample.
4. The **plugin must be operable by low-context / low-reasoning LLMs** —
   not only top-tier models (Gemini Pro / GPT-5.5 / Claude Opus).

Today the project has a deterministic names-only harvest covering four
page structures (heading-link, inline-degree, merged-columns, blob), but
it has **no layout classifier and no dispatch** — a structurally-new
university (e.g. NUS) yields 0 and the operator is left blind.

## Core principle: all intelligence lives inside the tool

Classification, strategy selection, and any LLM analysis happen **server
-side inside `adm-agent`**, using the tool's own configured LLM provider.
The plugin skill is a **thin wrapper**: the driving agent (which may be a
weak model) only runs `adm-agent crawl <url>`, reads a `status`, and
relays a tool-provided one-line message. This is what satisfies
requirement #4.

---

## Architecture: Strategy Registry + Classifier + Dispatcher

Two independent axes, discovered empirically across 5 universities:

### Module 1 — Strategy abstraction + registry

```
FetchStrategy  (how to obtain the page content)
  server       crawl4ai headless          (static / unprotected — Leeds)
  client       real Chrome via CDP        (Cloudflare, basic JS — UCL, Manchester)
  client_wait  client + wait-for-selector / network-idle / scroll
                                          (async JS apps — NUS)
  api          hit the backend JSON API   (known-API sites; developer-authored)

ExtractStrategy  (how to pull {name, detail_url} from content)
  heading_link   ## [Name MSc](url)        Leeds, Edinburgh
  inline_degree  [Name BSc](url)           UCL
  merged_columns [Name MSc (1 year)](url)  Manchester (duration stripped)
  blob           [code|term|Name - MSc - …](url)  PolyU
  text_heading   name is heading text + a separate anchor (Learn More)  NUS
  llm            fallback: LLM extracts directly
```

- **Strategy = (FetchStrategy, ExtractStrategy) + optional params** (e.g.
  `client_wait` selector, `merged_columns` duration regex).
- Each **ExtractStrategy is an independent, unit-testable pure function**
  `content -> [{name, detail_url}]`. Its golden sample is its test fixture.
- **University Registry** (data-driven, not if/else) pins known domains to
  a proven strategy so they **never regress to LLM guessing** — this
  guarantees requirement #1:

  ```python
  REGISTRY = {
    "courses.leeds.ac.uk": Strategy(fetch="server", extract="heading_link"),
    "study.nus.edu.sg":    Strategy(fetch="client_wait", extract="text_heading",
                                    wait_selector=".programme-card"),
    ...
  }
  ```
- Adding a new university = add a registry row + a golden sample +
  (if needed) one new ExtractStrategy pure function. The orchestration
  code is never touched.

### Module 2 — Classifier + LLM fallback (unknown universities)

Deterministic-first, LLM-last, with an explicit "good enough" gate at each
step:

```
unknown index page (content already fetched)
  1. Deterministic feature classify (0 tokens)
       compute feature signals (heading_link / inline_degree / blob /
       text_heading+anchor counts, total links, nav ratio); each
       ExtractStrategy reports a match score (# course-like rows).
       top score ≥ threshold → use it. DONE.
  2. LLM classify (1 call, links/condensed-text only)
       "which layout: [heading_link, inline_degree, merged_columns, blob,
       text_heading, none]?" → run that type's deterministic extractor.
       result ≥ threshold → DONE.
  3. LLM direct-extract (still failing)
       LLM extracts name+detail_url directly → status=llm_fallback,
       export a report (req #3: signals a deterministic strategy is owed).
  4. total failure → status=unsupported, export a report.
```

**Confidence/verify gate** (prevents "extracted garbage but thought it
succeeded"): plausible count (not 0/1/all-nav), names look like programs
(pass `is_noise_program_name`, reasonable degree-word/length ratio),
detail URLs same-domain and detail-like.

The LLM only ever does a **constrained 6-way classification** (reliable
even for weak models) or a last-resort extraction that always triggers a
report. It never "silently makes do" with the LLM.

### Module 3 — Fetch escalation ladder

Known universities use the registry-pinned fetch and skip escalation.
Unknown universities auto-escalate, cheapest first, with a uniform
"is the content usable?" gate between levels:

```
1. server      → usable? (links / degree-word hits / body length above floor;
                  NOT a Cloudflare "just a moment" page) → classify; else escalate
2. client      → usable? → classify; else escalate
3. client_wait → usable? → classify; else status=unsupported + report
```

- Escalation is capped at `client_wait`.
- `api` is **not** in the auto-ladder (discovering an API needs offline
  network analysis) — a developer authors an `api` strategy + registry
  row after seeing a report; auto path stays simple.
- Content-usable gate is a shared function reusing Module 2's signals;
  Cloudflare-challenge fingerprint short-circuits to "not usable".
- Honest boundary: pure-canvas / shadow-DOM / login-walled pages that
  even `client_wait` can't surface → `unsupported` + report.

### Module 4 — Detail crawl pipeline

Strategy yields `[{name, detail_url}]`; each detail page is crawled to
fill the remaining structured fields, reusing the existing pipeline
(quality gate + the fixes already merged).

**Iron rule: the index name is authoritative — never re-extract the name
from the detail page body.** (This is the root cause of the historical
"A bachelor degree with a 2:1 (hons)" garbage: requirement prose in the
detail body outranked the title.) Detail extraction fills
tuition/requirements/deadlines/study-options/faculty only; `name_en`
stays the index name.

- `catalog_key` uses `source_url` (already merged) so same-named courses
  don't collapse.
- `quality_gate` quarantines bad detail fields; the name itself can't be
  wrong.
- **Two-phase, separable**: `crawl --names-only` (index only, seconds,
  zero detail cost) runs first; `crawl` (with details) fills fields page
  by page, with concurrency limits + existing pagination stop signals.
- Detail fetch reuses the university's chosen FetchStrategy (Cloudflare /
  JS sites need `client` for detail pages too).
- For decoupled layouts (NUS "Learn More"), the `text_heading`
  ExtractStrategy pairs each name with its detail URL at extraction time,
  so `{name, detail_url}` already carries the association.

### Module 5 — Reporter (phenomenon capture only)

**Responsibility split:** the runtime (weak execution/analysis LLMs) only
**records the phenomenon** — params + logs + raw page — and exports a zip.
It does **not** diagnose or guess a strategy. A **senior developer LLM**
(e.g. Opus, offline) consumes the zip to analyze, reproduce, author the
strategy, and add the golden sample.

```
<report-out>/<domain>-<timestamp>.zip
  index.html     raw fetched page (reproducible)
  index.md       converted markdown
  params.json    objective runtime record — NOT conclusions:
                 { index_url, university_guess, timestamp, tool_version,
                   fetch_level_used, fetch_levels_tried,
                   content_signal{chars,links,degree_hits,nav_ratio},
                   feature_signals{per-strategy hit counts},
                   strategy_scores{per-strategy extracted counts},
                   llm_classified_as, llm_extract_count, outcome }
  run.log        full log of this run (escalation steps, what happened)
```

- No "what the developer should do" prose (that is analysis — left to the
  developer LLM). `params.json` records phenomena (e.g. each strategy
  scored 0), not conclusions (never "looks like text_heading").
- Default location `~/.uni-agent/reports/`; `--report-out <path>` overrides.
- Local-only, never auto-transmitted (send-before-publish principle).
- The weak agent merely triggers capture and relays the zip path.

### Module 6 — Weak-agent CLI / skill interface

All complexity is inside `adm-agent`. The skill is a thin wrapper; the
weak agent only runs a command, reads `status`, and relays a one-liner.

```bash
adm-agent crawl <index_url> --names-only        # default first pass
adm-agent crawl <index_url>                      # with details
adm-agent crawl <index_url> --report-out <path>  # zip destination
```

Command returns structured JSON (no agent reasoning needed):

```json
{ "status": "ok|llm_fallback|unsupported",
  "university": "leeds", "names_count": 15,
  "details_imported": 0, "quarantined": 0,
  "strategy_used": "server×heading_link",
  "report_zip": null,
  "message_for_user": "成功抓取 15 门课程名字。" }
```

The tool emits `message_for_user`; the weak agent relays it verbatim.
The `uni-admission-crawl` skill becomes a decision table:

| status | weak agent action |
|---|---|
| `ok` | relay `message_for_user` + the name list |
| `llm_fallback` | relay "got it via the generic path; please send the report to the developer to add a proper strategy" + `report_zip` |
| `unsupported` | relay "this university isn't supported yet; a phenomenon report was exported to `<report_zip>` — send it to the developer" |

The weak agent never touches page analysis, strategy selection, LLM calls,
or report contents.

### Module 7 — Testing + acceptance

**Deterministic unit tests** (no network / no LLM / no browser):
- Each ExtractStrategy pure function against its golden sample fixture.
- Classifier: feed each golden's content, assert correct strategy + the
  confidence gate.
- Fetch-usable gate: feed Cloudflare-challenge / empty / normal samples,
  assert the escalation decision.
- Reporter: assert zip structure + `params.json` field completeness.

**Integration / acceptance milestones:**
1. **#1**: 4 golden universities (registry-pinned) reproduce their
   validated name lists (regression guard).
2. **#2**: a university structurally like a known type but not in the
   registry → classifier matches → deterministic extraction (proves
   generalization without LLM).
3. **#3 + NUS loop**: run against NUS → correctly exports
   `study.nus.edu.sg-*.zip` (real index.html + objective params/logs) →
   developer authors NUS `text_heading` + `client_wait` strategy + golden
   sample → NUS becomes known and stably crawlable.
4. **#4**: skill is a thin wrapper; decision table covers all 3 statuses;
   weak agent does zero page reasoning.

**CI gate:** deterministic unit tests + pylint 10/10 + no regression of
the existing suite. Network/browser/LLM integration is validated manually
(as `naming_smoke` is today).

---

## Project delivery boundary

The project's job for un-crawlable pages ends at **correctly exporting the
phenomenon zip**. Analysis, reproduction, strategy authoring, and golden
sample creation are done by a senior developer LLM offline. NUS is the
first real exercise of this loop and the acceptance case for the reporter.

## Out of scope (this spec)

- Auto-filing GitHub issues / any network side-effect from the reporter.
- Pagination/filter completeness for sites that show a partial list
  (separate concern; tracked independently).
- The `api` FetchStrategy auto-discovery (developer-authored only).
