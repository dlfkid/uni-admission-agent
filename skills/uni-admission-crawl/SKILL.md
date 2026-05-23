---
name: uni-admission-crawl
description: Crawl university admission/program data using the adm-agent tool. Use when the user asks to scrape, crawl, extract, or fetch course lists, program details, tuition fees, or application deadlines from a specific university website. Triggers on phrases like "抓取大学课程", "爬取招生数据", "crawl university programs", "extract course data from <URL>".
---

# University Admission Data Crawler — Operator Skill

You are operating the **adm-agent** tool (https://github.com/dlfkid/uni-admission-agent). It is a Python service that crawls university websites, extracts admission/program data with LLM assistance, and stores results in a local SQLite database (Postgres is also supported via `DATABASE_URL` for advanced users). This skill teaches you how to drive it end-to-end and report results back to the user.

## When to use

Activate this skill when the user wants to extract structured program / course / admission data from a university website. Typical phrases:

- "帮我抓取 XXX 大学的硕士课程，入口是 <URL>"
- "用 adm-agent 爬取这个 index 页：<URL>"
- "crawl Leeds masters courses from <URL>"
- "extract all programs on this page"

Do NOT use this skill for: scraping non-university sites, modifying the adm-agent codebase itself, or general web scraping unrelated to admission data.

---

## Step 1 — Preflight: is the tool ready?

Before crawling, verify three things:

### 1.1 Backend is running

```bash
curl -sS --max-time 3 http://127.0.0.1:8910/health
```

- ✅ Returns `{"status":"ok",...}` → backend up, continue.
- ❌ Connection refused / timeout → backend is down. Tell the user:
  > 后端服务没启动。请在另一个终端运行 `adm-agent up`（会同时拉起 host 和 client），起来后告诉我，我继续。
  
  Do **not** try to start it yourself in the background — `adm-agent up` is a foreground process that should run in the user's terminal so they can see logs and Ctrl+C cleanly.

### 1.2 Database schema is up to date

```bash
adm-agent db-version
```

- ✅ "Database schema is up to date." → continue.
- ⚠️ "Migrations pending" → run `adm-agent db-migrate --yes` first.

### 1.3 LLM provider is configured

If the user mentions a specific provider, verify the API key is in `.env`. Otherwise just continue — `adm-agent check` will surface config issues if any.

---

## Step 2 — Determine the crawl mode

Three patterns based on what the user gave you:

| User intent | Mode | Command |
|---|---|---|
| Single program detail page (e.g., "MSc Finance" specific page) | `detail` | `adm-agent crawl ... --page-type detail` |
| One index page (program list, no pagination needed) | `index` | `adm-agent crawl ... --page-type index` |
| Multi-page paginated index | `paginate` | REST `/agent/run` with `auto_paginate=true` |

**Rules**:
- If the URL clearly looks like a single program (slug contains a degree code like `msc-finance` or `accounting-bsc`), use `detail`.
- If the URL is a course-search or program-list page AND the user only asked for "this page" / "first page" / "what's on this page", use `index`.
- If the user says "all programs", "every page", "complete list", "全部" — use `paginate`.
- When in doubt, **ask the user** which mode they want and what `max_pages` cap they're comfortable with.

---

## Step 3 — Execute the crawl

### 3.1 Detail or single index mode

```bash
adm-agent crawl \
  --name <UNIVERSITY_SLUG> \
  --year <ACADEMIC_YEAR> \
  --url '<URL>' \
  --page-type <detail|index> \
  --continue 0
```

The command runs synchronously and prints token usage + `0 programs imported` or `N programs imported` at the end.

### 3.2 Paginated index mode

For multi-page index, hit the REST API directly:

```bash
curl -sS -X POST http://127.0.0.1:8910/agent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "<INDEX_URL>",
    "univ_slug": "<UNIVERSITY_SLUG>",
    "year": <ACADEMIC_YEAR>,
    "page_type_hint": "index",
    "auto_paginate": true,
    "max_pages": <N>
  }'
```

The response contains `task_id`. Poll status:

```bash
# Wait for the task to finish (poll every 10s up to ~30 min).
for i in $(seq 1 180); do
  state=$(curl -sS "http://127.0.0.1:8910/tasks/<TASK_ID>" | jq -r '.state')
  case "$state" in
    DONE|FAILED|CANCELLED) echo "Final state: $state"; break ;;
  esac
  sleep 10
done
```

⚠️ **Budget warning**: Paginated crawls call the LLM once per detail page on the FIRST page (afterwards selectors are reused via SchemaLearner where applicable). For a page with 20 programs and 5 pages, expect 30-100 LLM calls and 5-15 minutes. Tell the user the expected scale BEFORE starting.

---

## Step 4 — Monitor progress

While the crawl runs, you can tail events (for paginated mode):

```bash
curl -sN "http://127.0.0.1:8910/tasks/<TASK_ID>/events"
```

Watch for these events that matter:
- `pagination_progress` — "page X/Y, programs so far: N"
- `quality_check_passed` / `quality_check_failed` — quality circuit breaker firing
- `pagination_stopped` with `reason=url_drift` or `decreasing_yield` — early stop fired

If a `quality_check_failed` or `pagination_stopped` event fires, that's not a crash — it means the system intelligently stopped. Mention it in your report.

---

## Step 5 — Report results

After the crawl finishes, run the dedicated summary command:

```bash
adm-agent crawl-summary --university <UNIVERSITY_SLUG> --year <YEAR>
```

This prints a structured block with:
- Funnel: raw → filtered → candidates → extracted
- Quarantine count + breakdown by reason
- `stop_reason` (with ⚠️ if anomalous: `url_drift`, `decreasing_yield`, `quality_failed`)
- Recovered count (if critique retry kicked in)

**Quote this verbatim to the user**, then add a one-sentence plain-language interpretation:

| stop_reason | What to say |
|---|---|
| `exhausted` | "正常爬完了所有检测到的页面。" |
| `max_pages` | "命中了 max_pages 上限——如果还有更多程序需要抓取，可以提高这个值再跑一次。" |
| `url_drift` | "⚠️ 检测到 URL 跳到了无关页面（不在 index pattern 内），自动停了。建议你检查下入口 URL 是否正确。" |
| `decreasing_yield` | "⚠️ 后几页几乎没新程序了，可能已经爬完——也可能是分页规则有问题。看看 audit 里最后几页的 extracted 数。" |
| `quality_failed` | "⚠️ 数据质量门挡下来了——可能 LLM 抽取出了一批垃圾。建议跑 `adm-agent quarantine list --university <slug>` 查具体失败原因。" |

If there are quarantine entries, also run:

```bash
adm-agent quarantine list --university <UNIVERSITY_SLUG> --year <YEAR>
```

…and summarize the top 3 most common reasons for the user.

---

## Troubleshooting cheatsheet

| Symptom | Cause | What to tell user |
|---|---|---|
| `curl: connection refused` on /health | Backend down | "请运行 `adm-agent up`" |
| `0 programs imported` and stop_reason is null | Page wasn't crawlable (anti-bot, JS-rendered, etc.) | Check `adm-agent quarantine list` for `extraction_failed` / `no_markdown` entries |
| Task stuck in RUNNING for > 30 min | Likely hit a rate limit or browser hang | Tell user to check server logs; offer to cancel via `POST /tasks/<id>/cancel` |
| `migration pending` | DB schema behind code | Run `adm-agent db-migrate --yes` |
| University slug invalid error | Slug must match `^[a-z0-9-]+$` | Suggest a valid slug (e.g., "leeds" not "Leeds") |

---

## Output format

When done, your final message to the user should look like:

```
✅ 抓取完成 — <university> <year>

  抓取入口: <URL>
  漏斗:    raw=X → filtered=Y → candidates=Z → extracted=N
  Quarantine: M 条 ({reason: count, ...})
  停止原因: <stop_reason>  [⚠️ if anomalous]
  耗时:    ~T 分钟
  
{一句话人话解读 — 见 Step 5 表格}
{如有 quarantine：top 3 失败原因 + 建议下一步}
```

Keep it short and skimmable — user might be reading it at 2 AM.
