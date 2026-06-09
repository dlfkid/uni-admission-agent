---
name: uni-admission-crawl
description: Execute a crawl against a specific university URL — single-page, single-index, or paginated. Use only AFTER [[using-uni-admission-agent]] has classified the user's intent as a crawl request and preflight has passed. Triggers on "抓取/爬取 <university> <URL>", "crawl programs from <URL>", "extract course data".
---

# uni-admission-crawl — Execute a Crawl

You arrived here from **[[using-uni-admission-agent]]** with preflight already passed. If you didn't, stop and start over there — there's shared glossary and conventions you need first.

This skill takes the user from "I want data from <URL>" to "here are the results". It does **not** cover installation, failure diagnosis, or export — those are sibling skills.

---

## Step 1 — Determine crawl mode

Three patterns based on the URL and user phrasing:

| Mode | When to use | Command |
|---|---|---|
| `detail` | URL is one specific program (slug contains a degree code like `msc-finance`, `accounting-bsc`) | `adm-agent crawl ... --page-type detail` |
| `index` | URL is a program-list / course-search page **and** user only asked for "this page" / "first page" | `adm-agent crawl ... --page-type index` |
| `paginate` | User said "all programs" / "every page" / "complete list" / "全部" | REST `POST /agent/run` with `auto_paginate=true` |

**When in doubt, ask** which mode they want and what `max_pages` cap they're comfortable with. Don't guess on paginated crawls — they cost the most.

---

## Step 2 — Get the user-side parameters

Before invoking the tool, you need:

- **University slug** — must match `^[a-z0-9-]+$` (see [[using-uni-admission-agent]] glossary). Ask if not provided.
- **Year** — academic year (e.g., `2026`). Default to current year if user didn't specify.
- **Entry URL** — verbatim from the user; never invent or normalize.
- **For paginated mode**: confirm `max_pages` upper bound. Default to 10 if user doesn't care.

---

## Step 3 — Execute

### 3.1 Detail / single-index mode

```bash
adm-agent crawl \
  --name <SLUG> \
  --year <YEAR> \
  --url '<URL>' \
  --page-type <detail|index> \
  --continue 0
```

Runs synchronously. Final line will say `N programs imported` or `0 programs imported`.

### 3.2 Paginated mode

```bash
curl -sS -X POST http://127.0.0.1:8910/agent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "<INDEX_URL>",
    "univ_slug": "<SLUG>",
    "year": <YEAR>,
    "page_type_hint": "index",
    "auto_paginate": true,
    "max_pages": <N>
  }'
```

Response includes `task_id`. **Always tell the user the expected cost before kicking off a paginated job**:

> 这是分页爬取，预计会调用 LLM ~{20 × max_pages} 次，耗时 ~{2 × max_pages} 分钟。开始吗？

---

## Step 4 — Monitor (paginated only)

Stream events:

```bash
curl -sN "http://127.0.0.1:8910/tasks/<TASK_ID>/events"
```

Events worth surfacing to the user as they arrive:

- `pagination_progress` — "page X/Y, programs so far: N" (every page)
- `quality_check_failed` — quality circuit breaker fired
- `pagination_stopped` with `reason` field — early stop fired (not a crash, intentional)

For terminal state, poll until `state in {DONE, FAILED, CANCELLED}`:

```bash
for i in $(seq 1 180); do
  state=$(curl -sS "http://127.0.0.1:8910/tasks/<TASK_ID>" | jq -r '.state')
  case "$state" in
    DONE|FAILED|CANCELLED) break ;;
  esac
  sleep 10
done
```

---

## Step 5 — Report

Run the dedicated summary command:

```bash
adm-agent crawl-summary --university <SLUG> --year <YEAR>
```

It prints a structured block with the audit funnel, quarantine count, `stop_reason`, and recovered count. **Quote it verbatim** to the user, then add a one-sentence interpretation:

| `stop_reason` | Plain-language interpretation |
|---|---|
| `exhausted` | "正常爬完了所有检测到的页面。" |
| `max_pages` | "命中了 max_pages 上限——还想要更多就提高这个值再跑。" |
| `url_drift` | "⚠️ URL pattern 跑偏了，自动停了。检查下入口 URL 是否对。" |
| `decreasing_yield` | "⚠️ 后几页几乎没新程序——可能爬完了，也可能分页规则错了。" |
| `quality_failed` | "⚠️ 数据质量门挡了一批垃圾输出。建议跑 [[uni-admission-diagnose]] 看具体失败。" |

**If there's quarantine output**, don't dig into it here — that's [[uni-admission-diagnose]]'s job. Just say:

> 有 N 条进了 quarantine，要看具体失败原因吗？

…and route to diagnose if they say yes.

---

## Final message format

```
✅ 抓取完成 — <university> <year>

  入口:        <URL>
  漏斗:        raw=X → filtered=Y → candidates=Z → extracted=N
  Quarantine:  M 条
  停止原因:    <stop_reason>  [⚠️ if anomalous]
  耗时:        ~T 分钟

{一句话解读}
{如有 quarantine：提示可以诊断}
```

Skimmable. Short. The user might be reading at 2 AM.

---

## Things you must NOT do

- Don't invent URLs the user didn't provide.
- Don't normalize the slug (e.g., don't lowercase if user already lowercased — just validate the regex).
- Don't run a paginated crawl without telling the user the cost estimate first.
- Don't debug a failed crawl here — route to [[uni-admission-diagnose]].
- Don't try to start the server if /health is down — route to [[uni-admission-install]] §"Start an existing install".

## Strategy-based crawl (preferred entry)

Run the tool — it classifies the page and picks a strategy itself. You do
NOT analyze the page or choose a strategy.

```bash
adm-agent crawl-index '<INDEX_URL>' --json
```

Read `status` from the JSON and act per this table. Relay the tool's
`message_for_user` verbatim — do not write your own analysis.

| status | what you do |
|---|---|
| `ok` | Relay `message_for_user`, then list the names. |
| `llm_fallback` | Relay `message_for_user`. Tell the user the result came via the generic path and the report at `report_zip` can be sent to the developer to add a proper strategy. |
| `unsupported` | Relay `message_for_user`. The phenomenon report was exported to `report_zip` — tell the user to send that file to the developer to add support. |

Never open or interpret the report's contents; that is the developer's job.

### Crawl range (how many to fetch)

The caller chooses how much to crawl; the tool paginates and auto-stops.

| Want | Command |
|---|---|
| Default (first batch, ≤30) | `adm-agent crawl-index <url>` |
| First N | `adm-agent crawl-index <url> --limit N` |
| Everything (safety-capped) | `adm-agent crawl-index <url> --all` |

The result JSON carries `pages_fetched` and `stopped_reason`
(`reached_limit` / `exhausted` / `unusable` / `no_growth` / `safety_cap`).
Relay `message_for_user` verbatim — it already explains why crawling stopped.
