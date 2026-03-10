# Uni-Admission-Agent 智能体化演进设计（PydanticAI）

## 1. 背景与目标
项目当前已经具备稳定的执行内核（serve/mcp/ingestion pipeline/taxonomy/golden gate），但仍以“工具调用 + 局部策略”为主。目标是在不破坏现有稳定能力的前提下，引入 PydanticAI 构建可编排的智能体运行时。

本次演进核心目标：
- 智能体默认关闭，显式命令开启。
- 智能体通过 Skill 封装复用现有 serve/mcp 能力。
- 智能体与现有 serve 程序解耦，通过抽象桥接层对接。
- 尽量不入侵现有逻辑；如必须触达，增加回归测试防护。
- 同时支持 `.env` 内置 LLM 与调用方 LLM 驱动。
- `adm-agent-client` 保持“执行器”角色，不承载决策本体。

## 2. 非目标
- 不重写现有 crawler / ingestion / db 持久化主链路。
- 不将智能体人格化（语气/人设）作为首版功能。
- 不做跨用户全局记忆学习。

## 3. 总体架构

### 3.1 三层结构
1. Core Layer（现有稳定层）
- `src/services/crawler.py`
- `src/services/ingestion_pipeline.py`
- `src/api/server.py` 与 MCP tools
- 数据库与 taxonomy 体系

2. Agent Runtime Layer（新增）
- 目录：`src/agent_runtime/`
- 抽象：`AgentRuntime` 接口
- 实现：`LegacyRuntime`（保底）与 `PydanticAIRuntime`（新）
- 职责：任务决策、步骤编排、降级控制

3. Bridge Layer（新增）
- 目录：`src/agent_bridge/`
- `ServeToolBridge`：统一调用 core 服务能力
- `ClientAutomationBridge`：复用 `/clients/ws` RPC 下发浏览器自动化
- 约束：bridge 返回 typed contract，不暴露底层 ORM 细节

### 3.2 Client 角色定位
- `adm-agent-client` 保持“手脚”定位，仅执行：
  - 打开/抓取页面
  - 返回 `html_content` / `detail_pages_batch`
- 智能体本体驻留 serve 侧，避免多客户端决策分裂与状态不一致。

## 4. 运行模式与开关策略

### 4.1 默认行为
- 默认关闭智能体：现有 `/crawl`、`/analyze`、MCP 基础工具行为不变。

### 4.2 显式开启
- 命令开关：`serve --agent`
- 配置开关：`AGENT_ENABLED=true`

### 4.3 运行时实现切换
- `AGENT_RUNTIME=legacy|pydanticai`（默认 `legacy`）
- `AGENT_ALLOW_INTERNAL_LLM=true|false`
- `AGENT_ALLOW_EXTERNAL_LLM=true|false`

## 5. LLM 驱动模式

### 5.1 内置模式（internal）
- 使用 `.env` provider 配置（沿用现有 router 能力）。

### 5.2 外置模式（external）
- 由调用方 LLM 驱动，智能体仅编排与执行 bridge skill。

### 5.3 统一适配
- 新增 `ModelProviderAdapter` 抽象统一两种模式，减少 runtime 分支复杂度。

## 6. Skill 契约化方案

### 6.1 首批 skill
- `analyze_page_skill`
- `select_detail_candidates_skill`
- `crawl_detail_batch_skill`
- `persist_programs_skill`
- `review_patch_skill`
- `query_db_skill`
- `browser_automation_skill`（经 client bridge）

### 6.2 契约要求
每个 skill 必须具备：
- Pydantic `input_schema`
- Pydantic `output_schema`
- 统一 `error_code`
- `idempotency_key` 规则
- 执行 trace 元信息

## 7. 任务数据流与状态机

### 7.1 Agent 状态机
- `PLANNING -> EXECUTING -> NEED_USER_INPUT -> FINALIZING -> DONE/FAILED`

### 7.2 典型 URL 流程
1. 输入 URL（可能缺 year）
2. 缺 year：进入 `NEED_USER_INPUT`，禁止落库
3. analyze 判别 index/detail
4. detail：直接 crawl -> persist -> summary
5. index：候选评分 + 自动/人工分流
6. 完成后输出 `review_items`，支持 patch 回写

### 7.3 与 ingestion pipeline 的关系
- Agent 负责“决策编排”
- Ingestion 负责“执行与持久化”
- 二者解耦并可独立回归

## 8. 用户策略配置（Policy Profile）

### 8.1 定位
- 非人格化配置，仅包含可执行策略参数。

### 8.2 存储与作用域
- 首版仅在客户端本地保存 profile。
- 每次请求随 payload 发送到 serve。
- serve 仅本次请求生效，不做跨用户共享学习。

### 8.3 参数优先级
- `request overrides` > `client profile` > `server defaults`
- 不合法参数自动归一化，返回 `normalized_policy` 与 `policy_warnings`。

### 8.4 示例参数
- `auto_run_max_candidates`
- `taxonomy_auto_threshold`
- `taxonomy_keep_threshold`
- `prefer_browser_provider`
- `require_manual_review_when_low_confidence`
- `llm_fallback_enabled`
- `batch_size` / `detail_concurrency`

## 9. 错误处理与降级

### 9.1 Skill 级
- 可重试异常：超时/网络错误指数退避。

### 9.2 Runtime 级
- PydanticAI 运行失败、schema 解析失败时自动降级到 `LegacyRuntime`。

### 9.3 预算边界
- `max_agent_steps`
- `max_total_tokens`
- `max_runtime_seconds`
- 超限后终止编排，返回可恢复状态或切换 legacy。

## 10. 可观测性
- 每个 skill 记录：
  - `trace_id`
  - `skill_name`
  - `duration_ms`
  - `tokens_used`
  - `result_status`
- 与现有 task/log 体系对齐，extension 能直接消费执行进度。

## 11. 兼容与回归门禁

### 11.1 兼容原则
- 不替换现有 REST/MCP 基础接口语义。
- 智能体能力通过新增入口/开关接入。

### 11.2 必备测试集
新增：
- `test_agent_bridge_contracts.py`
- `test_agent_runtime_fallback.py`
- `test_policy_profile_precedence.py`

保留：
- 现有 crawl/analyze/mcp 回归全绿
- golden samples 质量门禁持续通过

## 12. 发布策略
1. Phase A（Dark Launch）
- 合入代码但默认关闭，仅 CI 与本地可启

2. Phase B（Beta）
- 命令显式启用：`serve --agent`
- 收集 trace 与失败样本

3. Phase C（Selective Rollout）
- 在特定场景启用 `pydanticai runtime`
- 保持一键回退 `legacy`

## 13. 决策结论
- 采用方案A：智能体本体集成 serve 但通过 bridge 抽象解耦。
- client 仅作为执行器（手脚），不承载决策中枢。
- 首版聚焦策略配置，不引入人格化与跨用户记忆。
