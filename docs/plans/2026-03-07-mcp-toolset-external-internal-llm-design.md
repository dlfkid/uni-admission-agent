# MCP 双工具集（外部LLM/内置LLM）拆分设计稿

## 1. 背景与问题
- 当前服务虽支持 MCP，但核心链路中仍存在对 `.env` 内置 LLM 的强依赖。
- 这会让“调用方 LLM 已经很强”的场景出现重复配置与职责混乱。
- 目标是让 MCP 工具默认可被任意宿主 LLM 直接使用，不强制用户配置服务端 LLM。

## 2. 目标
- 提供两套 MCP 工具：
  - 基础工具集（保留现有工具名）：不依赖服务端内置 LLM。
  - 内置模型工具集（后缀 `_internal_llm`）：显式使用 `.env` 配置的服务端 LLM。
- 通过工具名差异降低调用方模型“误选能力路径”的幻觉风险。
- 复用已有 taxonomy 评分标准做自动化决策（0.75 / 0.92）。
- 增加“落库后审阅与修订回写”闭环。

## 3. 非目标
- 不改动现有数据库核心 schema 语义。
- 不取消现有客户端（client bridge）能力。
- 不强推“全量智能体化”改造。

---

## 4. 总体架构

### 4.1 双工具集
- 基础工具集（始终注册）：
  - `analyze`
  - `crawl`
  - `crawl_detail_batch`
  - `db_query`
  - `runtime_status`（新增）
  - `program_patch`（新增）
  - `program_patch_batch`（新增）
- 内置LLM工具集（仅当内置LLM可用时注册）：
  - `analyze_internal_llm`
  - `crawl_internal_llm`
  - `crawl_detail_batch_internal_llm`
  - （可选）`program_patch_from_feedback_internal_llm`

### 4.2 一致性原则
- 同一会话建议保持单一决策侧：
  - 基础工具链：调用方 LLM 负责所有决策与反馈解析。
  - `_internal_llm` 工具链：服务端 LLM 负责所有决策与反馈解析。
- 避免混用，减少上下文断裂和错误改写。

---

## 5. 运行态与 client 可见性
- 调用方仅需配置一个 MCP endpoint（serve 地址）。
- `runtime_status` 返回：
  - `client_available`
  - `client_count`
  - `client_ids`
  - `internal_llm_available`
  - `default_browser_provider_resolved`
- 所有 crawl/analyze 响应附带：
  - `resolved_browser_provider`
  - `client_id_used`（如有）

这使调用方 LLM 不必了解 client API 细节，仅需使用工具返回的运行态信息。

---

## 6. 交互与决策流程（基础工具链）

### 6.1 输入门禁
- 用户通常只给 URL。
- 若缺少 `year`，工具返回：
  - `requires_user_input=true`
  - `missing_fields=["year"]`
  - `prompt="请确认落库年份（如 2026）"`
- 未补齐 `year` 前不得执行抓取落库。

### 6.2 detail 场景
- 直接抓取详情页内容。
- 结构化 program 由调用方 LLM 组织输出。
- serve 负责入库并返回执行结果。

### 6.3 index 场景
- 使用已有 taxonomy 信号给候选链接打分（0~1）并提供课程名推断。
- 判定规则：
  - 候选保留阈值：`>= 0.75`
  - 自动直爬阈值：`>= 0.92` 且候选数量 `<= 10`
- 若满足自动条件且用户未要求人工复核：直接 batch crawl。
- 若候选 >10、语义不明确、或用户明确要求过目：先回传候选给用户选择，再执行 batch crawl。

### 6.4 browser_provider=auto
- 有在线 client：优先 `client`
- 无 client：回退 `server`
- `strict_client=true` 且不可用：返回明确错误，不回退

---

## 7. 落库后审阅与修订回写

### 7.1 审阅阶段
- 抓取与入库完成后，返回：
  - `review_token`
  - 有序结果列表（含稳定 `program_id`）
- 调用方 LLM 必须询问用户是否需要修改。

### 7.2 用户反馈处理
- 用户反馈示例：
  - “课程学费错了”
  - “第6-10条数据有误”
- 调用方 LLM（或 internal_llm 版本）将自然语言转结构化 patch：
  - 基于返回列表顺序映射到具体 `program_id`，禁止按文案模糊定位直接修改。

### 7.3 回写工具
- `program_patch`：单条更新。
- `program_patch_batch`：批量更新（支持第6-10条这种范围修订）。
- 回写后返回 `updated_count`、`failed_items`、`summary`。

---

## 8. 错误处理
- 单条 detail 失败：记录并继续。
- 单批失败：可重试该批，不影响已成功批次。
- patch 校验失败：按条目返回错误，不中断整批。
- 所有工具返回 `decision_reason` / `error_code` / `next_action_hint` 便于调用方 LLM继续对话。

---

## 9. 兼容与迁移
- 保留现有工具行为，先增量添加双工具集与新元数据字段。
- 基础工具集优先文档化与推荐。
- `_internal_llm` 工具作为增强能力，不影响无LLM配置用户。

---

## 10. 风险与缓解
- 风险：双工具集增加认知负担。
  - 缓解：统一命名规则 + `runtime_status` + 文档示例 flow。
- 风险：调用方 LLM 解析用户修改意见出错。
  - 缓解：要求基于 `program_id` 回写；支持批量 patch 的逐条错误返回。
- 风险：client 在线状态波动导致路径抖动。
  - 缓解：每次响应回传 `resolved_browser_provider`，保证可观测。

