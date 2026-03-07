# Serve 调 Client 浏览器自动化（统一模式）设计稿

## 背景
- 当前“浏览器自动化”主要通过 extension 触发并上送 `detail_pages_batch`。
- 系统同时提供 REST API 与 MCP，外部 LLM 可以直接调用 `crawl`，但这时用户可能未打开浏览器或未打开 extension。
- 目标是在不改变对外主入口（仍是 `crawl`）前提下，让服务端可调度用户本机浏览器执行自动化抓取。

## 目标与非目标

### 目标
- 同时支持两种场景，且调用模式一致：
  - `serve` 与用户浏览器同机；
  - 远程 `serve` 调度用户本机浏览器。
- 对外保持 `crawl` API/MCP 主入口，不新增必须工具名。
- 对非技术用户最小安装：下载解压、运行初始化向导、连接 `serve`。
- extension 不再是必需依赖。

### 非目标
- 不做系统级键鼠注入、读屏、窗口录制等高风险自动化能力。
- 不要求 root/sudo 常驻权限。

## 结论（架构决策）
- 采用 **Client Bridge**：新增跨平台 `adm-agent-client` 常驻进程（linux/apple silicon/windows）。
- `serve` 通过统一“client 派单协议”下发浏览器自动化任务，client 在本机拉起浏览器并回传 HTML。
- extension 保留为可选增强通道，不作为 MCP/REST 自动化抓取前提。

---

## 一、统一调用模型（同机/远程一致）

### 1) 对外入口保持不变
- CLI：`crawl`
- REST：`POST /crawl`
- MCP：`crawl` tool

### 2) `crawl` 扩展可选字段
- `browser_provider: "auto" | "server" | "client"`（默认 `auto`）
- `client_id?: str`（可指定目标 client）
- `strict_client?: bool`（默认 `false`，当 `true` 时 client 失败不回退）

### 3) `auto` 决策顺序
1. 若请求携带 `detail_pages_batch`：直接走现有浏览器 HTML 批处理分支。
2. 否则若存在可用 client：走 client browser automation。
3. 否则走 `server`（现有 Playwright 路径），或在 `strict_client=true` 时报错。

该决策使同机与远程在服务端完全同一逻辑，仅 client 连接拓扑不同。

---

## 二、Serve ↔ Client 协议设计

### 1) 连接形态
- client 主动连接 `serve`（建议 WebSocket）。
- 远程场景无需 `serve` 反向入站用户网络，降低家庭网络配置难度。

### 2) 注册握手（client -> serve）
- `client_id`（持久 UUID）
- `client_name`（用户可读名称）
- `platform` / `arch`
- `workdir`
- `capabilities`：
  - `browser_automation=true`
  - `extension_installed=true|false`
  - `providers=["cliten"]`（首发）

### 3) 心跳与状态
- 心跳周期（如 15s）
- `serve` 维护在线列表与最近活跃时间
- 暴露状态接口：
  - `GET /clients`
  - `GET /clients/{client_id}`

### 4) 派单消息（serve -> client）
- `task_id`, `url`, `page_type_hint`
- `batch_index`, `batch_total`
- `timeout_sec`
- `automation_profile`（加载等待、滚动策略、提取策略）

### 5) 回传消息（client -> serve）
- 成功：
  - `detail_pages_batch`（优先）
  - 或 `html_content`（detail/index 场景）
- 失败：
  - 结构化错误码：`BROWSER_START_FAILED`、`ANTI_BOT_BLOCKED`、`TIMEOUT`、`PERMISSION_REQUIRED` 等

### 6) 调度策略
- 默认选择“当前用户最近活跃 client”。
- 如请求指定 `client_id`，优先定向派发。

---

## 三、Client 执行器设计（`adm-agent-client`）

### 1) 职责边界
- 连接 `serve`
- 接收任务
- 本机启动/复用浏览器自动化会话
- 提取 HTML/候选链接并回传

### 2) 浏览器执行后端
- 首发主执行器：`cliten`（按命令契约调用）
- extension 执行器：可选，不作为默认必需

### 3) CLI 体验（面向非技术用户）
- `adm-agent-client init`
  - 交互输入 `serve host/port`、`client_name`
  - 写入 `~/.adm-agent/client.toml`
  - 自动做连通性测试
- `adm-agent-client start`
  - 前台常驻并输出连接状态
- `adm-agent-client status`
  - 显示在线状态、client_id、当前 workdir

### 4) 与现有分发方式对齐
- 与 `serve` 保持同样三平台构建与“下载解压即用”分发体验。
- 不依赖用户手写复杂配置。

---

## 四、权限与安全模型（重点：macOS）

### 1) 能力最小化
- 不申请系统辅助功能（Accessibility）注入权限。
- 不读取屏幕内容，不做系统键鼠驱动。
- 仅执行浏览器会话自动化与网页 HTML 读取。

### 2) 平台阻断说明（README 必须新增）
- macOS：
  - Gatekeeper 隔离属性处理（quarantine 清除命令）
  - 首次网络连接放行说明
- Windows：
  - SmartScreen 首次允许运行说明
- Linux：
  - 可执行权限与依赖检查说明

### 3) 网络与信任
- client 到 serve 建议使用 API token（首次 init 时生成或输入）。
- 所有任务日志记录 `client_id` 与来源，便于审计。

---

## 五、LLM 一键引导（安装/配置 prompt 模板）

### 1) 目标
- 用户只需把一段 prompt 交给其已可交互的 LLM CLI/IM 代理，即可完成安装、初始化和启动。

### 2) 命令
- `adm-agent-client bootstrap --emit-prompt --target codex|claude|openclaw|generic`

### 3) 模板策略
- `codex`：面向 Codex CLI 的步骤化 prompt。
- `claude`：面向 Claude Code 的步骤化 prompt。
- `openclaw`：不绑定具体命令入口，假设用户已在 OpenClaw 对话环境中。
- `generic`：任何“可执行命令+编辑文件”的代理通用模板。

### 4) 输出内容
- 自动识别 OS/arch 与下载链接建议。
- 包含 init/start/status 验证步骤。
- 失败自检分支（连不上 serve、端口错误、权限拦截）。

---

## 六、失败处理与回退策略

### 1) client 不在线
- `browser_provider=auto`：回退 `server` 或返回可操作提示。
- `browser_provider=client` 且 `strict_client=true`：直接失败并给出“启动 client”指引。

### 2) client 执行失败
- 支持按错误码决定是否重试（如超时可重试 1 次）。
- 反爬阻断错误直接上报，不盲目重试。

### 3) 任务取消
- `serve` 取消任务时通知 client 终止当前浏览器作业并清理上下文。

---

## 七、可观测性与运维
- 任务事件新增：
  - `client_selected`
  - `client_exec_started`
  - `client_exec_succeeded`
  - `client_exec_failed`
- `GET /tasks/{id}` 中暴露 `client_id`、`provider`、错误码摘要。
- `/clients` 提供在线诊断入口，降低远程支持成本。

---

## 八、兼容性结论（回答核心问题）
- **是否要求用户安装 extension？**
  - 在本设计下：**不要求**。
  - extension 仅作为可选增强路径；MCP/REST 自动化抓取可完全由 `adm-agent-client` + `cliten` 完成。

---

## 九、实施顺序建议（高层）
1. 先落地 client 注册与派单骨架（可先返回 mock html）。
2. 接入 `crawl` 的 `browser_provider/client_id/strict_client` 决策层。
3. 接入 cliten 执行器并串通 `detail_pages_batch` 既有 ingestion 分支。
4. 补齐 `/clients` 观测、README 权限章节、bootstrap prompt 模板（含 openclaw）。
5. 最后做跨平台打包与端到端验收。

---

## 十、实施补充（2026-03-07）
- `serve` 与 `browser_provider` 的对接方式：
  - `availability_fn`: 基于 `client_registry.select_client_id(...)`
  - `fetch_fn`: 通过 `WS /clients/ws` 发送 `rpc_request`，等待 `rpc_result`
- 当前 client 执行器采用“外部命令模板”契约：
  - 环境变量：`ADM_AGENT_CLIENT_FETCH_CMD`
  - 占位符：`{url}`、`{page_type_hint}`
  - stdout 必须返回 JSON payload（`html_content` 或 `detail_pages_batch`）
- `adm-agent-client` 连续模式 (`start --continuous`) 已实现 websocket 长连与心跳。
