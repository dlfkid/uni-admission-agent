---
name: crawl-workflow
description: Step-by-step workflow for crawling university admission pages
---

# Crawl Workflow

## Step 1: Fetch Page HTML
Use `browser_automation_skill` to fetch the HTML content of the target URL.
This gives you the raw HTML needed for analysis.

## Step 2: Analyze Page Type
Use `analyze_page_skill` with the fetched HTML to determine whether the page is:
- **index**: A listing page with links to multiple program detail pages.
- **detail**: A single program's information page.

## Step 3a: Index Page Flow
If the page is an index:
1. Use `select_detail_candidates_skill` to pick the best candidate URLs
   from the links returned by the analysis.
2. For each selected URL, use `browser_automation_skill` to fetch the detail
   page HTML, then use `persist_programs_skill` to store the extracted data.

**Alternative (legacy, NOT dry-run compatible):**
Use `legacy_crawl_batch_skill` to run the traditional ingestion pipeline
(fetch + LLM parse + DB persist in one shot). This bypasses agent-driven
extraction and does not support dry-run mode.

## Step 3b: Detail Page Flow
If the page is a detail page:
1. Use `browser_automation_skill` to fetch the HTML.
2. Use `persist_programs_skill` to store extracted program data.

**Alternative (legacy):** Use `legacy_crawl_batch_skill` directly with that URL.

## Step 4: Verify Results
Use `query_db_skill` to check what was persisted to the database.
Report the number of programs imported and any errors.

## Common Pitfalls
- Always fetch HTML before analyzing — `analyze_page_skill` needs `html_content`.
- For batch crawls, keep batch sizes reasonable (10-20 URLs per batch).
- `legacy_crawl_batch_skill` does NOT support dry-run mode — avoid it when
  dry_run is active.
