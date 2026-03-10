# Auto Page-Type Accuracy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `auto` mode page-type classification reliably choose `index` vs `detail`, and enforce a permanent golden-sample gate where all current and future golden cases must pass.

**Architecture:** Introduce a dedicated two-stage classifier for `auto`: deterministic rule scoring first, then one-shot LLM fallback only for uncertain cases. Wire structured decision traces into logs/results, remove detail-biased fallback behavior, and add golden-sample regression tests (index+detail) as CI gate.

**Tech Stack:** Python 3.12, pytest, existing crawler/ingestion pipeline, RouterAgent-based LLM integration, golden_samples fixtures.

---

### Task 1: Add failing golden auto-classification regression tests (index + detail)

**Files:**
- Create: `tests/test_auto_page_type_golden.py`
- Reference: `golden_samples/manifest.json`
- Reference: `golden_samples/cases/*/index.md`
- Reference: `golden_samples/cases/*/detail.md`

**Step 1: Write failing tests for all golden cases**

```python
import json
from pathlib import Path

import pytest

from src.models.scraper_models import PageType
from src.scrapers.link_parser import detect_page_type


def _load_manifest() -> dict:
    return json.loads(Path("golden_samples/manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_manifest()["cases"])
def test_auto_detects_golden_index_pages(case: dict) -> None:
    case_dir = Path("golden_samples/cases") / case["case_id"]
    markdown = (case_dir / "index.md").read_text(encoding="utf-8")
    detected = detect_page_type(markdown=markdown, link_count=0, page_url=case["index_url"])
    assert detected == PageType.INDEX


@pytest.mark.parametrize("case", _load_manifest()["cases"])
def test_auto_detects_golden_detail_pages(case: dict) -> None:
    case_dir = Path("golden_samples/cases") / case["case_id"]
    markdown = (case_dir / "detail.md").read_text(encoding="utf-8")
    detected = detect_page_type(markdown=markdown, link_count=0, page_url=case["detail_url"])
    assert detected == PageType.DETAIL
```

**Step 2: Run tests to verify failures**

Run: `uv run pytest tests/test_auto_page_type_golden.py -v`  
Expected: FAIL for current heuristics on at least one index/detail sample.

**Step 3: Commit failing tests**

```bash
git add tests/test_auto_page_type_golden.py
git commit -m "test(auto-page-type): add failing golden index/detail classification tests"
```

### Task 2: Add classifier result model + failing unit tests for two-stage decision

**Files:**
- Create: `src/services/page_type_resolution.py`
- Create: `tests/test_page_type_resolution.py`

**Step 1: Write failing unit tests for rule/uncertain/llm/fallback paths**

```python
from src.services.page_type_resolution import classify_page_type_auto


class FakeRouter:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def generate(self, *_args, **_kwargs):
        self.calls += 1
        return self.text


def test_rule_confident_index_no_llm() -> None:
    result = classify_page_type_auto(
        url="https://courses.leeds.ac.uk/course-search/masters-courses",
        markdown="Find your course\nFilters\nBrowse by subject",
        html="",
        link_count=50,
        router=None,
    )
    assert result.page_type == "index"
    assert result.decision_source == "rule"


def test_uncertain_triggers_llm_once() -> None:
    router = FakeRouter('{"page_type": "index", "confidence": 0.84, "reason": "listing page"}')
    result = classify_page_type_auto(
        url="https://example.edu/programmes",
        markdown="How to apply\nFind your course",
        html="",
        link_count=8,
        router=router,
    )
    assert result.page_type == "index"
    assert result.decision_source == "llm"
    assert router.calls == 1


def test_llm_failure_falls_back_to_rule_side() -> None:
    router = FakeRouter("not-json")
    result = classify_page_type_auto(
        url="https://example.edu/programmes",
        markdown="How to apply\nFind your course",
        html="",
        link_count=8,
        router=router,
    )
    assert result.decision_source == "rule_fallback"
```

**Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_page_type_resolution.py -v`  
Expected: FAIL (module/function not implemented yet).

**Step 3: Commit failing tests**

```bash
git add tests/test_page_type_resolution.py
git commit -m "test(auto-page-type): add failing two-stage classifier behavior tests"
```

### Task 3: Implement two-stage auto classifier (rule scoring + one-shot LLM fallback)

**Files:**
- Create: `src/services/page_type_resolution.py`
- Create: `src/agents/prompts/classify_page_type_auto.txt`

**Step 1: Implement result dataclass + scoring signal extractors**

```python
@dataclass
class PageTypeDecision:
    page_type: Literal["index", "detail"]
    confidence: float
    decision_source: Literal["rule", "llm", "rule_fallback"]
    reasons: list[str]
    scores: dict[str, float]
```

```python
def _score_rule_signals(url: str, markdown: str, html: str, link_count: int) -> tuple[float, float, list[str]]:
    # URL/content/structure weighted scoring
    ...
```

**Step 2: Implement uncertainty thresholds and LLM escalation policy**

```python
def classify_page_type_auto(...):
    index_score, detail_score, reasons = _score_rule_signals(...)
    margin = abs(index_score - detail_score)
    if margin >= margin_high:
        ...  # rule direct
    if router:
        ...  # one-shot llm parse
    ...      # rule_fallback
```

**Step 3: Implement strict LLM JSON parse and fallback**

```python
parsed = json.loads(response_text)
if parsed_confidence >= llm_confidence_pass:
    return llm_decision
return fallback_decision
```

**Step 4: Run unit tests**

Run: `uv run pytest tests/test_page_type_resolution.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/services/page_type_resolution.py src/agents/prompts/classify_page_type_auto.txt
git commit -m "feat(auto-page-type): add two-stage rule+llm classifier"
```

### Task 4: Integrate classifier into auto flow and expose decision trace logs

**Files:**
- Modify: `src/scrapers/engine.py`
- Modify: `src/services/ingestion_pipeline.py`
- Modify: `src/scrapers/link_parser.py`
- Modify: `tests/test_engine.py`
- Modify: `tests/test_ingestion_pipeline.py`

**Step 1: Route auto mode through new classifier in engine determine logic**

```python
if page_type_hint == "auto" and probe_result:
    decision = classify_page_type_auto(
        url=probe_result.url,
        markdown=probe_result.markdown,
        html=str(probe_result.html or ""),
        link_count=len(probe_result.links),
        router=self.router,
    )
    logger.info("auto page-type decision=%s source=%s scores=%s reasons=%s", ...)
    return decision.page_type == "index"
```

**Step 2: Keep explicit `index/detail` manual hints unchanged**

```python
if page_type_hint == "index":
    return True
if page_type_hint == "detail":
    return False
```

**Step 3: Add/update tests for auto branch integration**

```python
def test_determine_page_type_auto_uses_two_stage_classifier(...):
    ...
```

**Step 4: Run integration-targeted tests**

Run: `uv run pytest tests/test_engine.py tests/test_ingestion_pipeline.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/scrapers/engine.py src/services/ingestion_pipeline.py src/scrapers/link_parser.py tests/test_engine.py tests/test_ingestion_pipeline.py
git commit -m "feat(auto-page-type): integrate two-stage decision and trace logging"
```

### Task 5: Enforce golden gate for all current and future cases

**Files:**
- Modify: `tests/test_auto_page_type_golden.py`
- Modify: `docs/changelog_phase1_data_layer.md`
- Optional Modify: CI workflow file if needed (`.github/workflows/*.yml`)

**Step 1: Make test iterate manifest dynamically (future cases auto-included)**

```python
@pytest.mark.parametrize("case", _load_manifest()["cases"], ids=lambda c: c["case_id"])
def test_auto_detects_golden_index_pages(case):
    ...
```

**Step 2: Add assertion messages for clear CI failures**

```python
assert detected == PageType.INDEX, f"{case['case_id']} index misclassified as {detected}"
```

**Step 3: Document permanent gate rule in changelog/docs**

Append explicit “new golden case must pass auto page-type tests before merge”.

**Step 4: Run golden gate tests**

Run: `uv run pytest tests/test_auto_page_type_golden.py -v`  
Expected: PASS for all current cases.

**Step 5: Commit**

```bash
git add tests/test_auto_page_type_golden.py docs/changelog_phase1_data_layer.md .github/workflows
git commit -m "test(golden): enforce auto page-type gate for all manifest cases"
```

### Task 6: Real LLM validation and full verification sweep

**Files:**
- Modify (if needed): `tests/test_auto_page_type_golden.py` (marker/param for llm mode)
- Optional Create: `scripts/validate_auto_page_type_with_llm.py`

**Step 1: Add explicit real-LLM golden validation entrypoint**

```python
@pytest.mark.integration
def test_auto_page_type_golden_with_real_llm():
    ...
```

Or provide script:

```python
# scripts/validate_auto_page_type_with_llm.py
# loads manifest, runs classifier with real router, prints pass/fail summary
```

**Step 2: Run real-LLM validation against all golden index URLs**

Run: `uv run pytest tests/test_auto_page_type_golden.py::test_auto_detects_golden_index_pages -v`  
Expected: PASS for all 5 universities.

**Step 3: Run real-LLM validation against all golden detail URLs**

Run: `uv run pytest tests/test_auto_page_type_golden.py::test_auto_detects_golden_detail_pages -v`  
Expected: PASS for all 5 universities.

**Step 4: Run full regression set**

Run: `uv run pytest -q`  
Expected: PASS.

**Step 5: Lint verification**

Run: `uv run pylint $(git ls-files '*.py')`  
Expected: exit code 0.

**Step 6: Commit final verification hooks/docs**

```bash
git add tests scripts docs
git commit -m "test(auto-page-type): validate golden cases with real llm and full regression"
```
