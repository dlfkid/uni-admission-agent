# Auto-Pagination for Index Pages

**Date**: 2026-04-12
**Status**: Approved
**Scope**: Phase 1 — URL parameter pagination + large single-page batching

---

## 1. Problem Statement

When a university's index page lists courses across multiple pages (e.g. Edinburgh has 72 pages, Leeds has 19 pages), users must manually trigger crawl for each page. This is tedious and impractical for large universities.

Additionally, single-page indexes with hundreds of links (e.g. UCL with 453 courses) process all links in one shot without quality control, risking massive token waste on degraded extractions.

### Pagination Patterns Observed (Golden Samples)

| University | HTML Size | Pattern | Detail |
|------------|-----------|---------|--------|
| Edinburgh | 105K | **URL param** | `?page=0` to `?page=71`, 72 pages |
| Leeds | 84K | **URL param** | `?page=1&start_rank=1` to `?page=19`, 19 pages |
| Manchester | 69K | **AJAX one-shot** | jQuery `$.getJSON`, all courses loaded at once. CDP client already captures rendered HTML |
| UCL | 492K | **Single page** | ~453 degree links, no pagination |
| PolyU | 252K | **Single page** | ~127 detail links, `swiper-pagination` is image carousel only |
| NUS | SPA | **SPA button** | Salesforce LWR, 244 programmes across 25 pages, JS-driven `<button data-page="N">`, URL never changes. **Phase 2 target** |

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Approach | New `paginated_crawl_skill` | Encapsulates pagination loop, quality checks, and batch extraction in one skill. Zero LLM overhead for page navigation (heuristic only). Minimal agent system prompt change |
| Trigger | User explicit opt-in | Extension checkbox / CLI natural language / MCP parameter. Prevents accidental large crawls |
| Quality control | Circuit breaker every 10 courses | Two-layer: fast heuristic pre-check + LLM review only when uncertain. Stops immediately on failure |
| NUS SPA pagination | Phase 2 | Requires CDP button clicking + DOM change detection. Phase 1 detects and reports "not supported" |

## 3. Architecture

### 3.1 New Skill: `paginated_crawl_skill`

Registered in `SkillRegistry` alongside existing skills. Parallel to `browser_automation_skill` — does not modify it.

**Data flow:**

```
User triggers "auto-paginate"
  -> Agent calls paginated_crawl_skill(url, univ_slug, year)
    -> Skill fetches page 1 HTML via ClientAutomationBridge
    -> Pagination Detector (heuristic) extracts:
        - pagination_type: "url_param" | "single_page" | "spa_button"
        - page_urls: list of all page URLs
        - total_pages
    -> Page loop:
        - Extract course links from current page (LLM link filter)
        - Fetch & extract detail pages (reuse _auto_fetch_and_extract, batched)
        - Every 10 courses -> Quality Circuit Breaker
        - If PASS -> fetch next page
        - If FAIL -> stop, return warning + accumulated data
    -> Return all programs + pagination summary
  -> Agent calls persist_programs_skill(programs) if status == "done"
  -> Agent reports summary to user
```

### 3.2 Trigger Mechanism

| Entry Point | Trigger | Delivery |
|-------------|---------|----------|
| **Extension** | New checkbox "Auto-paginate" (unchecked by default, visible when page type is index/auto) | `submitAgentRun` payload: `auto_paginate: true` |
| **CLI chat** | Natural language keywords: "翻页", "all pages", "paginate", "收集全部课程", etc. | Agent system prompt pattern matching -> calls `paginated_crawl_skill` |
| **MCP / REST** | `auto_paginate` parameter on `/agent/run` and `agent_run` MCP tool | Passthrough to `AgentRequest.payload` |

### 3.3 Agent System Prompt Addition

```
## For index pages with auto-pagination requested:
1. Call paginated_crawl_skill(url=<given URL>, univ_slug, year).
2. The skill handles pagination detection, multi-page fetching,
   quality checks, and extraction internally.
3. If status is "done": call persist_programs_skill ONCE with the
   returned programs array.
4. If status is "quality_failed": report the warning to the user.
   Do NOT call persist_programs_skill — let the user decide.
5. If status is "pagination_not_supported": inform the user that
   SPA pagination was detected and auto-pagination is not yet supported.
```

## 4. Pagination Detector

Pure heuristic module. Accepts index page HTML, returns pagination metadata. No LLM calls.

### 4.1 Output Model

```python
class PaginationInfo(BaseModel):
    pagination_type: Literal["url_param", "single_page", "spa_button"]
    page_urls: list[str] = []        # Complete URL list for all pages
    total_pages: int | None = None
    current_page: int = 1
    confidence: float = 0.0          # 0.0-1.0
```

### 4.2 Detection Strategies (priority order)

**Strategy 1: Pagination container scan**

Match `<nav aria-label="Pagination">`, `<ul class="pagination">`, `<nav class="*pagination*">`, `<ol class="*pagination*">`.

Extract all `<a href="...">` links within the container. Parse URL parameters to identify the page parameter (`page`, `p`, `offset`, `start_rank`, `pg`).

Covers: Edinburgh (`<nav aria-label="Pagination">` + `?page=0..71`), Leeds (`<nav class="uol-pagination">` + `?page=1..19`).

**Strategy 2: Loose page-link scan**

When Strategy 1 finds no container, scan all `<a>` tags in the page for pagination-like URL parameters:
```
href="[^"]*[?&](page|p|offset|start_rank|pg)=(\d+)[^"]*"
```
Require the same parameter name to appear with >= 3 distinct numeric values. Lower confidence than Strategy 1.

**Strategy 3: SPA button detection (Phase 2 marker)**

Detect `<button data-page="N">`, `<button aria-label="Next page">` (without `href`), or Salesforce LWC pagination patterns. Return `pagination_type="spa_button"`, `page_urls=[]`. Phase 1 does not paginate, only detects and notifies.

**Strategy 4: No pagination fallback**

None of the above matched. Return `pagination_type="single_page"`, `total_pages=1`. Skill degrades to large single-page batch mode.

### 4.3 Page URL List Construction

Prefer extracting complete URLs directly from pagination links rather than generating templates. For pages where the pagination container only shows a subset (e.g. `1, 2, 3 ... 72`):

1. Extract the "Last page" link (e.g. `?page=71` from Edinburgh's `aria-label="Last page"`)
2. Extract the URL pattern from any two consecutive page links
3. Generate intermediate URLs by incrementing the page parameter

For multi-parameter pagination (Leeds: `?page=2&start_rank=16&type=PGT&term=202627`):
- Identify which parameter is the page counter vs. which are static filters
- Keep static parameters from page 1, only vary the page counter
- Validate by checking that the "Next page" link's non-page parameters match page 1

## 5. Quality Circuit Breaker

### 5.1 Trigger Frequency

- Every **10 courses** extracted
- First batch (courses 1-10) is always checked — fail fast on bad pages
- Final batch (< 10 courses remaining) is also checked

### 5.2 Two-Layer Check

**Layer 1: Heuristic pre-check (zero LLM cost)**

| Check | Rule | Weight |
|-------|------|--------|
| `name_en` non-empty and non-noise | Reuse existing `is_noise_program_name()` | High |
| `name_en` dedup ratio | >= 5 duplicate names in 10 -> anomaly | High |
| Key field fill rate | At least 1 of `faculty`, `tuition_amount`, `study_options` has value | Medium |
| `name_en` length | 5 <= length <= 200 characters | Low |

Compute `heuristic_score` (0.0 - 1.0):
- `>= 0.7` -> **PASS** (skip LLM)
- `< 0.4` -> **FAIL** (skip LLM)
- `0.4 - 0.7` -> delegate to Layer 2

**Layer 2: LLM quality review (only when heuristic is uncertain)**

Input: course name list + key field summary (~500 tokens).
Output: `{"verdict": "PASS"|"FAIL", "reason": "..."}` (~50 tokens).

Expected: 80%+ of batches resolved by Layer 1 alone.

### 5.3 Breaker Behavior on FAIL

1. **Stop pagination immediately** — no more pages fetched
2. **Preserve accumulated data** — already extracted programs are not discarded
3. **Return detailed warning** — which page, which program count, what failed
4. **Agent does NOT auto-persist** — returns `status="quality_failed"` with the accumulated `programs` array. The agent presents the warning and asks the user whether to save the already-extracted data or discard. If the user confirms, agent calls `persist_programs_skill` with the returned programs

### 5.4 Output Model

```python
class QualityCheckResult(BaseModel):
    verdict: Literal["pass", "fail"]
    heuristic_score: float
    llm_used: bool
    reason: str
    failed_at_page: int | None = None
    failed_at_program_count: int | None = None
```

## 6. Skill Input/Output Contracts

### 6.1 Input

```python
class PaginatedCrawlSkillInput(BaseModel):
    url: str = Field(min_length=1)
    univ_slug: str = Field(min_length=1)
    year: int = Field(gt=0)
    max_pages: int = Field(default=50, ge=1, le=200)
    batch_quality_size: int = Field(default=10, ge=5, le=50)
    client_id: str | None = None
```

### 6.2 Output

```python
class PaginatedCrawlSkillOutput(BaseModel):
    status: Literal["done", "quality_failed", "pagination_not_supported"]
    pagination_type: str
    total_pages_detected: int | None = None
    pages_processed: int = 0
    programs: list[dict] = []
    total_programs: int = 0
    quality_scores: list[dict] = []
    warning: str | None = None
    summary: str = ""
```

## 7. Progress Events

The skill emits events via `event_sink` for real-time monitoring:

```python
# Page progress
{"type": "pagination_progress", "page": 3, "total_pages": 19, "programs_so_far": 45}

# Quality check passed
{"type": "quality_check_passed", "batch_index": 2, "heuristic_score": 0.85}

# Quality check failed (breaker tripped)
{"type": "quality_check_failed", "batch_index": 5, "reason": "6/10 names are noise"}

# Pagination detection result
{"type": "pagination_detected", "type": "url_param", "total_pages": 19}
```

## 8. Integration Points

### 8.1 Existing Code Modifications

| File | Change |
|------|--------|
| `src/agent_runtime/skills/impl/common.py` | Refactor `_auto_fetch_and_extract` to support batched execution with callback after each batch |
| `src/agent_runtime/skills/registry.py` | Register `paginated_crawl_skill` |
| `src/agent_runtime/loop.py` | Add tool description + system prompt rule for pagination |
| `src/agent_runtime/pydanticai_runtime.py` | Append pagination instruction to user message when `auto_paginate=True` |
| `src/api/server.py` | Add `auto_paginate` field to `AgentRunRequest` |
| `src/api/schemas.py` | Update MCP schema |
| `extension/src/popup/crawlFlow.ts` | Add checkbox + progress display |
| `extension/src/popup/crawlApi.ts` | Add `autoPaginate` to payload |
| `extension/src/popup/dom.ts` | Add DOM reference for new checkbox |

### 8.2 New Files

| File | Purpose |
|------|---------|
| `src/agent_runtime/skills/impl/paginated_crawl.py` | Skill handler + main loop |
| `src/agent_runtime/skills/impl/pagination_detector.py` | Heuristic pagination detection |
| `src/agent_runtime/skills/impl/quality_circuit_breaker.py` | Two-layer quality checking |
| `tests/test_pagination_detector.py` | Unit tests for detection strategies |
| `tests/test_quality_circuit_breaker.py` | Unit tests for quality checking |
| `tests/test_paginated_crawl_skill.py` | Integration tests for skill handler |

## 9. Phase 2: SPA Button Pagination (Future)

Target: NUS (`study.nus.edu.sg/programme`) and similar Salesforce/React SPA sites.

### NUS Characteristics
- Built on Salesforce Experience Cloud (Lightning Web Runtime)
- 244 programmes, 10 per page, 25 pages
- Pagination via `<button data-page="N">` — no URL change
- Detail links point to external subdomains (`cde.nus.edu.sg`, `duke-nus.edu.sg`, etc.)
- Index cards already contain rich info (title, description, intake, mode, faculty)

### Phase 2 Capabilities Required
1. **CDP button clicking**: `Runtime.evaluate` to click `[data-page="N"]` buttons
2. **DOM change detection**: Poll for content update after click (no `readyState` change in SPA)
3. **Shadow DOM traversal**: Salesforce LWC uses shadow roots
4. **Index-page direct extraction**: Skip detail page fetching when index cards have sufficient data

### Phase 1 Preparation
- `PaginationInfo.pagination_type="spa_button"` detection is implemented in Phase 1
- Skill returns `status="pagination_not_supported"` with explanation
- No dead code — detection is useful feedback even without SPA pagination support
