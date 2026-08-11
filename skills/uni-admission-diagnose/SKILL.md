---
name: uni-admission-diagnose
description: Investigate failed or low-quality crawls — quarantine entries, audit funnel drilldowns, pagination stop reasons — and run destructive cleanup (clear quarantine/diagnostics, batch-delete programs, full db-reinit). Use when the user reports a crawl problem, asks why something failed, or after a crawl finishes with anomalous stop_reason (url_drift / decreasing_yield / quality_failed) or non-empty quarantine. Also use for "删除 / delete / 清空 <university>'s data". Triggers on "上次爬失败", "为什么没爬到", "quarantine 里有什么", "诊断", "数据有问题", "删除利兹的数据", "delete programs for".
---

# uni-admission-diagnose — Investigate Crawl Failures

You arrived here from **[[using-uni-admission-agent]]**. If not, go back — there's shared glossary (stop_reason, quarantine, audit funnel) you need to know first.

This skill helps the user understand *why* a crawl underperformed or what's hidden in failed records. It does **not** fix the underlying website / scraper issue — fixes happen at the codebase level, not via the agent.

---

## Step 1 — Classify the symptom

Ask (or infer from prior conversation):

| Symptom | First command to run |
|---|---|
| Crawl finished but with `0 programs imported` | `adm-agent audit list --university <SLUG> --year <YEAR>` (see Step 2 for drilling into the row it returns) |
| Crawl finished with `stop_reason=url_drift` / `decreasing_yield` / `quality_failed` | `adm-agent audit list ...` then `adm-agent quarantine list ...` |
| User says "数据看着不对" / "name 是错的" | `adm-agent quarantine list --university <SLUG> --year <YEAR>` |
| User can't recall task id / which university | `adm-agent crawl-summary --university <SLUG>` (omit year for all) |

---

## Step 2 — Audit funnel drilldown

Two commands, in order — `audit drill` takes a row **id**, not a
university/year filter, so you always list first:

```bash
adm-agent audit list --university <SLUG> --year <YEAR>
# each line starts with "[<ID>] <slug> <year>  raw=X → filtered=Y → ..." —
# newest first, so the row you usually want is the FIRST line printed.

adm-agent audit drill <ID>
```

`audit list` alone already shows the funnel counts (raw → filtered →
candidates → extracted) per row — often enough to answer "where did it
drop" without `drill` at all. Only run `drill` when you need the actual
dropped URLs (e.g. to check whether a specific programme got filtered out).

The funnel has four stages: **raw → filtered → candidates → extracted**. Where the drop happens tells you the failure mode:

| Drop location | Likely cause | What to tell the user |
|---|---|---|
| `raw=0` | Index page returned no HTML (anti-bot, JS-required) | "页面拉不到内容——可能反爬或全 JS 渲染。试试 client browser_provider。" |
| `raw=N, filtered=0` | Filter rejected every candidate (taxonomy too strict?) | "候选 link 全被 taxonomy 过滤掉了。检查 candidate_taxonomy_filter_threshold。" |
| `filtered=N, candidates=0` | URL pattern detection failed | "找不到 detail 页面 pattern。是不是给错了 index URL？" |
| `candidates=N, extracted=0` | LLM 抽取全失败 | "LLM 抽不出结构化字段——看 quarantine 里的原因。" |
| `extracted=N` but data wrong | LLM 抽到了但内容错 | Run `quarantine list` and look at `name_suspect` / `taxonomy_mismatch` reasons. |

---

## Step 3 — Quarantine analysis

```bash
adm-agent quarantine list --university <SLUG> --year <YEAR>
```

Aggregate by `reason` field. Top patterns:

| Reason | What it means | Fix-path |
|---|---|---|
| `NO_MARKDOWN` | Crawler got no usable text (JS-only page, blocked) | Try `browser_provider=client` (Playwright stealth) |
| `EXTRACTION_FAILED` | LLM返回了空/不可解析的 JSON | One-off — usually fine. If > 50% of records: check LLM provider quota / model name. |
| `NAME_SUSPECT` | Extracted name looks like noise ("Faculty of X", "About us") | Self-critique didn't recover — the page genuinely doesn't have program info. Filter out the URL. |
| `TAXONOMY_MISMATCH` | Name doesn't fit any known taxonomy bucket | Either rare-but-real program or junk. Manual review. |

For each top reason, quote 2-3 example records (truncate `extra_payload` to first 200 chars) so the user can see the actual failure shape.

---

## Step 4 — Pagination stop signals

If the crawl was paginated and stopped early, the reason is in the summary:

```bash
adm-agent crawl-summary --university <SLUG> --year <YEAR>
```

- **`url_drift`** — pagination URL stopped matching the index pattern. Usually means: the site moved you to a search results page, an unrelated catalog, or a 404 silently. Show the user the last 3 URLs visited (via `audit list` then `audit drill <ID>`, per Step 2) — the drift point is obvious.
- **`decreasing_yield`** — 3+ consecutive pages with steeply-falling program counts. Often legitimate end-of-list; sometimes pagination loop bug. Verify by spot-checking one of the "near-empty" pages manually.
- **`quality_failed`** — quality gate tripped. Look at the per-page extracted counts vs raw counts. If raw is high but extracted near zero, the LLM is choking on the format.

---

## Step 5 — Cleanup commands

If the user wants to start fresh:

| Command | Effect |
|---|---|
| `adm-agent quarantine clear --university <SLUG>` | Drop quarantine entries for one university (irreversible) |
| `adm-agent diagnostics clear --university <SLUG>` | Wipe quarantine + audit + stop_reason rows in one shot (does NOT touch the `program` table) |
| `adm-agent programs delete --university <SLUG> [--year <YEAR>]` | Batch-delete actual imported programs (and their child rows). **Without `--yes` it only previews the count — run it once unconfirmed first and show the user the preview before adding `--yes`.** |
| `adm-agent db-reinit --yes` | **DESTRUCTIVE** — drops and recreates the whole database (every university, every table), then migrates to head. Confirm twice with the user. |

`quarantine clear` / `diagnostics clear` / `programs delete` are scoped to
one university (and optionally one year); `db-reinit` is the only one that
wipes everything — reach for the narrowest command that fixes what the user
actually asked for.

Never run any of these with `--yes` without explicit confirmation in the
current message — past `--yes` flags from earlier conversations don't
count. For `programs delete` specifically, always run the unconfirmed
preview first (it's non-destructive by design) and quote its count to the
user before ever adding `--yes`.

---

## What you must NOT do

- Don't blame the LLM provider when raw=0 — that's a fetch problem, not a model problem.
- Don't suggest code changes; this skill is read-only diagnosis. If the user wants a code fix, point them at the GitHub repo's issue tracker.
- Don't auto-clear quarantine without showing the entries first. The user might want to review.
- Don't recommend `db-reinit` as a "fix" — it's a developer reset, not a recovery tool.
- Don't run `programs delete --yes` straight off — always run it unconfirmed first (it previews the affected count and does nothing) and get explicit confirmation on the actual number before adding `--yes`.
- Don't reach for `db-reinit` (wipes every university) when the user only wants one university's data gone — that's `programs delete --university <SLUG>`.
