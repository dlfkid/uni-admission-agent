# Using adm-agent from LLM CLIs (Claude Code / Codex / Gemini CLI)

This skill lets you drive adm-agent **by talking to a coding LLM** — no need to open the Chrome extension, write `curl` commands, or remember CLI flags. Tell your LLM what to crawl, and it figures out how to start the backend, run the crawl, wait, and report back.

---

## 1. Install the skill

Pick the section matching your LLM CLI. The skill file is `SKILL.md` in this directory.

### Claude Code

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/uni-admission-crawl" ~/.claude/skills/uni-admission-crawl
```

Now `claude` will auto-discover the skill on next startup.

### Codex CLI

```bash
mkdir -p ~/.codex/skills
ln -s "$(pwd)/skills/uni-admission-crawl" ~/.codex/skills/uni-admission-crawl
```

### Gemini CLI

```bash
mkdir -p ~/.gemini/skills
ln -s "$(pwd)/skills/uni-admission-crawl" ~/.gemini/skills/uni-admission-crawl
```

> If your CLI uses a different skill directory, check its docs. The skill file format is plain markdown — it should work anywhere skills are supported.

---

## 2. Make sure adm-agent is installed

Before invoking the skill, the LLM needs the `adm-agent` binary on PATH (or `uv run` inside the repo). See the main repo README for installation. Quick test:

```bash
adm-agent --version
```

---

## 3. Prompt templates — copy, replace placeholders, paste

The LLM is smart enough to do everything from a one-line ask, but a structured template makes the intent unambiguous. Pick the one matching your scenario.

### Template A — single program detail page

Use when you have ONE URL pointing to a specific program (e.g., the MSc Finance page).

```
请用 uni-admission-crawl skill 帮我抓取一个程序详情：

  大学 slug:     <UNIVERSITY_SLUG>         # 例如 hku, leeds, manchester
  入学年份:      <YEAR>                    # 例如 2026
  URL:           <PROGRAM_DETAIL_URL>      # 例如 https://courses.leeds.ac.uk/202627/d027/clinical-embryology-msc
  抓取模式:      detail

完成后请汇报：是否成功入库、有没有进 quarantine、quality gate 怎么判的。
```

### Template B — single index page

Use when you have ONE index URL and want all programs ON THAT PAGE (no pagination follow).

```
请用 uni-admission-crawl skill 帮我抓取一个 index 页：

  大学 slug:     <UNIVERSITY_SLUG>
  入学年份:      <YEAR>
  入口 URL:      <INDEX_PAGE_URL>
  抓取模式:      index
  分页处理:      不要翻页，只抓这一页上能看到的程序

完成后请汇报：找到了多少 program、quarantine 分布、有没有异常 stop_reason。
```

### Template C — multi-page paginated index

Use when you want to crawl a paginated program listing across multiple pages. The system will auto-paginate but **stop early on URL drift, decreasing yield, or quality breaker**.

```
请用 uni-admission-crawl skill 帮我抓取一个分页 index：

  大学 slug:     <UNIVERSITY_SLUG>
  入学年份:      <YEAR>
  入口 URL:      <INDEX_PAGE_URL>          # 可以从任意中间页开始，比如 ?page=4
  抓取模式:      paginate
  最大页数:      <MAX_PAGES>                # 建议 5-20，超过先跑小数据集试

跑之前先告诉我预估耗时和 LLM 成本（~$0.01 / 程序 估算就行）。跑完后汇报：
- 总抓到多少程序
- stop_reason 是什么（"exhausted" 才是正常完成，其他都要提醒我）
- quarantine 分布（top 3 reason）
- 有没有进入 quarantine 的程序应该是 quality_failed 类型的，需要我重新检查
```

### Template D — quick smoke test on existing data

When you just want to see what's already in the DB:

```
请用 uni-admission-crawl skill 帮我查看 <UNIVERSITY_SLUG> 最近一次抓取的状况。

跑 `adm-agent crawl-summary --university <slug> --year <year>` 并解读结果给我。
```

---

## 4. What the LLM will do

When you submit a template, the LLM will (per SKILL.md):

1. **Preflight check**: verify backend is running (`curl /health`), DB is migrated, .env is configured
2. **Mode resolution**: based on your `抓取模式` placeholder, pick CLI flow or REST `/agent/run` flow
3. **Execute**: run the actual crawl, monitoring progress
4. **Summarize**: run `adm-agent crawl-summary` and translate the funnel + stop_reason + quarantine breakdown into plain language

If anything is ambiguous (e.g., you said "all programs" but didn't specify max_pages), it will ask before spending tokens.

---

## 5. Example session

```
You>  请用 uni-admission-crawl skill 帮我抓取一个分页 index：
        大学 slug:  leeds
        入学年份:   2026
        入口 URL:   https://courses.leeds.ac.uk/course-search/masters-courses?page=4
        抓取模式:   paginate
        最大页数:   5

LLM>   [preflight] 我先检查后端…
       ✅ /health 返回 ok
       ✅ Database schema is up to date
       
       这是个分页 index 抓取。预估：
       - Leeds 一页约 15 个程序 × 5 页 = ~75 个 detail 页面要走 LLM
       - 预计耗时 ~7-10 分钟
       - 预计 LLM 成本 ~$0.10-0.20 (DeepSeek)
       
       OK 我开始跑了。
       
       [运行中... LLM 会显示 task_id 和定期进度]
       
       ✅ 抓取完成 — leeds 2026
       
         抓取入口: https://courses.leeds.ac.uk/course-search/masters-courses?page=4
         漏斗:    raw=5 → filtered=5 → candidates=5 → extracted=75
         Quarantine: 0 条
         停止原因: max_pages
         耗时:    7m 14s
       
       命中了 max_pages 上限（5 页），如果还有更多程序需要抓取，可以把 max_pages 调高再跑一次。
       
       Quarantine 是 0 条，全部 75 个程序都通过了 quality gate。可以直接看：
         adm-agent quarantine list --university leeds
```

---

## 6. 故障排查

如果 LLM 报错或卡住：

| 症状 | 通常原因 | 解决 |
|---|---|---|
| LLM 说 "后端没启动" | `adm-agent up` 没运行 | 开新终端跑 `adm-agent up`，告诉 LLM 继续 |
| LLM 说 "migration pending" | DB schema 落后 | `adm-agent db-migrate --yes` |
| Task 长时间 RUNNING 不动 | LLM rate limit 或浏览器 hang | 查看 server 日志；用 `curl -X POST /tasks/<id>/cancel` 取消 |
| stop_reason 是 `url_drift` | 入口 URL 不是真正的 index 页 | 换一个真正包含程序列表的 URL |
| 大量 `extraction_failed` | 页面 JS 渲染 / 被 Cloudflare 拦 | 这类站点目前 server-side 抓不下来，需要 Chrome 扩展配合 |

如果你不知道哪里出问题，可以直接问 LLM：

> 用 uni-admission-crawl skill 诊断一下：刚才那次 <slug> 的抓取，audit 和 quarantine 里有什么异常？给我一个总结。

LLM 会自动跑 `crawl-summary` + `audit drill` + `quarantine list` 综合给你结论。
