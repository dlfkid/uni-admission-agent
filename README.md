# UniAdmission Agent

**Autonomous LLM-powered engine for aggregating and synchronizing global university admission requirements into a structured database.**

## 🎯 Overview
This project automates the collection of admission criteria from world-renowned universities. It uses an **Agentic Workflow** to handle dynamic web content, bypass anti-detection mechanisms, and transform unstructured web data into verified JSON schemas.

## 🛠 Tech Stack
- **Engine:** Python 3.12+ (managed by `pyenv`)
- **Intelligence:** Gemini 2.0 Flash / DeepSeek / VolcEngine (豆包)
- **Automation:** Playwright with Stealth Plugin
- **Extraction:** Crawl4AI / Firecrawl (Markdown-first approach)
- **Validation:** Pydantic (Strongly typed schemas)
- **Storage:** SQLite

## 🚀 Getting Started
1. `pyenv local 3.12.0`
2. `python -m venv venv && source venv/bin/activate`
3. `pip install -e .`
4. Copy `.env.example` to `.env` and add your API keys.
4. Copy `.env.example` to `.env` and add your API keys.


## 📖 Usage
Run the main CLI to interact with the engine:

```bash
# General environment check
uv run src/main.py check

# Start the crawling task (Placeholder for now)
uv run src/main.py run

# Import Excel data (Strict Regex only by default)
# Arguments:
#   --name: University slug (lowercase, numbers, hyphens only, e.g., hku)
#   --year: Academic year (e.g., 2026)
#   --file: Path to the XLSX file
#   --llm:  Enable LLM analysis for missing data (optional)
# Example:
uv run src/main.py import --name hku --year 2026 --file example/hku-26-27.xlsx

# Import Excel data with LLM Fallback
uv run src/main.py import --name hku --year 2026 --file example/hku-26-27.xlsx --llm

# Export data to Excel
# Arguments:
#   --name:   University slug
#   --output: Output file path
#   --year:   Academic year (optional, defaults to all years)
# Example:
uv run src/main.py export --name hku --output hku_export.xlsx --year 2026

# Crawl a URL and import admission data
# Arguments:
#   --name:      University slug (lowercase, numbers, hyphens only)
#   --year:      Academic year (e.g., 2026)
#   --url:       Starting URL to crawl
#   --continue:  Extra depth for LLM-driven scouting (default: 0)
# Examples:
uv run src/main.py crawl --name hku --year 2026 --url https://admissions.hku.hk/programmes
uv run src/main.py crawl --name hku --year 2026 --url https://admissions.hku.hk/programmes --continue 2

# Check database status
uv run src/main.py status
```

## 🤖 Agentic Principles
- **Stealth First:** Never trigger bot detection; emulate human behavior.
- **Markdown-Centric:** Convert HTML to Markdown before LLM processing to save tokens.
- **Verified Output:** All data must pass Pydantic validation before being committed to the database.

## Action trail
```mermaid
graph TD
    A[开始任务: Task Runner] --> B{本地状态检查}
    B -->|首次运行| C[发现阶段: Discovery Agent]
    B -->|增量更新| D[同步阶段: Sync Monitor]

    subgraph "Phase 1: 智能侦察 (Reconnaissance)"
        C --> C1[访问大学官网主页]
        C1 --> C2[Gemini 识别 Admission/Requirement 深度链接]
        C2 --> C3[构建待爬取 URL 队列]
    end

    subgraph "Phase 2: 隐身抓取 (Stealth Crawling)"
        D & C3 --> E[Playwright Stealth 模拟访问]
        E --> E1[执行真人模拟操作: 滚动/点击]
        E1 --> E2[HTML 内容捕获]
        E2 --> E3[Crawl4AI 转化为 Markdown]
    end

    subgraph "Phase 3: 语义提取 (Intelligence Extraction)"
        E3 --> F[计算页面 Hash]
        F -->|Hash 没变| G[跳过处理: 节省 Token]
        F -->|Hash 已变| H[Gemini Flash 解析 Markdown]
        H --> I[按照 Pydantic Schema 生成 JSON]
    end

    subgraph "Phase 4: 校验与存储 (Persistence)"
        I --> J{Pydantic 自动验证}
        J -->|失败| K[标记异常: 人工干预/重试]
        J -->|通过| L[写入 SQLite 数据库]
        L --> M[更新版本快照]
    end

    M --> N[生成更新报告]
    N --> O[结束]
```