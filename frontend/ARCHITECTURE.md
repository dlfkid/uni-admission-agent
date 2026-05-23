# UniAdmission Chrome Extension Architecture

> 目标：让 LLM/开发者在最短时间理解 extension 的结构、数据流和改造入口。

---

## 1) High-Level

这是一个 **Manifest V3** Chrome Extension，采用 **Side Panel UI** + **Background Service Worker** 架构：

- `background.ts`：仅负责打开 side panel（无业务状态）
- `popup.html + popup.ts`：主交互界面与流程编排
- `src/popup/*.ts`：按功能拆分的子流程模块
- 后端 API：默认调用 `http://localhost:8910`

---

## 2) Directory Map

```text
extension/
├─ public/
│  └─ manifest.json                # MV3 配置
├─ src/
│  ├─ background.ts                # Service Worker
│  ├─ popup.html                   # Side panel UI 结构
│  ├─ popup.css                    # Side panel 样式
│  ├─ popup.ts                     # 主流程编排（初始化 + crawl + 模块接线）
│  └─ popup/
│     ├─ dom.ts                    # DOM 引用集中导出
│     ├─ types.ts                  # 前端类型定义（TaskInfo/ProgramRecord 等）
│     ├─ monitorFlow.ts            # 任务轮询/日志/进度条
│     ├─ linkSelectionFlow.ts      # Index 候选链接选择与提交
│     ├─ configFlow.ts             # 配置编辑弹窗
│     ├─ exportFlow.ts             # 导出弹窗
│     └─ previewFlow.ts            # 数据预览弹窗
├─ vite.config.ts                  # 多入口构建（popup + background）
└─ package.json                    # build/dev 命令
```

---

## 3) Runtime Components

### 3.1 `background.ts`（极简）

- 监听点击扩展图标
- 调用 `chrome.sidePanel.open(...)` 打开面板
- 设置 `openPanelOnActionClick=true`

### 3.2 `popup.ts`（编排层）

主要职责：

1. 初始化 UI 状态与本地缓存（`localStorage`）
2. 读取当前 Tab URL / HTML
3. 调用 `/analyze` 决定 `index/detail` 流程
4. 提交 `/crawl` 任务
5. 初始化并接入子模块：
   - `initMonitorFlow(...)`
   - `initLinkSelectionFlow(...)`
   - `initConfigFlow(...)`
   - `initExportFlow(...)`
   - `initPreviewFlow(...)`

---

## 4) Main User Flows

## 4.1 Crawl Flow

1. 用户点击 `Start Crawl`
2. popup 获取当前页面 HTML（`chrome.scripting.executeScript`）
3. 调用 `POST /analyze`
4. 分支：
   - `detail`：直接 `POST /crawl`
   - `index`：进入 `linkSelectionFlow`，用户确认后 `POST /crawl(selected_urls)`
5. 进入 `monitorFlow` 轮询 `GET /tasks/{task_id}`，展示：
   - 状态文本
   - 日志
   - token 使用量
   - 进度条（优先使用后端 `progress_percent`）

## 4.2 Preflight Logs（分析前日志）

`popup.ts` 在输入页增加 preflight console，展示：

- 读取 HTML 开始/成功
- analyze 开始/完成
- index 候选链接数量
- 错误信息

用于改善 index 场景“只灰显按钮无反馈”的体验。

## 4.3 Index Candidate Progress（逐链接抓取进度）

后端任务状态返回 `progress_meta.event = fetch_url_progress`，包含：

- `phase`（如 `selected_urls` / `index_detail_links`）
- `current`
- `total`
- `status`
- `url`

前端在 `progress` 文案与进度条中体现“第几个链接”。

---

## 5) API Contract Used by Extension

### 5.1 Core APIs

- `POST /analyze`
  - 入参：`url`, `html_content`, `page_type_hint`
  - 出参：`page_type`, `links`, `total_found`

- `POST /crawl`
  - 入参（关键）：`url`, `univ_slug`, `year`, `page_type_hint`, `selected_urls?`
  - 出参：`task_id`

- `GET /tasks/active`
  - 打开面板时用于“任务重连”

- `GET /tasks/{task_id}`
  - 轮询状态：`state`, `progress`, `logs`, `tokens_used`,
    `progress_percent`, `progress_meta`

- `POST /tasks/{task_id}/cancel`

### 5.2 Config/Export/Preview APIs

- `GET/POST /config/structured`
- `POST /export`
- `GET /programs?univ_slug=...&year=...`
- `GET /universities`

---

## 6) State Management

没有全局状态管理库，采用“轻量本地状态 + DOM 驱动”：

- 内存状态（`popup.ts`）：
  - `cachedUniversities`
  - `currentWindowId`
  - `lastPageHTML`
  - `monitorFlow` 实例引用

- 本地持久化（`localStorage`）：
  - `crawl_page_type`
  - `crawl_export_md`
  - `crawl_export_path`
  - `crawl_univ_slug`
  - `logs_expanded`

---

## 7) Build & Packaging

命令（`package.json`）：

- `npm run build`：`tsc --noEmit && vite build`
- `npm run dev`：`vite build --watch`

构建产物：

- `dist/popup.html`
- `dist/assets/popup.js`
- `dist/assets/background.js`
- `dist/manifest.json`（来自 `public/manifest.json`）

---

## 8) Fast Modification Guide (for LLM)

按需求类型定位：

- **调整 crawl 主流程**：`src/popup.ts`
- **调整任务轮询/进度条/日志**：`src/popup/monitorFlow.ts`
- **调整 index 选链接 UI/交互**：`src/popup/linkSelectionFlow.ts`
- **调整配置弹窗**：`src/popup/configFlow.ts`
- **调整导出弹窗**：`src/popup/exportFlow.ts`
- **调整预览弹窗**：`src/popup/previewFlow.ts`
- **新增/修改 DOM 元素引用**：`src/popup/dom.ts`
- **新增任务字段类型**：`src/popup/types.ts`

---

## 9) Known Constraints

- `API_BASE` 当前是硬编码：`http://localhost:8910`
- Extension 依赖本地后端在线，否则 UI 只会展示错误状态
- 任务轮询间隔固定为 2 秒（`monitorFlow.ts`）

---

## 10) Minimal Mental Model

可以把系统理解为：

**“popup.ts 负责编排，子模块负责局部交互，后端 task API 负责长任务状态机。”**

