# Leeds index->detail 课程名准确率优化设计

## 背景与问题
在 Leeds 等站点的 index -> detail 抓取链路中，`selected_anchor_text` 与 detail URL slug 对课程名有强信号，但当前流程会被 detail 正文中的噪声句（如入学要求文案）误导，导致课程名抽取错误（例如误抓 `What's New` 或要求句）。

该问题直接影响：
- 用户对批量抓取结果的信任度
- 数据库去重与可读性（错误名/unknown 污染）
- taxonomy 后续学习质量

## 目标
- 显著提升 index -> detail 场景课程名准确率（优先保障 Leeds 一类页面）
- 默认开启 LLM 兜底，避免低置信 `unknown` 污染
- 若连 LLM 也不确定，记录日志并跳过入库
- 控制额外成本：单 detail 页额外延迟目标可控（预算上限 800ms 级别），token 增量目标不超过 30%

## 非目标
- 不改动现有业务字段语义
- 不重构整个爬虫流程，仅增强课程名决策链路
- 不在本阶段引入复杂多轮 LLM 交互

## 方案概览（已确认）
采用**方案 C（两阶段混合）**：
1. 规则快路径：多源候选 + 打分，命中高置信直接确定课程名
2. 低置信兜底：默认触发一次 LLM，基于高信号证据包决策
3. LLM 仍低置信：标记 unresolved，不入库，仅在任务结果/日志输出

## 架构与组件

### 1) 候选生成层
输入源（index -> detail）：
- `selected_anchor_text`
- detail URL 与 slug
- `title/h1/h2`
- `breadcrumb/og:title`
- 现有正文抽取候选

输出：`NameCandidate[]`（文本、来源、初始分、证据）

### 2) 候选过滤与评分层
- 噪声过滤：拦截导航词、栏目词、cookie 文案、入学要求句型等
- 来源权重：`anchor` > `slug` > `title/h1/h2` > 正文
- 一致性加分：候选与 slug/anchor 语义一致时加分
- taxonomy 匹配加分：仅用于加权，不强制覆盖

输出：最佳候选、次优候选、置信分、冲突度

### 3) LLM 兜底层（默认开启）
触发条件：
- 最高分 < 0.80，或
- 前两名分差 < 0.05（冲突）

调用策略：
- 每个 detail 最多 1 次 LLM
- 固定 JSON 输出 schema（name/confidence/evidence）
- 超时/解析失败视为 unresolved

### 4) 入库门禁层（去污染）
- `RESOLVED`：允许进入 `persist_versioned`
- `UNRESOLVED`：禁止入库（program/taxonomy 都不写）
- 任务输出新增 `unresolved_urls[]`，用于 UI/日志提示

## 数据流
1. index 选择 detail URL 后进入 detail 抓取
2. 生成多源课程名候选
3. 过滤与评分
4. 若高置信：直接返回 `RESOLVED`
5. 若低置信/冲突：构建证据包调用 LLM
6. LLM 返回高置信：`RESOLVED`
7. LLM 仍低置信/失败：`UNRESOLVED`，记录并跳过
8. batch 结束后向前端返回成功结果 + unresolved 清单

## LLM 证据包设计（避免“只取到 cookie 垃圾”）
证据包不按页面前序截断，而按信号强度拼装：
- 必含：`anchor_text`、`url`、`slug`、`title`、`h1/h2`、`breadcrumb`、`og:title`
- 正文：提取最多 3 个“学位关键词邻域 chunk”（每块约 350~500 字符）
- 总长度硬上限（约 2.5k~3.5k 字符），超出按权重裁剪

这样保证至少有“一个有效 chunk”，同时不让 token 无上限增长。

## 错误处理
- LLM 超时：标记 unresolved 并继续下一个 detail
- LLM 返回格式错误：标记 unresolved
- 候选空集：直接 unresolved
- batch 内单条失败不阻塞整体队列，最终给出失败统计与 URL 明细

## 性能与成本控制
- 大多数页面应停留在规则快路径（0 次 LLM）
- LLM 仅在低置信页触发，单页最多 1 次
- 设置兜底并发上限（建议 2），避免批量任务抖动
- 通过证据包裁剪控制 token 增量目标 <= 30%

## 可观测性与日志
新增指标建议：
- 规则直出命中率
- LLM 兜底触发率
- unresolved 比例
- LLM 平均延迟/token
- taxonomy 命中加分贡献

前端日志需明确：
- 哪些 URL 被跳过（unresolved）
- 跳过原因（低置信/超时/解析失败）
- 建议动作（可转单条 detail 重试）

## 测试策略

### 单元测试
- 候选过滤：噪声词与要求句应被抑制
- 候选排序：anchor/slug 在 index->detail 中应优先
- 触发阈值：<0.80 或分差<0.05 才触发 LLM
- unresolved 门禁：不允许进入入库流程

### 集成测试
- Leeds golden sample：课程名应稳定命中正确名称
- 构造低置信样本：验证 LLM 兜底路径
- LLM 失败样本：验证 unresolved 记录与跳过行为

### 回归测试
- 非 index->detail 场景不应出现行为倒退
- 批量抓取在并发限流下应稳定完成

## 实施注意事项
- 将新逻辑做成可配置策略（阈值、兜底开关、并发上限）
- 默认值按本设计启用：LLM 兜底开启、低置信跳过入库
- 所有新分支必须带可观测日志，避免“静默丢数据”

## 验收标准
- Leeds 案例课程名正确（不再出现 `What's New`/要求句）
- 低置信条目不入库且可在任务结果中定位
- token 增量与延迟在预算内可解释
- 现有核心流程与批量抓取稳定通过回归测试
