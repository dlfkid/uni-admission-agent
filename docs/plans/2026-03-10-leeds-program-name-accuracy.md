# Leeds Index->Detail Program Name Accuracy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix program-name extraction in index->detail flows (especially Leeds-style pages) so anchor/url signals are prioritized, low-confidence names are LLM-resolved by default, and unresolved names are skipped from DB writes.

**Architecture:** Introduce a dedicated name-resolution layer between structured extraction and validation/persist. Keep fast deterministic scoring for most pages, and trigger one-shot LLM fallback only for low-confidence/conflicting candidates. Propagate unresolved URL diagnostics to task/crawl outputs while enforcing a hard gate: unresolved records never enter persist/taxonomy learning.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, existing `LLMCleanerAgent` router, subject taxonomy service, pytest.

---

### Task 1: Add failing tests for noisy plain-title rejection (Leeds symptom guard)

**Files:**
- Modify: `tests/test_scrapers_helpers.py`
- Modify: `src/scrapers/helpers.py`

**Step 1: Write failing tests for requirement-sentence false positive**

```python
def test_extract_program_name_ignores_requirement_sentence_with_degree_keyword() -> None:
    markdown = """
A bachelor degree with a 2:1 (hons) in any subject.

# AI for Business MSc
## Year of entry 2026
"""
    assert extract_program_name(markdown) == "AI for Business MSc"


def test_extract_program_name_ignores_whats_new_when_heading_exists() -> None:
    markdown = """
## What's New
### Masters Discovery Fair
# Master of Science in Asset and Wealth Management
"""
    assert extract_program_name(markdown) == "Master of Science in Asset and Wealth Management"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scrapers_helpers.py::test_extract_program_name_ignores_requirement_sentence_with_degree_keyword -v`  
Expected: FAIL (current logic may return the requirement sentence).

**Step 3: Write minimal helper changes**

```python
_REQUIREMENT_SENTENCE_RE = re.compile(r"\b(entry requirements?|a bachelor degree|hons|ielts|to apply)\b", re.I)

if _REQUIREMENT_SENTENCE_RE.search(candidate):
    continue
```

**Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_scrapers_helpers.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_scrapers_helpers.py src/scrapers/helpers.py
git commit -m "fix(scraper): reject requirement/noise lines in program-name extraction"
```

### Task 2: Add failing tests for dedicated program-name resolver behavior

**Files:**
- Create: `tests/test_program_name_resolution.py`
- Create: `src/services/program_name_resolution.py`

**Step 1: Write failing tests for source-priority and fallback triggers**

```python
def test_index_mode_prefers_anchor_over_markdown_noise() -> None:
    result = resolve_program_name(
        markdown_name="A bachelor degree with a 2:1 (hons)",
        selected_anchor_text="AI for Business MSc",
        detail_url="https://courses.leeds.ac.uk/k198/ai-for-business-msc",
        html_title="AI for Business MSc | University of Leeds",
        is_index_mode=True,
    )
    assert result.status == "resolved"
    assert result.name == "AI for Business MSc"
    assert result.source == "anchor"


def test_low_confidence_triggers_llm_fallback_once(monkeypatch) -> None:
    fake_router = FakeRouterReturning("{\"name\": \"AI for Business MSc\", \"confidence\": 0.91}")
    result = resolve_program_name(..., router=fake_router, llm_fallback_enabled=True)
    assert result.status == "resolved"
    assert result.source == "llm"
    assert fake_router.calls == 1


def test_unresolved_when_llm_still_low_confidence() -> None:
    fake_router = FakeRouterReturning("{\"name\": \"\", \"confidence\": 0.41}")
    result = resolve_program_name(..., router=fake_router, llm_fallback_enabled=True)
    assert result.status == "unresolved"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_program_name_resolution.py -v`  
Expected: FAIL (resolver module not implemented yet).

**Step 3: Commit failing tests**

```bash
git add tests/test_program_name_resolution.py
git commit -m "test(name-resolution): add failing source-priority and llm-fallback tests"
```

### Task 3: Implement the program-name resolver + evidence pack + one-shot LLM fallback

**Files:**
- Create: `src/services/program_name_resolution.py`
- Create: `src/agents/prompts/resolve_program_name.txt`
- Modify: `src/scrapers/helpers.py`

**Step 1: Implement resolver models and deterministic scoring**

```python
@dataclass
class NameResolutionResult:
    status: Literal["resolved", "unresolved"]
    name: str
    confidence: float
    source: str
    reason: str
    top_candidates: list[dict[str, Any]]
```

```python
def resolve_program_name(...):
    candidates = _build_candidates(...)
    ranked = _rank_candidates(candidates, taxonomy_matches)
    if _can_accept_rule_result(ranked, low_threshold, conflict_delta):
        return _resolved_from_ranked(ranked)
    if llm_fallback_enabled:
        return _resolve_with_llm_once(ranked, evidence_pack, router, timeout_seconds)
    return _unresolved("low_confidence", ranked)
```

**Step 2: Implement evidence-pack builder (signal-driven, not first-chunk driven)**

```python
def build_evidence_pack(...):
    return {
        "anchor_text": selected_anchor_text,
        "url": detail_url,
        "slug": slug_signal,
        "title": html_title,
        "headings": headings[:6],
        "keyword_chunks": keyword_chunks[:3],
        "candidates": top_candidates[:5],
    }
```

**Step 3: Implement LLM prompt + strict JSON parse/timeout path**

```python
response = router.generate(prompt_text, ProgramNameLLMOutput)
if parsed.confidence < low_threshold:
    return _unresolved("llm_low_confidence", ranked)
```

**Step 4: Run resolver tests**

Run: `uv run pytest tests/test_program_name_resolution.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/services/program_name_resolution.py src/agents/prompts/resolve_program_name.txt src/scrapers/helpers.py
git commit -m "feat(name-resolution): add deterministic ranking with one-shot llm fallback"
```

### Task 4: Add failing ingestion-pipeline tests for unresolved gate and diagnostics

**Files:**
- Modify: `tests/test_taxonomy_name_resolution.py`
- Modify: `tests/test_ingestion_pipeline.py`
- Modify: `src/services/ingestion_pipeline.py`

**Step 1: Write failing test that unresolved name is skipped from candidates**

```python
def test_extract_structured_skips_unresolved_program_name(monkeypatch) -> None:
    pipeline = IngestionPipeline(db_manager=MagicMock())
    monkeypatch.setattr("src.services.ingestion_pipeline.resolve_program_name", lambda **_: unresolved())

    result = pipeline._stage_extract_structured(request_payload, context)
    assert result["extracted_count"] == 0
    assert len(result["unresolved_urls"]) == 1
```

**Step 2: Write failing test that unresolved never reaches persist_versioned**

```python
def test_persist_versioned_not_called_for_unresolved(monkeypatch) -> None:
    # unresolved entries absent from validated_programs pipeline path
    ...
```

**Step 3: Run tests to verify fail**

Run: `uv run pytest tests/test_taxonomy_name_resolution.py tests/test_ingestion_pipeline.py -v`  
Expected: FAIL.

**Step 4: Commit failing tests**

```bash
git add tests/test_taxonomy_name_resolution.py tests/test_ingestion_pipeline.py
git commit -m "test(ingestion): add failing unresolved-name gate and diagnostics tests"
```

### Task 5: Integrate resolver into extract stage and enforce unresolved no-persist gate

**Files:**
- Modify: `src/services/ingestion_pipeline.py`
- Modify: `src/scrapers/page_processor.py`
- Modify: `src/services/subject_taxonomy.py` (reuse match score only; no extra DB queries)

**Step 1: Wire resolver call per raw page in `_stage_extract_structured`**

```python
resolution = resolve_program_name(
    markdown=page.markdown,
    extracted_name=str(program_data.get("name_en") or ""),
    selected_anchor_text=row.get("selected_anchor_text"),
    detail_url=page.url,
    html=page.html,
    is_index_mode=is_index_mode_request,
    taxonomy_matches=taxonomy_matches,
    router=cleaner.router,
    llm_fallback_enabled=name_resolution_llm_enabled,
)
```

**Step 2: Apply gate**

```python
if resolution.status != "resolved":
    unresolved_urls.append({...})
    extract_errors.append({...})
    logger.warning("program-name unresolved, skipped url=%s reason=%s", page.url, resolution.reason)
    continue
program_data["name_en"] = resolution.name
```

**Step 3: Extend stage context keys and final job result payload**

```python
STAGE_CONTEXT_KEYS[IngestionStage.EXTRACT_STRUCTURED] += ("unresolved_urls",)
```

**Step 4: Run pipeline tests**

Run: `uv run pytest tests/test_taxonomy_name_resolution.py tests/test_ingestion_pipeline.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/services/ingestion_pipeline.py src/scrapers/page_processor.py src/services/subject_taxonomy.py
git commit -m "feat(ingestion): resolve names before validate and skip unresolved records"
```

### Task 6: Add request-level knobs (default enabled) and plumb through crawl/API layers

**Files:**
- Modify: `src/api/schemas.py`
- Modify: `src/api/server.py`
- Modify: `src/services/crawler.py`
- Modify: `tests/test_api_taxonomy_overrides.py`
- Create: `tests/test_api_name_resolution_overrides.py`

**Step 1: Add new optional request fields with defaults**

```python
name_resolution_llm_enabled: Optional[bool] = Field(default=None)
name_resolution_low_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
name_resolution_conflict_delta: Optional[float] = Field(default=None, ge=0.0, le=1.0)
```

**Step 2: Plumb fields from API -> crawl_url -> run_new_job payload**

```python
result = await crawl_url(..., name_resolution_llm_enabled=body.name_resolution_llm_enabled, ...)
```

**Step 3: Add validation tests and plumbing tests (failing first, then pass)**

Run: `uv run pytest tests/test_api_name_resolution_overrides.py tests/test_api_taxonomy_overrides.py -v`

**Step 4: Verify backward compatibility**

Run: `uv run pytest tests/test_api_crawl_browser_provider.py tests/test_crawler_service_phase2.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/api/schemas.py src/api/server.py src/services/crawler.py tests/test_api_name_resolution_overrides.py tests/test_api_taxonomy_overrides.py
git commit -m "feat(api): expose name-resolution fallback controls with safe defaults"
```

### Task 7: Expose unresolved URLs in crawl results and task-facing payloads

**Files:**
- Modify: `src/services/crawler.py`
- Modify: `src/api/server.py`
- Modify: `tests/test_crawler_service_phase2.py`
- Modify: `tests/test_api_crawl_browser_provider.py`

**Step 1: Extend `CrawlResult` model**

```python
unresolved_urls: list[dict[str, Any]] = Field(default_factory=list)
```

**Step 2: Populate from ingestion result and keep logs explicit**

```python
unresolved_urls = list(result.get("unresolved_urls") or [])
logger.warning("Crawl completed with unresolved program names: %d", len(unresolved_urls))
```

**Step 3: Add tests for response payload propagation**

```python
assert result.unresolved_urls == [{"url": "...", "reason": "llm_low_confidence"}]
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_crawler_service_phase2.py tests/test_api_crawl_browser_provider.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/services/crawler.py src/api/server.py tests/test_crawler_service_phase2.py tests/test_api_crawl_browser_provider.py
git commit -m "feat(crawl): return unresolved url diagnostics for skipped records"
```

### Task 8: Leeds regression validation + full verification sweep

**Files:**
- Modify: `tests/test_page_processor.py`
- Create: `tests/test_leeds_program_name_regression.py`
- Modify: `docs/changelog_phase1_data_layer.md` (append Phase 3 name-resolution entry)

**Step 1: Add Leeds regression test using golden sample files**

```python
def test_leeds_detail_resolves_ai_for_business_name(...) -> None:
    markdown = Path("golden_samples/cases/leeds_masters_ai_business/detail.md").read_text()
    result = resolve_program_name(...)
    assert result.name == "AI for Business MSc"
```

**Step 2: Run targeted regression tests**

Run: `uv run pytest tests/test_leeds_program_name_regression.py tests/test_program_name_resolution.py -v`  
Expected: PASS.

**Step 3: Run full verification set for touched areas**

Run: `uv run pytest tests/test_scrapers_helpers.py tests/test_page_processor.py tests/test_taxonomy_name_resolution.py tests/test_ingestion_pipeline.py tests/test_crawler_service_phase2.py tests/test_api_name_resolution_overrides.py -v`  
Expected: PASS.

**Step 4: Lint verification**

Run: `uv run pylint $(git ls-files '*.py')`  
Expected: exit code 0.

**Step 5: Commit**

```bash
git add tests/test_page_processor.py tests/test_leeds_program_name_regression.py docs/changelog_phase1_data_layer.md
git commit -m "test(leeds): add regression coverage and verify name-resolution pipeline"
```
