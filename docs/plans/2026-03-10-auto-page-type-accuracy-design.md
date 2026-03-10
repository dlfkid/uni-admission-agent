# Auto 模式页面类型判断准确度优化设计

## 背景与问题
当前 `auto` 模式在部分大学（尤其 Leeds 风格页面）会把 index 误判为 detail，导致系统直接按 detail 抓取并跳过 index 选链流程。误判后用户需要多次手动重试，违背“一次操作完成”的主诉求。

## 目标
- 显著提升 `auto` 模式页面类型判断准确度。
- 在 `golden_samples` 现有 5 所大学（index+detail 共 10 条）达到 100% 通过。
- 建立长期门禁：后续任何新增 `golden_samples` 案例都必须通过 `auto` 模式页面类型判断。

## 非目标
- 不重构抓取主流程（fetch/extract/validate/persist）。
- 不引入多轮 LLM 分类。
- 不修改用户显式传入 `page_type_hint=index|detail` 的语义。

## 方案概览（已确认）
采用两阶段判定：
1. 规则阶段：多信号打分，输出 `index/detail/uncertain`。
2. LLM 阶段：仅对 `uncertain` 样本进行一次分类兜底（固定 schema，单次调用）。

核心原则：
- `auto` 不再默认 fallback 到 detail；
- 仅在规则不确定时调用 LLM；
- 判定结果必须可解释（结构化 reasons/trace）。

## 架构与数据流
1. 入口统一调用 `classify_page_type_auto(url, markdown, html, link_count)`。
2. 规则阶段计算 `index_score/detail_score` 和判定理由集合。
3. 若分差达到高置信阈值，直接返回规则结果。
4. 若分差过小或信号冲突，进入一次 LLM 分类。
5. LLM 成功则返回 LLM 判定；LLM 失败则回退规则高分侧并标记低置信原因。
6. 上游按结果进入 index 或 detail 分支，同时记录决策 trace。

输出模型（建议）：
- `page_type`: `index|detail`
- `confidence`: `0.0~1.0`
- `decision_source`: `rule|llm|rule_fallback`
- `reasons`: `list[str]`
- `scores`: `{index: float, detail: float}`

## 信号体系与打分

### URL 信号（中等权重）
- index 正信号：`course-search`、`find-your-programmes`、`/courses/list`（无具体 id）、`?page=`。
- detail 正信号：`/courses/list/<id>/`、`/programmes/<long-slug>`、`/tpg/<year>/<code>`。

### 内容信号（去噪后）
- index 正信号：`filters`、`showing N courses`、`browse subject`、`search degree programmes`。
- detail 正信号：`entry requirements`、`tuition fees`、`course structure`、`modules`。
- 弱化项：`how to apply`、`duration` 单独命中不应直接导致 detail 高置信。

### 结构信号
- `unique_internal_links` 与课程链接占比（course-like link ratio）。
- heading 结构：列表筛选型 vs 单课程 hero heading。
- HTML title：列表页词 vs 课程名/课程码词。

### 冲突消解
- `abs(index_score - detail_score) < margin_low` => `uncertain`（进 LLM）。
- 双高冲突（均高分）=> `uncertain`。
- 单弱信号触发不得强判 detail。

默认阈值（初版）：
- `margin_high = 0.35`
- `margin_low = 0.15`
- `llm_confidence_pass = 0.70`

## LLM 兜底设计
- 触发条件：仅 `uncertain`。
- 输入证据包：URL、title、关键 headings、链接统计、关键词计数、规则阶段 top reasons。
- 输出 schema：`{page_type, confidence, reason}`。
- 超时/解析失败：回退规则高分侧，`decision_source=rule_fallback`。

## 错误处理
- markdown 过短或结构异常时，补用 html title 与链接统计。
- LLM 异常不阻断流程，仅降低判定置信度并记录原因。
- 所有路径都返回完整判定结构，避免静默误判。

## 可观测性
新增日志字段：
- `auto_page_type`
- `rule_index_score`
- `rule_detail_score`
- `used_llm`
- `llm_confidence`
- `decision_reasons`

新增指标：
- `auto_rule_direct_rate`
- `auto_llm_escalation_rate`
- `auto_low_confidence_rate`
- `auto_accuracy_golden_index`
- `auto_accuracy_golden_detail`

## 测试与回归策略

### 单元测试
- 信号命中、阈值边界、冲突分支、LLM 失败回退。

### Golden 回归测试（强制）
- 当前 `golden_samples` 5 所大学：
  - 5 个 index URL 在 `auto` 下必须判为 index；
  - 5 个 detail URL 在 `auto` 下必须判为 detail。
- 验收门槛：10/10 全通过。

### 长期门禁规则
- 任何后续新增 `golden_samples` 案例，必须纳入同一 `auto` 页面类型回归测试。
- CI 作为必过门禁：新增样例一旦误判，合并阻断。

## 验收标准
- `auto` 在现有 golden 10 条样本全对（100%）。
- 新增样例能自动进入门禁并保持全通过。
- 判定日志可复盘具体原因。
- LLM 失败时流程可用且行为可解释。
