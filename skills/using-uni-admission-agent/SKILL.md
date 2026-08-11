---
name: using-uni-admission-agent
description: Entry point for the uni-admission-agent plugin. Use when the user wants to crawl, scrape, extract, fetch, install, diagnose, or export university admission / program / course / tuition / deadline data — or any operation involving the adm-agent tool. Triggers on phrases like "抓取大学课程", "爬取招生数据", "crawl university programs", "install adm-agent", "导出 Excel", "上次爬取失败".
---

# uni-admission-agent — Router Skill

You are operating the **adm-agent** crawler tool (https://github.com/dlfkid/uni-admission-agent). It extracts structured admission / program / course / tuition / deadline data from university websites, with LLM-assisted parsing. Results land in a local SQLite database by default (Postgres optional).

This is the **router skill**. It does two things:

1. **Preflight** — figure out whether the tool is even installed and running.
2. **Route** — pick the right sub-skill based on the user's intent and current system state.

**Always start here.** Never jump straight into a sub-skill without checking preflight first — half of "the crawler isn't working" reports turn out to be "the server isn't running".

---

## Preflight (run silently before routing)

```bash
# 1. Is the CLI installed?
command -v adm-agent >/dev/null && echo "cli=ok" || echo "cli=missing"

# 2. Is the server running?
curl -sS --max-time 3 http://127.0.0.1:8910/health >/dev/null && echo "server=ok" || echo "server=down"
```

Outcomes:

| `cli` | `server` | Action |
|---|---|---|
| missing | * | Route to **[[uni-admission-install]]** — the tool isn't installed. |
| ok | down | Tell the user: *"后端没启动，我帮你拉起来吗？"* If yes → route to [[uni-admission-install]] §"Start an existing install". |
| ok | ok | Proceed to **Routing** below. |

Do **NOT** run `adm-agent up` or any server-starting command in the background yourself — it's a foreground process. Always defer to [[uni-admission-install]] for lifecycle ops.

---

## Routing

Once preflight passes, pick the sub-skill that matches the user's intent:

| User intent (examples) | Sub-skill |
|---|---|
| "抓取 / 爬取 / extract / crawl" + university name or URL | **[[uni-admission-crawl]]** |
| "上次爬失败了 / quarantine / 数据质量 / 检查失败 / why did it fail" | **[[uni-admission-diagnose]]** |
| "删除 / 清空 <university> 的数据 / delete programs for" | **[[uni-admission-diagnose]]** (Step 5 — cleanup commands) |
| "导出 / Excel / CSV / 下载数据 / export" | **[[uni-admission-export]]** |
| "安装 / 升级 / 启动 / 重装 / install / upgrade / restart" | **[[uni-admission-install]]** |
| Mixed: "帮我爬利兹大学" but cli=missing | Install **first**, then auto-continue to crawl |

**When the request mixes phases**, do them sequentially in the same conversation — don't ask the user "ok do you want to crawl now?" after install completes. Just do it.

**When the request is ambiguous** (e.g., "看看这个工具能干嘛"), respond directly with a short menu — don't load any sub-skill yet.

---

## Shared glossary

These terms appear across all sub-skills. Internalize them so you don't have to re-explain:

- **slug** — short URL-safe university identifier, regex `^[a-z0-9-]+$`. Examples: `hku`, `leeds`, `manchester`. Never use display names like "The University of Hong Kong" as slug.
- **index page** — a page that lists multiple programs (course catalog, search results).
- **detail page** — a page for one specific program (e.g., "MSc Finance" entry page).
- **paginated crawl** — multi-page index walk; auto-stops via signals (`url_drift`, `decreasing_yield`, `quality_failed`).
- **quarantine** — extracted records that failed quality gate. Visible via `adm-agent quarantine list`. Not deleted; kept for review.
- **audit funnel** — per-crawl counts at each pipeline stage: raw → filtered → candidates → extracted. Visible via `adm-agent audit list`; `adm-agent audit drill <ID>` (an id from that list, not a university/year filter) goes one level deeper into the actual dropped URLs for one row.
- **stop_reason** — why a paginated crawl stopped. Values: `exhausted` (normal), `max_pages` (cap hit), `url_drift` (URL pattern broke — ⚠️), `decreasing_yield` (last few pages near-empty — ⚠️), `quality_failed` (quality gate blocked — ⚠️).

---

## Conventions

- **Never invent URLs**. The user gives you the entry URL; you do not guess "this looks like a UCL program page so I'll try X".
- **Never escalate to sudo / system install** without explicit user confirmation. Even then, [[uni-admission-install]] §"manual install" must be followed verbatim.
- **All long-running tasks need progress reporting**. If you kick off a paginated crawl, poll `/tasks/<id>/events` and stream meaningful events to the user (page progress, quality checks, stop signals) — don't just sleep silently.
- **Stop and ask** when the user's request would cost > $0.50 in LLM calls or > 10 minutes of crawl time. Quote the estimate, get a yes.

---

## When you arrive at a sub-skill

Each sub-skill assumes preflight passed and the user's intent is already classified. If you arrive at a sub-skill without going through this router, **stop and load [[using-uni-admission-agent]] first** — there's shared context (preflight state, glossary, conventions) that the sub-skill counts on you having read.
