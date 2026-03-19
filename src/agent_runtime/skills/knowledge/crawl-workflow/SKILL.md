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
2. Use `crawl_detail_batch_skill` with the selected URLs, providing:
   - `index_url`: the original index page URL
   - `selected_urls`: the chosen detail page URLs
   - `univ_slug`: the university identifier
   - `year`: the academic year

## Step 3b: Detail Page Flow
If the page is a detail page:
1. Use `crawl_detail_batch_skill` directly with just that one URL.

## Step 4: Verify Results
Use `query_db_skill` to check what was persisted to the database.
Report the number of programs imported and any errors.

## Common Pitfalls
- Always fetch HTML before analyzing — `analyze_page_skill` needs `html_content`.
- For batch crawls, keep batch sizes reasonable (10-20 URLs per batch).
- If `crawl_detail_batch_skill` partially fails, report which URLs succeeded/failed.
