# PROJECT CONTEXT: UniAdmission Agent

## 1. Project Goal
Build a trusted, self-updating database of university admission requirements. Initial focus: Hong Kong (18 Universities).

## 2. Your Role as an Agent
You are a **Senior Automation Architect**. You write robust, stealth-focused Python code. You prioritize data accuracy over speed.

## 3. Core Technical Decisions (DO NOT RE-ARGUE)
- **Tooling:** Use `playwright-extra` with `Stealth` plugin for all browsing.
- **Data Flow:** Web -> Markdown -> LLM -> Pydantic Model -> SQLite.
- **Anti-Detection:**
    - Use random user agents.
    - Implement random delays between actions.
    - Avoid direct API calls to endpoints that are protected by TLS fingerprinting.
- **LLM Usage:**
    - Use **Gemini Flash** for high-volume text extraction.
    - Use **Claude** for complex script generation and debugging.

## 4. Data Schema Definition (Draft)
Every extraction task must target these fields:
- `university_name` (string)
- `program_name` (string)
- `degree_level` (UG/PG)
- `gpa_requirement` (float/string)
- `language_tests` (dict: {toefl, ielts})
- `deadlines` (list of dates)
- `source_url` (string)

## 5. Workflow Strategy
1. **Recon:** Identify URL patterns.
2. **Fetch:** Save content to `data/raw_markdown/`.
3. **Analyze:** Extract structured data.
4. **Diff:** Compare with `admission.db` to detect updates.
