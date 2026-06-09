# 学科名抓取 — 第二轮真实抓取结果（诚实版）

> 在当前分支做完一轮修复后，对**全新 SQLite 库**真实抓取 Leeds 硕士索引页。
> **坏消息：修复没完全生效。** 真实结果暴露了更深的两层问题。

---

## A. 页面真实有 15 门课（ground truth）

```
1  Accounting and Finance MSc
2  Advanced Chemical Engineering MSc
3  Advanced Clinical Practice MSc
4  Advanced Clinical Practice (Apprenticeship) MSc
5  Advanced Computer Science MSc
6  Advanced Computer Science (Artificial Intelligence) MSc
7  Advanced Computer Science (Cloud Computing) MSc
8  Advanced Computer Science (Data Analytics) MSc
9  Advanced Manufacturing and Automation MSc
10 Advanced Mechanical Engineering MSc (Eng)
11 Advertising and Design MA
12 Aerospace Engineering MSc
13 AI Ethics and Society MSc
14 AI for Business MSc
15 Applied and Professional Ethics PGDip
```

抓取阶段：**15 个详情页全部抓到了**（日志确认，包括上次丢的 AI for Business / AI Ethics / Advertising and Design）。

## B. 但最终只入库 9 行（实际结果）

| # | 入库 name_en | 问题 |
|---|---|---|
| 1 | Accounting and Finance | ⚠️ 丢了 "MSc" 后缀 |
| 2 | Advanced Chemical Engineering | ⚠️ 丢了 "MSc" 后缀 |
| 3 | Advanced Clinical Practice MSc | ✅ |
| 4 | Advanced Computer Science | ⚠️ 丢了 "MSc" 后缀 |
| 5 | Advanced Computer Science (Artificial Intelligence) MSc | ✅ |
| 6 | Advanced Computer Science (Cloud Computing) MSc | ✅ |
| 7 | Advanced Computer Science (Data Analytics) MSc | ✅ |
| 8 | Aerospace Engineering | ⚠️ 丢了 "MSc" 后缀 |
| 9 | Applied and Professional Ethics PGDip | ✅ |

**消失的 6 门**：Advanced Clinical Practice (Apprenticeship) MSc、Advanced Manufacturing and Automation MSc、Advertising and Design MA、AI Ethics and Society MSc、AI for Business MSc，以及一门塌缩。

进步：**不再有"入学要求句子"当名字了**（噪声过滤 + fallback 检查生效）。
但：**后缀仍被剥、课程仍在丢**。

---

## C. 为什么没修好 —— 真实数据挖出的更深两层

我之前的修复（anchor URL-key 对齐、catalog_key 改用 source_url）**被更底层逻辑绕过了**：

### 根因 1：`program_group_code` 是「名字派生」的，且去重真正用的是它
- `page_processor.py:332` 给每个程序生成 `program_group_code = "{slug}#{规范化名字}"`
- `catalog_key` 优先用 group_code → 我加的 `url:` 去重分支**根本没机会执行**
- 所以两门课只要名字规范化后相同 → 同 group_code → **塌缩**（15→9 就这么来的）
- 实测一行的 group_code 甚至是 `leeds#watchouronlinetalks...`（一段横幅 CTA 文字）

### 根因 2：anchor 仍没进到最终名字
- 入库名是详情页正文名（"Accounting and Finance"，无后缀），不是索引页 anchor（"Accounting and Finance MSc"）
- 说明 `resolve_program_name` 拿到的 `selected_anchor_text` 仍是空
- 我改的 `_serialize_pages` URL-key 对齐**在真实抓取路径上没把 anchor 喂进去** —— anchor map 要么没这些 URL，要么这条 CLI 路径没走到我改的地方
- 且 `name_en` 在 resolution 后更新了（1239 行），但 `program_group_code` **没重算** → 用的是旧的正文名派生码

---

## D. 我的判断

这是一个**多层架构问题**，不是一两处补丁能解决的。我这轮做的 3 个改动（噪声 fallback、合并正则、slug 去码、catalog_key url 优先）方向对、有单元测试，但**真实链路上有两个更上游的拦路点**（group_code 名字派生 + anchor 没喂进去），把效果吃掉了。

**下一步需要：**
1. anchor 真正打通到 `selected_anchor_text`（要在真实 CLI 路径上加日志定位 anchor map 在哪断）
2. `program_group_code` 不再从易错的名字派生 —— 改用 source_url，或在 name resolution 后重算
3. resolution 后同步重算 group_code

要不要我继续往下挖？还是你想先看 anchor map 在真实抓取里到底有没有这些 URL（加临时日志重跑一次）？
