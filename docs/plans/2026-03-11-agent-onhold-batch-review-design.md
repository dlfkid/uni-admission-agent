# Agent 调度增强设计：低置信度 Onhold 批量确认流

## 1. 目标与范围

### 1.1 目标
在现有智能体基础上增加“可感知调度能力”：
- 智能体从单入口 URL 自动跑完整链路：`analyze -> candidate 处理 -> detail 抓取 -> persist -> review_items`
- 低置信度 case 不中断主流程，统一进入 `onhold`
- 执行完后集中给用户确认
- 用户只需要给出“要继续处理的序号集合”，其余默认废弃

### 1.2 非目标
- 不做跨用户/跨任务长期学习
- 不做 A/B 对比页
- 不重写现有 ingestion 主链路

## 2. 用户体验定义

### 2.1 行为原则
- 主流程自动执行到底
- 低置信度统一后置处理，不打断
- 用户交互聚焦在一次“批量选择”

### 2.2 关键交互
系统在任务结束时返回 `onhold_items`：
- 先按 `confidence` 从高到低排序
- 再按排序结果分配动态序号 `1..N`
- 用户输入示例（仅为格式示例，非固定值）：`继续处理 3,6,18`
- 仅处理用户选择项，未选择项默认废弃

## 3. 状态机扩展

在现有状态机上新增/细化阶段：
- `EXECUTING`：自动执行主流程
- `ONHOLD_REVIEW`：聚合低置信项并排序编号
- `WAIT_USER_SELECTION`：等待用户给出继续处理序号
- `APPLY_SELECTED`：执行白名单项后处理
- `DONE/FAILED`

状态转移：
1. `EXECUTING` 结束后若无低置信项 -> `DONE`
2. 有低置信项 -> `ONHOLD_REVIEW -> WAIT_USER_SELECTION`
3. 收到合法序号集合 -> `APPLY_SELECTED -> DONE`
4. 输入非法序号 -> 维持 `WAIT_USER_SELECTION` 并返回错误信息

## 4. 数据结构设计

### 4.1 Onhold 项结构（最小）
每项至少包含：
- `index`：本次任务内排序后编号（稳定）
- `item_id`：内部稳定标识（用于幂等）
- `program_name_candidate`
- `source_url`
- `confidence`
- `hold_reason`

### 4.2 批量选择请求结构
- `task_id`
- `selected_indices`（支持离散与区间语义）

### 4.3 批量处理结果结构
- `selected_count`
- `discarded_count`
- `invalid_indices`
- `applied_items`
- `discarded_items`

## 5. 协议与调度规则

### 5.1 编号规则
- 先排序后编号：`confidence DESC`
- 仅在同一 `task_id` 下保证编号稳定

### 5.2 默认规则
- 用户未选中的 `onhold_items` 默认 `discard`
- 用户空选择时，全部 `discard`

### 5.3 输入容错
解析用户常见表达：
- `3,6,18`
- `1-5,9`
- `2 4 7`
并提供 `invalid_indices` 反馈

## 6. 与现有架构的集成点

### 6.1 Runtime 层
- 在 `PydanticAIRuntime` 中接入真实 skill 编排
- 把低置信判定结果沉淀为 `onhold_items`

### 6.2 Skill 层
- 继续复用现有 typed skill registry
- 新增/细化：
  - `finalize_onhold_review_skill`
  - `apply_selected_onhold_items_skill`

### 6.3 API/MCP 层
- 保留现有 `agent_run`
- 增加确认入口（REST/MCP 任一或两者）：
  - 示例：`agent_review_confirm(task_id, selected_indices)`

## 7. 错误处理与恢复

- 非法序号：返回 `invalid_indices`，不执行处理
- 序号重复：去重后执行
- `onhold_items` 为空：跳过确认阶段
- 用户迟迟未确认：任务保持 `WAIT_USER_SELECTION` 可恢复
- 处理阶段失败：标记失败项，任务可重试且不重复处理已完成项

## 8. 测试策略

### 8.1 单测
- 低置信项排序与编号稳定性
- 序号解析（逗号/空格/区间）
- 默认 discard 行为

### 8.2 集成测试
- `EXECUTING -> WAIT_USER_SELECTION -> APPLY_SELECTED -> DONE`
- 非法输入后重提成功

### 8.3 回归测试
- `AGENT_ENABLED=false` 时原 `/crawl` 与 MCP 基础工具行为不变

## 9. 发布建议

- Phase 1：仅对 `agent_run` 启用，默认关闭
- Phase 2：扩展到 extension 可视化确认
- 全阶段保持 `legacy` 一键回退能力
