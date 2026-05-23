---
name: uni-admission-export
description: Export crawled program data to Excel/CSV, or query the database for previewing what's stored. Use when the user says "导出", "下载", "Excel", "CSV", "export to file", or asks "数据库里有什么". Triggers on "导出 hku 的数据", "把利兹的程序导出来", "export Manchester programs as Excel".
---

# uni-admission-export — Export & Preview Stored Data

You arrived here from **[[using-uni-admission-agent]]**. If not, go back first.

This skill covers reading data out of the local store — either to a file (Excel/CSV) or in-conversation as a preview table.

---

## Decide: export or preview?

| User phrasing | Action |
|---|---|
| "导出", "下载", "保存到文件", "Excel", "CSV" | Export to file (Step A) |
| "看看", "预览", "数据库里有什么", "show me what we have" | Preview in-conversation (Step B) |
| Both? | Do preview first; if non-empty, offer export |

---

## Step A — Export to file

### A.1 Required parameters

- **University slug** (required) — see [[using-uni-admission-agent]] glossary
- **Year** (optional) — omit to export all years for that university
- **Output path** (optional) — default `./<slug>-<year>-programs.xlsx`

### A.2 Run

```bash
adm-agent export \
  --university <SLUG> \
  --year <YEAR> \
  --output <OUTPUT_PATH>
```

Output is an Excel workbook with one sheet per year (or single sheet if year filter applied). Columns:

`name_en, name_zh, faculty, program_group_code, study_options, deadlines, tuition_amount, currency, requirements, source_url, last_updated`

JSONB columns (`study_options`, `deadlines`, `requirements`) are flattened to human-readable strings (e.g., "Full-Time / 12 months | Part-Time / 24 months").

### A.3 Report to user

```
✅ 导出完成 — <university> <year|all>

  文件:    <OUTPUT_PATH>
  程序数:  <N>
  Sheet:   <SHEET_NAMES>
```

If `N == 0`, don't create the file. Tell the user:

> 数据库里 <university> 没有 <year> 的程序。要先跑 [[uni-admission-crawl]] 吗？

---

## Step B — Preview in conversation

Use the REST `/programs` endpoint for a quick look:

```bash
curl -sS "http://127.0.0.1:8910/programs?univ_slug=<SLUG>&year=<YEAR>" | jq '.programs[] | {name_en, faculty, tuition_amount, currency}'
```

For a wide preview, omit `jq` and show the raw first 5 records.

**When to truncate**: if more than 20 programs, show first 10 + last 3 + a count line ("…还有 N 个程序未显示"). Don't dump 200 records into the chat.

---

## Common follow-ups

| User says | Do this |
|---|---|
| "这个程序的 requirements 是什么" | Hit `/programs/<id>` for the full record, format as a list |
| "导出多个大学一起" | Loop the export command per slug; merge files only if user explicitly asks. |
| "我要 CSV 不要 Excel" | `--output foo.csv` — the CLI auto-detects format by extension |
| "数据是不是过期了" | Show `last_updated` column; if > 30 days old, suggest a re-crawl |

---

## What you must NOT do

- Don't run `adm-agent export` without a slug — there's no "export everything" mode and trying creates an unhelpful empty file.
- Don't show raw JSON dumps to the user as the final answer — always format as a table or summary.
- Don't suggest opening the SQLite file directly with `sqlite3` CLI — the schema has joins (study_options, requirements tables) that need formatting. Use `adm-agent export` or `/programs` instead.
