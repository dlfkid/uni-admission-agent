# UniAdmission Agent

**Autonomous LLM-powered engine for aggregating and synchronizing global university admission requirements into a structured database.**

## 🎯 Overview
This project automates the collection of admission criteria from world-renowned universities. It uses an **Agentic Workflow** to handle dynamic web content, bypass anti-detection mechanisms, and transform unstructured web data into verified JSON schemas.

## 🛠 Tech Stack
- **Engine:** Python 3.12+ (managed by `pyenv`)
- **Intelligence:** Gemini 2.0 Flash / Claude 3.5 Sonnet
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

# Import Excel data (Strict Regex only by default)
uv run src/main.py import example/hku-26-27.xlsx

# Import Excel data with LLM Fallback (Analyzes complex fields)
uv run src/main.py import example/hku-26-27.xlsx --llm

# Check database status
uv run src/main.py status
```

## 🤖 Agentic Principles
- **Stealth First:** Never trigger bot detection; emulate human behavior.
- **Markdown-Centric:** Convert HTML to Markdown before LLM processing to save tokens.
- **Verified Output:** All data must pass Pydantic validation before being committed to the database.