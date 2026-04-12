# Auto-Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `paginated_crawl_skill` that automatically crawls multi-page index pages with a quality circuit breaker, triggered by explicit user opt-in via extension checkbox, CLI chat, or MCP parameter.

**Architecture:** Three new focused modules — pagination detector (heuristic HTML analysis), quality circuit breaker (two-layer scoring), and paginated crawl skill handler (orchestration loop). These compose with the existing `ClientAutomationBridge` and `_auto_fetch_and_extract` infrastructure. The agent system prompt gains one new routing rule; the extension gains one checkbox.

**Tech Stack:** Python 3.12, Pydantic v2, BeautifulSoup4 (HTML parsing for pagination detection), existing LLM router for quality Layer 2, TypeScript/Vite (extension).

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/agent_runtime/skills/impl/pagination_detector.py` | **NEW** — Heuristic pagination detection from raw HTML. Returns `PaginationInfo` |
| `src/agent_runtime/skills/impl/quality_circuit_breaker.py` | **NEW** — Two-layer quality check (heuristic + LLM). Returns `QualityCheckResult` |
| `src/agent_runtime/skills/impl/paginated_crawl.py` | **NEW** — Skill handler orchestrating the page loop, calling detector + breaker + extraction |
| `src/agent_runtime/skills/contracts.py` | **MODIFY** — Add `PaginatedCrawlSkillInput`, `PaginatedCrawlSkillOutput`, `PaginationInfo`, `QualityCheckResult` |
| `src/agent_runtime/skills/impl/__init__.py` | **MODIFY** — Export `paginated_crawl_skill_handler` |
| `src/agent_runtime/skills/registry.py` | **MODIFY** — Register `paginated_crawl_skill` |
| `src/agent_runtime/loop.py` | **MODIFY** — Add `paginated_crawl_skill` to `TOOL_DESCRIPTIONS` and `_ESSENTIAL_SKILL_NAMES`; update system prompt |
| `src/agent_runtime/pydanticai_runtime.py` | **MODIFY** — Append auto-paginate instruction to user message when payload contains `auto_paginate=True` |
| `src/api/schemas.py` | **MODIFY** — Add `auto_paginate` field to `AgentRunRequest` |
| `extension/src/popup.html` | **MODIFY** — Add auto-paginate checkbox |
| `extension/src/popup/dom.ts` | **MODIFY** — Export new checkbox element |
| `extension/src/popup/crawlApi.ts` | **MODIFY** — Add `autoPaginate` to `SubmitAgentRunOpts` and payload |
| `extension/src/popup/crawlFlow.ts` | **MODIFY** — Wire checkbox visibility and pass value to `submitAgentRun` |
| `tests/test_pagination_detector.py` | **NEW** — Unit tests for all 4 detection strategies |
| `tests/test_quality_circuit_breaker.py` | **NEW** — Unit tests for heuristic scoring + LLM fallback |
| `tests/test_paginated_crawl_skill.py` | **NEW** — Integration tests for skill handler |

---

### Task 1: Pydantic Contracts

**Files:**
- Modify: `src/agent_runtime/skills/contracts.py`
- Test: `tests/test_pagination_detector.py` (import validation only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_pagination_detector.py`:

```python
"""Tests for pagination detection contracts and detector."""

import pytest
from pydantic import ValidationError

from src.agent_runtime.skills.contracts import (
    PaginationInfo,
    QualityCheckResult,
    PaginatedCrawlSkillInput,
    PaginatedCrawlSkillOutput,
)


def test_pagination_info_defaults():
    info = PaginationInfo(pagination_type="single_page")
    assert info.pagination_type == "single_page"
    assert info.page_urls == []
    assert info.total_pages is None
    assert info.current_page == 1
    assert info.confidence == 0.0


def test_pagination_info_rejects_invalid_type():
    with pytest.raises(ValidationError):
        PaginationInfo(pagination_type="invalid_type")


def test_quality_check_result_defaults():
    result = QualityCheckResult(
        verdict="pass", heuristic_score=0.8, llm_used=False, reason="ok"
    )
    assert result.verdict == "pass"
    assert result.failed_at_page is None


def test_paginated_crawl_input_defaults():
    inp = PaginatedCrawlSkillInput(
        url="https://example.com", univ_slug="test", year=2026
    )
    assert inp.max_pages == 50
    assert inp.batch_quality_size == 10
    assert inp.client_id is None


def test_paginated_crawl_input_rejects_empty_url():
    with pytest.raises(ValidationError):
        PaginatedCrawlSkillInput(url="", univ_slug="test", year=2026)


def test_paginated_crawl_output_defaults():
    out = PaginatedCrawlSkillOutput(status="done", pagination_type="single_page")
    assert out.pages_processed == 0
    assert out.programs == []
    assert out.warning is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_pagination_detector.py -v`
Expected: FAIL with `ImportError` — contracts don't exist yet.

- [ ] **Step 3: Write the contracts**

Append to `src/agent_runtime/skills/contracts.py`:

```python
class PaginationInfo(BaseModel):
    """Pagination metadata extracted from an index page."""

    pagination_type: Literal["url_param", "single_page", "spa_button"] = "single_page"
    page_urls: list[str] = Field(default_factory=list)
    total_pages: Optional[int] = None
    current_page: int = 1
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class QualityCheckResult(BaseModel):
    """Result of a quality circuit breaker check on a batch of programs."""

    verdict: Literal["pass", "fail"]
    heuristic_score: float = Field(ge=0.0, le=1.0)
    llm_used: bool = False
    reason: str = ""
    failed_at_page: Optional[int] = None
    failed_at_program_count: Optional[int] = None


class PaginatedCrawlSkillInput(BaseModel):
    """Input payload for paginated crawl skill."""

    url: str = Field(min_length=1)
    univ_slug: str = Field(min_length=1)
    year: int = Field(gt=0)
    max_pages: int = Field(default=50, ge=1, le=200)
    batch_quality_size: int = Field(default=10, ge=5, le=50)
    client_id: Optional[str] = None


class PaginatedCrawlSkillOutput(BaseModel):
    """Output payload for paginated crawl skill."""

    status: Literal["done", "quality_failed", "pagination_not_supported"] = "done"
    pagination_type: str = "single_page"
    total_pages_detected: Optional[int] = None
    pages_processed: int = 0
    programs: list[dict[str, Any]] = Field(default_factory=list)
    total_programs: int = 0
    quality_scores: list[dict[str, Any]] = Field(default_factory=list)
    warning: Optional[str] = None
    summary: str = ""
```

Note: `Literal` import already exists in the file. Add it to the existing import if missing:
```python
from typing import Any, Literal, Optional
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_pagination_detector.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/skills/contracts.py tests/test_pagination_detector.py
git commit -m "feat(pagination): add Pydantic contracts for pagination detector, quality breaker, and paginated crawl skill"
```

---

### Task 2: Pagination Detector

**Files:**
- Create: `src/agent_runtime/skills/impl/pagination_detector.py`
- Test: `tests/test_pagination_detector.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pagination_detector.py`:

```python
from src.agent_runtime.skills.impl.pagination_detector import detect_pagination


class TestStrategy1PaginationContainer:
    """Strategy 1: <nav> / <ul> pagination container."""

    def test_edinburgh_pagination(self):
        """Edinburgh: <nav aria-label="Pagination"> with ?page=0..71."""
        html = """
        <nav aria-label="Pagination">
          <ul class="pagination">
            <li class="page-item active">
              <a href="?page=0" class="page-link">1</a>
            </li>
            <li class="page-item">
              <a href="?page=1" class="page-link">2</a>
            </li>
            <li class="page-item">
              <a href="?page=2" class="page-link">3</a>
            </li>
            <li class="page-item">
              <a href="?page=71" aria-label="Last page" class="page-link">72</a>
            </li>
          </ul>
        </nav>
        """
        result = detect_pagination(html, "https://study.ed.ac.uk/programmes/undergraduate?page=0")
        assert result.pagination_type == "url_param"
        assert result.total_pages == 72
        assert len(result.page_urls) == 72
        assert result.page_urls[0] == "https://study.ed.ac.uk/programmes/undergraduate?page=0"
        assert result.page_urls[71] == "https://study.ed.ac.uk/programmes/undergraduate?page=71"
        assert result.confidence >= 0.8

    def test_leeds_pagination(self):
        """Leeds: <nav class="uol-pagination"> with ?page=1..19 and extra params."""
        html = """
        <nav class="uol-pagination" aria-label="pagination">
          <ol class="uol-pagination__list">
            <li class="uol-pagination__item uol-pagination__item--current">
              <a href="?page=1&start_rank=1&type=PGT&term=202627"
                 class="uol-pagination__link">1</a>
            </li>
            <li class="uol-pagination__item">
              <a href="?page=2&start_rank=16&type=PGT&term=202627"
                 class="uol-pagination__link">2</a>
            </li>
            <li class="uol-pagination__item">
              <a href="?page=3&start_rank=31&type=PGT&term=202627"
                 class="uol-pagination__link">3</a>
            </li>
            <li class="uol-pagination__item">
              <a href="?page=19&start_rank=271&type=PGT&term=202627"
                 class="uol-pagination__link">Last</a>
            </li>
          </ol>
        </nav>
        """
        result = detect_pagination(html, "https://courses.leeds.ac.uk/course-search/masters-courses")
        assert result.pagination_type == "url_param"
        assert result.total_pages == 19
        assert len(result.page_urls) == 19
        assert "page=1" in result.page_urls[0]
        assert "page=19" in result.page_urls[18]
        # Static params preserved
        assert "type=PGT" in result.page_urls[5]
        assert result.confidence >= 0.8


class TestStrategy3SpaButton:
    """Strategy 3: SPA button detection."""

    def test_nus_spa_buttons(self):
        """NUS: <button data-page="N"> pagination."""
        html = """
        <div class="pagination-container">
          <button class="arrow-button" disabled>Prev</button>
          <button data-page="1" class="active-page">1</button>
          <button data-page="2" class="page-button">2</button>
          <span class="ellipsis">...</span>
          <button data-page="25" class="page-button">25</button>
          <button class="arrow-button">Next</button>
        </div>
        """
        result = detect_pagination(html, "https://study.nus.edu.sg/programme")
        assert result.pagination_type == "spa_button"
        assert result.page_urls == []
        assert result.confidence >= 0.5


class TestStrategy4NoPagination:
    """Strategy 4: No pagination fallback."""

    def test_ucl_no_pagination(self):
        """UCL: large page with no pagination elements."""
        html = """
        <div class="degree-list">
          <a href="/degrees/anthropology-bsc">Anthropology BSc</a>
          <a href="/degrees/chemistry-bsc">Chemistry BSc</a>
        </div>
        """
        result = detect_pagination(html, "https://www.ucl.ac.uk/prospective-students/undergraduate/degrees")
        assert result.pagination_type == "single_page"
        assert result.total_pages == 1
        assert result.page_urls == []

    def test_polyu_swiper_not_pagination(self):
        """PolyU: swiper-pagination is NOT course pagination."""
        html = """
        <div class="swiper-pagination"></div>
        <div class="course-list">
          <a href="/study/pg/tpg/2026/xxx">Course A</a>
        </div>
        """
        result = detect_pagination(html, "https://www.polyu.edu.hk/study/pg/taught-postgraduate")
        assert result.pagination_type == "single_page"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_pagination_detector.py::TestStrategy1PaginationContainer -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the pagination detector**

Create `src/agent_runtime/skills/impl/pagination_detector.py`:

```python
"""Heuristic pagination detection from index page HTML.

Detects URL-parameter pagination (e.g. ?page=N), SPA button pagination,
or falls back to single-page mode. No LLM calls.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from src.agent_runtime.skills.contracts import PaginationInfo

logger = logging.getLogger(__name__)

_PAGE_PARAM_NAMES = {"page", "p", "offset", "start_rank", "pg"}

# Matches <nav> or <ul>/<ol> elements with pagination-related attributes
_PAGINATION_CONTAINER_RE = re.compile(
    r"<(?:nav|ul|ol)\b[^>]*?"
    r"(?:aria-label\s*=\s*[\"'](?:[^\"']*pagination[^\"']*)[\"']"
    r"|class\s*=\s*[\"'](?:[^\"']*pagination[^\"']*)[\"'])"
    r"[^>]*>(.*?)</(?:nav|ul|ol)>",
    re.IGNORECASE | re.DOTALL,
)

# Extracts href from <a> tags inside a pagination container
_PAGINATION_LINK_RE = re.compile(
    r"<a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)

# SPA button pagination: <button data-page="N">
_SPA_BUTTON_RE = re.compile(
    r"<button\b[^>]*data-page\s*=\s*[\"'](\d+)[\"'][^>]*>",
    re.IGNORECASE,
)

# Loose page-link scan across the entire HTML
_LOOSE_PAGE_LINK_RE = re.compile(
    r'href\s*=\s*["\']([^"\']*[?&](?:page|p|offset|start_rank|pg)=(\d+)[^"\']*)["\']',
    re.IGNORECASE,
)


def detect_pagination(html: str, base_url: str) -> PaginationInfo:
    """Detect pagination pattern from index page HTML.

    Tries strategies in priority order:
    1. Pagination container (<nav>/<ul> with pagination class/aria-label)
    2. Loose page-link scan across full HTML
    3. SPA button detection (<button data-page="N">)
    4. No pagination fallback
    """
    if not html:
        return PaginationInfo(pagination_type="single_page", total_pages=1)

    # Strategy 1: Pagination container
    result = _strategy_pagination_container(html, base_url)
    if result:
        return result

    # Strategy 2: Loose page-link scan
    result = _strategy_loose_page_links(html, base_url)
    if result:
        return result

    # Strategy 3: SPA button detection
    result = _strategy_spa_buttons(html)
    if result:
        return result

    # Strategy 4: No pagination
    logger.info("[PaginationDetector] No pagination detected")
    return PaginationInfo(pagination_type="single_page", total_pages=1)


def _strategy_pagination_container(html: str, base_url: str) -> PaginationInfo | None:
    """Strategy 1: Find a pagination <nav>/<ul>/<ol> container and extract page links."""
    # Filter out non-course pagination (e.g. in-page section navigation)
    for match in _PAGINATION_CONTAINER_RE.finditer(html):
        container_html = match.group(1)
        hrefs = _PAGINATION_LINK_RE.findall(container_html)
        if not hrefs:
            continue

        # Find which hrefs contain page-like query parameters
        page_param, page_values = _extract_page_param(hrefs)
        if page_param is None or len(page_values) < 2:
            continue

        # Build complete URL list
        page_urls = _build_page_url_list(hrefs, base_url, page_param, page_values)
        if not page_urls:
            continue

        total = len(page_urls)
        logger.info(
            "[PaginationDetector] Strategy 1: found %d pages via container (param=%s)",
            total, page_param,
        )
        return PaginationInfo(
            pagination_type="url_param",
            page_urls=page_urls,
            total_pages=total,
            current_page=1,
            confidence=0.9,
        )

    return None


def _strategy_loose_page_links(html: str, base_url: str) -> PaginationInfo | None:
    """Strategy 2: Scan all hrefs for page-like URL parameters."""
    matches = _LOOSE_PAGE_LINK_RE.findall(html)
    if not matches:
        return None

    # Group by parameter name
    param_groups: dict[str, dict[int, str]] = {}
    for href, value_str in matches:
        parsed = urlparse(urljoin(base_url, href))
        qs = parse_qs(parsed.query)
        for param_name in _PAGE_PARAM_NAMES:
            if param_name in qs:
                val = int(qs[param_name][0])
                param_groups.setdefault(param_name, {})[val] = href
                break

    # Need >= 3 distinct values for the same param to qualify
    for param_name, value_map in param_groups.items():
        if len(value_map) < 3:
            continue

        sorted_values = sorted(value_map.keys())
        min_val, max_val = sorted_values[0], sorted_values[-1]
        total = max_val - min_val + 1

        # Build URLs from a representative href
        sample_href = value_map[sorted_values[0]]
        page_urls = _generate_page_urls_from_template(
            sample_href, base_url, param_name, min_val, max_val,
        )

        logger.info(
            "[PaginationDetector] Strategy 2: found %d pages via loose scan (param=%s)",
            total, param_name,
        )
        return PaginationInfo(
            pagination_type="url_param",
            page_urls=page_urls,
            total_pages=total,
            current_page=1,
            confidence=0.6,
        )

    return None


def _strategy_spa_buttons(html: str) -> PaginationInfo | None:
    """Strategy 3: Detect SPA button-based pagination (Phase 2 marker)."""
    button_values = [int(m) for m in _SPA_BUTTON_RE.findall(html)]
    if len(button_values) >= 2:
        total = max(button_values)
        logger.info(
            "[PaginationDetector] Strategy 3: SPA button pagination detected (%d pages)",
            total,
        )
        return PaginationInfo(
            pagination_type="spa_button",
            page_urls=[],
            total_pages=total,
            current_page=1,
            confidence=0.7,
        )
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_page_param(
    hrefs: list[str],
) -> tuple[str | None, dict[int, str]]:
    """Identify which query parameter is the page counter across hrefs."""
    param_counts: dict[str, dict[int, str]] = {}

    for href in hrefs:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        for param_name in _PAGE_PARAM_NAMES:
            if param_name in qs:
                try:
                    val = int(qs[param_name][0])
                    param_counts.setdefault(param_name, {})[val] = href
                except (ValueError, IndexError):
                    pass

    # Pick the param with the most distinct values
    best_param = None
    best_values: dict[int, str] = {}
    for param_name, value_map in param_counts.items():
        if len(value_map) > len(best_values):
            best_param = param_name
            best_values = value_map

    return best_param, best_values


def _build_page_url_list(
    hrefs: list[str],
    base_url: str,
    page_param: str,
    page_values: dict[int, str],
) -> list[str]:
    """Build a complete list of page URLs, filling in gaps between min and max."""
    sorted_values = sorted(page_values.keys())
    min_val = sorted_values[0]
    max_val = sorted_values[-1]

    # Use a known href as the template for generating missing pages
    template_href = page_values[sorted_values[0]]
    return _generate_page_urls_from_template(
        template_href, base_url, page_param, min_val, max_val,
    )


def _generate_page_urls_from_template(
    template_href: str,
    base_url: str,
    page_param: str,
    min_val: int,
    max_val: int,
) -> list[str]:
    """Generate page URLs by varying one parameter in a template URL."""
    absolute = urljoin(base_url, template_href)
    parsed = urlparse(absolute)
    qs = parse_qs(parsed.query, keep_blank_values=True)

    urls: list[str] = []
    for page_num in range(min_val, max_val + 1):
        new_qs = dict(qs)
        new_qs[page_param] = [str(page_num)]
        new_query = urlencode(new_qs, doseq=True)
        new_url = urlunparse(parsed._replace(query=new_query))
        urls.append(new_url)

    return urls
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_pagination_detector.py -v`
Expected: All 11 tests PASS (6 contract + 5 detector).

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/skills/impl/pagination_detector.py tests/test_pagination_detector.py
git commit -m "feat(pagination): implement heuristic pagination detector with 4 strategies"
```

---

### Task 3: Quality Circuit Breaker

**Files:**
- Create: `src/agent_runtime/skills/impl/quality_circuit_breaker.py`
- Test: `tests/test_quality_circuit_breaker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quality_circuit_breaker.py`:

```python
"""Tests for the quality circuit breaker."""

import pytest

from src.agent_runtime.skills.impl.quality_circuit_breaker import (
    heuristic_quality_score,
    quality_check,
)


def _make_program(name: str, faculty: str = "", tuition: str = "") -> dict:
    return {
        "name_en": name,
        "faculty": faculty,
        "tuition_amount": tuition,
        "study_options": [],
    }


class TestHeuristicScore:
    def test_all_good_programs(self):
        programs = [
            _make_program("MSc Data Science", faculty="Computing", tuition="45000"),
            _make_program("MSc Business Analytics", faculty="Business"),
            _make_program("MA English Literature", tuition="30000"),
            _make_program("BSc Computer Science", faculty="Engineering", tuition="42000"),
            _make_program("MBA", faculty="Business", tuition="60000"),
        ]
        score = heuristic_quality_score(programs)
        assert score >= 0.7

    def test_all_noise_programs(self):
        programs = [
            _make_program("Skip to main content"),
            _make_program("Home"),
            _make_program("Search"),
            _make_program("Menu"),
            _make_program("Contact Us"),
        ]
        score = heuristic_quality_score(programs)
        assert score < 0.4

    def test_all_empty_names(self):
        programs = [_make_program("") for _ in range(5)]
        score = heuristic_quality_score(programs)
        assert score < 0.4

    def test_many_duplicates(self):
        programs = [_make_program("MSc Data Science", faculty="CS")] * 8 + [
            _make_program("MA History", faculty="Arts"),
            _make_program("BSc Physics", faculty="Science"),
        ]
        score = heuristic_quality_score(programs)
        assert score < 0.7  # Duplicate penalty

    def test_mixed_quality(self):
        programs = [
            _make_program("MSc Data Science", faculty="Computing"),
            _make_program(""),
            _make_program("MA History", faculty="Arts"),
            _make_program("Skip to content"),
            _make_program("BSc Physics"),
        ]
        score = heuristic_quality_score(programs)
        assert 0.4 <= score <= 0.7  # Uncertain zone


class TestQualityCheck:
    def test_good_batch_passes_without_llm(self):
        programs = [
            _make_program(f"MSc Program {i}", faculty="Faculty", tuition="40000")
            for i in range(10)
        ]
        result = quality_check(programs)
        assert result.verdict == "pass"
        assert result.llm_used is False
        assert result.heuristic_score >= 0.7

    def test_bad_batch_fails_without_llm(self):
        programs = [_make_program("") for _ in range(10)]
        result = quality_check(programs)
        assert result.verdict == "fail"
        assert result.llm_used is False
        assert result.heuristic_score < 0.4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_quality_circuit_breaker.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the quality circuit breaker**

Create `src/agent_runtime/skills/impl/quality_circuit_breaker.py`:

```python
"""Two-layer quality circuit breaker for paginated crawl.

Layer 1: Fast heuristic scoring (no LLM) — resolves 80%+ of batches.
Layer 2: LLM review — only when heuristic score is in the uncertain zone.
"""

from __future__ import annotations

import logging
from collections import Counter

from src.agent_runtime.skills.contracts import QualityCheckResult
from src.scrapers.helpers import is_noise_program_name

logger = logging.getLogger(__name__)

HEURISTIC_PASS_THRESHOLD = 0.7
HEURISTIC_FAIL_THRESHOLD = 0.4


def heuristic_quality_score(programs: list[dict]) -> float:
    """Compute a 0.0-1.0 quality score from structural field checks.

    Checks:
    - name_en non-empty and non-noise (high weight)
    - name_en dedup ratio (high weight)
    - key field fill rate (medium weight)
    - name_en length (low weight)
    """
    if not programs:
        return 0.0

    count = len(programs)
    valid_name_count = 0
    has_key_field_count = 0
    good_length_count = 0
    names: list[str] = []

    for prog in programs:
        name = str(prog.get("name_en") or "").strip()
        faculty = str(prog.get("faculty") or "").strip()
        tuition = str(prog.get("tuition_amount") or "").strip()
        study_opts = prog.get("study_options") or []

        # Check 1: name is non-empty and non-noise
        if name and not is_noise_program_name(name):
            valid_name_count += 1
        names.append(name)

        # Check 2: at least one key field has value
        if faculty or tuition or (isinstance(study_opts, list) and study_opts):
            has_key_field_count += 1

        # Check 3: name length in reasonable range
        if 5 <= len(name) <= 200:
            good_length_count += 1

    # Check 4: dedup ratio — penalize if too many identical names
    name_counts = Counter(n for n in names if n)
    max_dup = max(name_counts.values()) if name_counts else 0
    dup_ratio = max_dup / count if count > 0 else 0.0
    dup_penalty = max(0.0, (dup_ratio - 0.5) * 0.6)  # Penalty kicks in above 50% dups

    # Weighted score
    name_score = valid_name_count / count              # weight: 0.4
    field_score = has_key_field_count / count           # weight: 0.3
    length_score = good_length_count / count            # weight: 0.1
    dup_score = max(0.0, 1.0 - dup_penalty)            # weight: 0.2

    raw = (name_score * 0.4) + (field_score * 0.3) + (length_score * 0.1) + (dup_score * 0.2)
    return round(min(1.0, max(0.0, raw)), 3)


def quality_check(
    programs: list[dict],
    *,
    page_index: int | None = None,
    total_program_count: int | None = None,
) -> QualityCheckResult:
    """Run the two-layer quality check on a batch of programs.

    Layer 1 (heuristic) handles clear pass/fail.
    Layer 2 (LLM) handles the uncertain zone — only called when needed.
    """
    score = heuristic_quality_score(programs)

    if score >= HEURISTIC_PASS_THRESHOLD:
        logger.info(
            "[QualityBreaker] PASS (heuristic=%.3f, page=%s)", score, page_index
        )
        return QualityCheckResult(
            verdict="pass",
            heuristic_score=score,
            llm_used=False,
            reason=f"Heuristic score {score:.3f} >= {HEURISTIC_PASS_THRESHOLD}",
        )

    if score < HEURISTIC_FAIL_THRESHOLD:
        logger.warning(
            "[QualityBreaker] FAIL (heuristic=%.3f, page=%s)", score, page_index
        )
        return QualityCheckResult(
            verdict="fail",
            heuristic_score=score,
            llm_used=False,
            reason=f"Heuristic score {score:.3f} < {HEURISTIC_FAIL_THRESHOLD}",
            failed_at_page=page_index,
            failed_at_program_count=total_program_count,
        )

    # Uncertain zone — call LLM for review
    return _llm_quality_review(
        programs, score,
        page_index=page_index,
        total_program_count=total_program_count,
    )


def _llm_quality_review(
    programs: list[dict],
    heuristic_score: float,
    *,
    page_index: int | None = None,
    total_program_count: int | None = None,
) -> QualityCheckResult:
    """Layer 2: LLM quality review for uncertain batches."""
    summary_lines = []
    for i, prog in enumerate(programs, 1):
        name = str(prog.get("name_en") or "(empty)").strip()
        faculty = str(prog.get("faculty") or "null").strip()
        tuition = str(prog.get("tuition_amount") or "null").strip()
        summary_lines.append(f"{i}. \"{name}\" — faculty: {faculty}, tuition: {tuition}")

    prompt = (
        "Here are extracted program entries from a university index page. "
        "Rate extraction quality: PASS or FAIL.\n"
        "FAIL if: >=3 names are clearly not program names (navigation text, "
        "page titles, etc.), or >=5 entries have all key fields empty.\n"
        "Reply with JSON: {\"verdict\": \"PASS\"|\"FAIL\", \"reason\": \"...\"}\n\n"
        + "\n".join(summary_lines)
    )

    try:
        from src.agents.factory import create_router
        router = create_router()
        response = router.generate_text(prompt)
        text = str(response or "").strip()

        import json
        # Try to parse JSON from response
        for candidate in [text, text.split("```")[-1] if "```" in text else text]:
            candidate = candidate.strip().strip("`").strip()
            if candidate.startswith("{"):
                try:
                    data = json.loads(candidate)
                    verdict = str(data.get("verdict", "")).strip().lower()
                    reason = str(data.get("reason", "")).strip()
                    if verdict in ("pass", "fail"):
                        logger.info(
                            "[QualityBreaker] LLM verdict=%s (heuristic=%.3f, page=%s)",
                            verdict, heuristic_score, page_index,
                        )
                        return QualityCheckResult(
                            verdict=verdict,
                            heuristic_score=heuristic_score,
                            llm_used=True,
                            reason=f"LLM: {reason}",
                            failed_at_page=page_index if verdict == "fail" else None,
                            failed_at_program_count=total_program_count if verdict == "fail" else None,
                        )
                except json.JSONDecodeError:
                    pass

        # LLM response unparseable — fall back to conservative pass
        logger.warning("[QualityBreaker] LLM response unparseable, defaulting to pass")
        return QualityCheckResult(
            verdict="pass",
            heuristic_score=heuristic_score,
            llm_used=True,
            reason="LLM response unparseable; defaulting to pass",
        )

    except Exception as exc:
        logger.warning("[QualityBreaker] LLM review failed: %s; defaulting to pass", exc)
        return QualityCheckResult(
            verdict="pass",
            heuristic_score=heuristic_score,
            llm_used=False,
            reason=f"LLM unavailable ({exc}); defaulting to pass",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_quality_circuit_breaker.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/skills/impl/quality_circuit_breaker.py tests/test_quality_circuit_breaker.py
git commit -m "feat(pagination): implement two-layer quality circuit breaker"
```

---

### Task 4: Paginated Crawl Skill Handler

**Files:**
- Create: `src/agent_runtime/skills/impl/paginated_crawl.py`
- Modify: `src/agent_runtime/skills/impl/__init__.py`
- Test: `tests/test_paginated_crawl_skill.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_paginated_crawl_skill.py`:

```python
"""Tests for paginated crawl skill handler."""

from unittest.mock import MagicMock, patch

import pytest

from src.agent_runtime.skills.contracts import PaginatedCrawlSkillInput


class TestPaginatedCrawlSkillHandler:
    def test_single_page_no_pagination(self):
        """When no pagination detected, processes page 1 only."""
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        inp = PaginatedCrawlSkillInput(
            url="https://example.com/courses",
            univ_slug="example",
            year=2026,
        )

        fake_html = "<html><body><a href='/course/1'>Course 1</a></body></html>"
        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content=fake_html
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page"
        ) as mock_process:
            from src.agent_runtime.skills.contracts import PaginationInfo
            mock_detect.return_value = PaginationInfo(
                pagination_type="single_page", total_pages=1
            )
            mock_process.return_value = [
                {"name_en": "MSc Data Science", "faculty": "Computing"}
            ]

            result = paginated_crawl_skill_handler(inp, mock_bridge)

        assert result["status"] == "done"
        assert result["pagination_type"] == "single_page"
        assert result["pages_processed"] == 1
        assert len(result["programs"]) == 1

    def test_spa_button_returns_not_supported(self):
        """When SPA pagination detected, returns pagination_not_supported."""
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        inp = PaginatedCrawlSkillInput(
            url="https://study.nus.edu.sg/programme",
            univ_slug="nus",
            year=2026,
        )

        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content="<button data-page='1'>1</button><button data-page='2'>2</button><button data-page='25'>25</button>"
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page"
        ) as mock_process:
            from src.agent_runtime.skills.contracts import PaginationInfo
            mock_detect.return_value = PaginationInfo(
                pagination_type="spa_button", total_pages=25, confidence=0.7
            )
            mock_process.return_value = [
                {"name_en": "Doctor of Engineering", "faculty": "CDE"}
            ]

            result = paginated_crawl_skill_handler(inp, mock_bridge)

        assert result["status"] == "pagination_not_supported"
        assert "SPA" in (result.get("warning") or "")
        # Should still process page 1
        assert result["pages_processed"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_paginated_crawl_skill.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the skill handler**

Create `src/agent_runtime/skills/impl/paginated_crawl.py`:

```python
"""Paginated crawl skill — multi-page index crawling with quality gates."""

from __future__ import annotations

import logging
from typing import Any, Callable

from src.agent_bridge.client_automation_bridge import ClientAutomationBridge
from src.agent_bridge.contracts import BrowserFetchInput
from src.agent_runtime.skills.contracts import (
    PaginatedCrawlSkillInput,
    PaginationInfo,
)
from src.agent_runtime.skills.impl.pagination_detector import detect_pagination
from src.agent_runtime.skills.impl.quality_circuit_breaker import quality_check

logger = logging.getLogger(__name__)


def paginated_crawl_skill_handler(
    payload: PaginatedCrawlSkillInput,
    bridge: ClientAutomationBridge,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Orchestrate multi-page index crawling with quality circuit breaker."""

    def _emit(event_type: str, **kwargs: Any) -> None:
        if event_sink is not None:
            event_sink({"type": event_type, **kwargs})

    # Step 1: Fetch page 1 HTML
    page1_output = bridge.fetch_browser_payload(
        BrowserFetchInput(
            url=payload.url,
            page_type_hint="index",
            client_id=payload.client_id,
        )
    )
    page1_html = page1_output.html_content or ""

    if not page1_html:
        return _build_output(
            status="done",
            pagination_type="single_page",
            pages_processed=0,
            warning="Failed to fetch page HTML",
            summary="No HTML content received from browser",
        )

    # Step 2: Detect pagination
    pagination = detect_pagination(page1_html, payload.url)
    _emit(
        "pagination_detected",
        pagination_type=pagination.pagination_type,
        total_pages=pagination.total_pages,
    )

    # Step 3: Handle SPA button (Phase 2 — not yet supported)
    if pagination.pagination_type == "spa_button":
        # Still process page 1
        page1_programs = _process_single_index_page(
            page1_html, payload.url, bridge, payload.univ_slug, payload.year,
        )
        return _build_output(
            status="pagination_not_supported",
            pagination_type="spa_button",
            total_pages_detected=pagination.total_pages,
            pages_processed=1,
            programs=page1_programs,
            warning=(
                f"Detected SPA button pagination ({pagination.total_pages} pages) "
                f"which is not yet supported for auto-pagination. "
                f"Only page 1 ({len(page1_programs)} programs) was processed."
            ),
            summary=f"SPA pagination detected. Processed page 1 only: {len(page1_programs)} programs.",
        )

    # Step 4: Build page URL list
    if pagination.pagination_type == "url_param":
        page_urls = pagination.page_urls[:payload.max_pages]
    else:
        # single_page — just process the one page
        page_urls = [payload.url]

    # Step 5: Page loop with quality gates
    all_programs: list[dict[str, Any]] = []
    quality_log: list[dict[str, Any]] = []
    pages_processed = 0
    next_check_at = payload.batch_quality_size

    for page_idx, page_url in enumerate(page_urls):
        # Fetch HTML (reuse page 1 for first iteration)
        if page_idx == 0:
            html = page1_html
        else:
            try:
                output = bridge.fetch_browser_payload(
                    BrowserFetchInput(
                        url=page_url,
                        page_type_hint="index",
                        client_id=payload.client_id,
                    )
                )
                html = output.html_content or ""
            except Exception as exc:
                logger.warning(
                    "[PaginatedCrawl] Failed to fetch page %d (%s): %s",
                    page_idx + 1, page_url, exc,
                )
                continue

        if not html:
            continue

        # Extract programs from this page
        page_programs = _process_single_index_page(
            html, page_url, bridge, payload.univ_slug, payload.year,
        )
        all_programs.extend(page_programs)
        pages_processed += 1

        _emit(
            "pagination_progress",
            page=page_idx + 1,
            total_pages=len(page_urls),
            programs_so_far=len(all_programs),
        )

        # Quality check
        if len(all_programs) >= next_check_at:
            batch_start = max(0, len(all_programs) - payload.batch_quality_size)
            recent_batch = all_programs[batch_start:]
            qc = quality_check(
                recent_batch,
                page_index=page_idx + 1,
                total_program_count=len(all_programs),
            )
            quality_log.append(qc.model_dump(mode="json"))

            if qc.verdict == "fail":
                _emit(
                    "quality_check_failed",
                    batch_index=len(quality_log),
                    reason=qc.reason,
                )
                return _build_output(
                    status="quality_failed",
                    pagination_type=pagination.pagination_type,
                    total_pages_detected=pagination.total_pages,
                    pages_processed=pages_processed,
                    programs=all_programs,
                    quality_scores=quality_log,
                    warning=(
                        f"Quality check failed at page {page_idx + 1}, "
                        f"program #{len(all_programs)}: {qc.reason}"
                    ),
                    summary=(
                        f"Stopped at page {page_idx + 1}/{len(page_urls)}. "
                        f"{len(all_programs)} programs extracted before quality failure."
                    ),
                )

            _emit(
                "quality_check_passed",
                batch_index=len(quality_log),
                heuristic_score=qc.heuristic_score,
            )
            next_check_at = len(all_programs) + payload.batch_quality_size

    # Final quality check on any remaining unchecked programs
    unchecked_start = next_check_at - payload.batch_quality_size
    if unchecked_start < len(all_programs):
        remaining = all_programs[unchecked_start:]
        if remaining:
            qc = quality_check(
                remaining,
                page_index=pages_processed,
                total_program_count=len(all_programs),
            )
            quality_log.append(qc.model_dump(mode="json"))
            if qc.verdict == "fail":
                return _build_output(
                    status="quality_failed",
                    pagination_type=pagination.pagination_type,
                    total_pages_detected=pagination.total_pages,
                    pages_processed=pages_processed,
                    programs=all_programs,
                    quality_scores=quality_log,
                    warning=f"Final quality check failed: {qc.reason}",
                    summary=(
                        f"Processed {pages_processed}/{len(page_urls)} pages. "
                        f"{len(all_programs)} programs extracted. Final check failed."
                    ),
                )

    return _build_output(
        status="done",
        pagination_type=pagination.pagination_type,
        total_pages_detected=pagination.total_pages,
        pages_processed=pages_processed,
        programs=all_programs,
        quality_scores=quality_log,
        summary=(
            f"Completed {pages_processed}/{len(page_urls)} pages. "
            f"{len(all_programs)} programs extracted."
        ),
    )


def _process_single_index_page(
    html: str,
    page_url: str,
    bridge: ClientAutomationBridge,
    univ_slug: str,
    year: int,
) -> list[dict[str, Any]]:
    """Extract programs from one index page using existing infrastructure."""
    from src.agent_runtime.skills.impl.common import (
        _html_to_markdown,
        _auto_fetch_and_extract,
        _get_cached_llm_filter,
        _set_cached_llm_filter,
        get_task_context,
    )
    from src.services.crawler import analyze_page

    # LLM link filter (with cache)
    cached = _get_cached_llm_filter(page_url, "index")
    if cached is not None:
        filtered_urls, link_texts = cached
    else:
        try:
            analysis = analyze_page(page_url, html, "index")
            llm_links = analysis.get("links") or []
            filtered_urls = [link["url"] for link in llm_links]
            link_texts = {
                link["url"]: link["text"]
                for link in llm_links
                if link.get("text")
            }
            _set_cached_llm_filter(page_url, "index", filtered_urls, link_texts)
        except Exception as exc:
            logger.warning("[PaginatedCrawl] Link filter failed for %s: %s", page_url, exc)
            return []

    if not filtered_urls:
        return []

    # Fetch detail pages and extract programs
    extracted = _auto_fetch_and_extract(
        filtered_urls, link_texts, bridge,
        index_url=page_url,
        univ_slug=univ_slug,
        year=year,
    )
    return extracted.get("programs") or []


def _build_output(**kwargs: Any) -> dict[str, Any]:
    """Build a PaginatedCrawlSkillOutput-compatible dict."""
    programs = kwargs.get("programs") or []
    kwargs["total_programs"] = len(programs)
    kwargs.setdefault("quality_scores", [])
    kwargs.setdefault("summary", "")
    kwargs.setdefault("warning", None)
    kwargs.setdefault("pages_processed", 0)
    kwargs.setdefault("total_pages_detected", None)
    kwargs.setdefault("pagination_type", "single_page")
    kwargs.setdefault("status", "done")
    kwargs.setdefault("programs", [])
    return kwargs
```

- [ ] **Step 4: Update `__init__.py` exports**

Add to `src/agent_runtime/skills/impl/__init__.py`:

```python
from src.agent_runtime.skills.impl.paginated_crawl import (
    paginated_crawl_skill_handler,
)
```

And add `"paginated_crawl_skill_handler"` to the `__all__` list.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_paginated_crawl_skill.py -v`
Expected: All 2 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_runtime/skills/impl/paginated_crawl.py src/agent_runtime/skills/impl/__init__.py tests/test_paginated_crawl_skill.py
git commit -m "feat(pagination): implement paginated crawl skill handler with page loop and quality gates"
```

---

### Task 5: Skill Registration + Agent Loop Integration

**Files:**
- Modify: `src/agent_runtime/skills/registry.py`
- Modify: `src/agent_runtime/loop.py`
- Modify: `src/agent_runtime/pydanticai_runtime.py`
- Test: `tests/test_agent_skill_registry.py` (extend), `tests/test_tool_trimming.py` (update)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_skill_registry.py`:

```python
def test_paginated_crawl_skill_registered():
    registry = build_skill_registry()
    assert "paginated_crawl_skill" in registry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_agent_skill_registry.py::test_paginated_crawl_skill_registered -v`
Expected: FAIL — skill not registered yet.

- [ ] **Step 3: Register the skill in registry.py**

In `src/agent_runtime/skills/registry.py`, add the import:

```python
from src.agent_runtime.skills.contracts import (
    # ...existing imports...
    PaginatedCrawlSkillInput,
    PaginatedCrawlSkillOutput,
)
from src.agent_runtime.skills.impl import (
    # ...existing imports...
    paginated_crawl_skill_handler,
)
```

Add to the `skills` list inside `build_skill_registry()`:

```python
        SkillDef(
            name="paginated_crawl_skill",
            input_model=PaginatedCrawlSkillInput,
            output_model=PaginatedCrawlSkillOutput,
            handler=lambda payload: paginated_crawl_skill_handler(payload, client_bridge),
        ),
```

- [ ] **Step 4: Add tool description and essential skill in loop.py**

In `src/agent_runtime/loop.py`, add to `TOOL_DESCRIPTIONS`:

```python
    "paginated_crawl_skill": (
        "Crawl a multi-page or large index page with automatic pagination and "
        "quality checks. Use ONLY when the user explicitly requests auto-pagination "
        "(e.g. '翻页', 'paginate', 'all pages'). Handles URL-parameter pagination "
        "and large single-page indexes. Returns extracted programs from all pages."
    ),
```

Add `"paginated_crawl_skill"` to the `_ESSENTIAL_SKILL_NAMES` set inside `build_openai_tools()`:

```python
    _ESSENTIAL_SKILL_NAMES = {
        "browser_automation_skill",
        "persist_programs_skill",
        "analyze_page_skill",
        "paginated_crawl_skill",
    }
```

Update `_build_system_prompt()` — append before the closing `"""`:

```python
## For index pages with auto-pagination requested:
If the user message mentions auto-pagination, paginate, 翻页, all pages,
or collect all courses:
1. Call paginated_crawl_skill(url=<given URL>, univ_slug, year).
2. The skill handles pagination detection, multi-page fetching,
   quality checks, and extraction internally.
3. If status is "done": call persist_programs_skill ONCE with the
   returned programs array AS-IS.
4. If status is "quality_failed": report the warning to the user.
   Do NOT call persist_programs_skill — let the user decide.
5. If status is "pagination_not_supported": inform the user that
   SPA pagination was detected and auto-pagination is not yet supported.
```

- [ ] **Step 5: Update pydanticai_runtime.py**

In `src/agent_runtime/pydanticai_runtime.py`, modify `_build_user_message` — inside the `if task == "crawl":` block, after the existing `parts` list, add:

```python
            auto_paginate = payload.get("auto_paginate", False)
            if auto_paginate:
                parts.append(
                    "AUTO-PAGINATE REQUESTED: Use paginated_crawl_skill for this index page."
                )
```

- [ ] **Step 6: Update tool trimming test**

In `tests/test_tool_trimming.py`, update the `_ESSENTIAL_TOOLS` set:

```python
_ESSENTIAL_TOOLS = {
    "browser_automation_skill",
    "persist_programs_skill",
    "analyze_page_skill",
    "paginated_crawl_skill",
}
```

- [ ] **Step 7: Run all affected tests**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_agent_skill_registry.py tests/test_tool_trimming.py -v`
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agent_runtime/skills/registry.py src/agent_runtime/loop.py src/agent_runtime/pydanticai_runtime.py tests/test_agent_skill_registry.py tests/test_tool_trimming.py
git commit -m "feat(pagination): register paginated_crawl_skill and update agent system prompt"
```

---

### Task 6: API Schema Update

**Files:**
- Modify: `src/api/schemas.py`
- Test: `tests/test_agent_api_entrypoints.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_api_entrypoints.py` (or create a new focused test file if preferred):

```python
def test_agent_run_request_accepts_auto_paginate():
    from src.api.schemas import AgentRunRequest

    req = AgentRunRequest(
        url="https://example.com",
        univ_slug="test",
        year=2026,
        auto_paginate=True,
    )
    assert req.auto_paginate is True


def test_agent_run_request_auto_paginate_defaults_false():
    from src.api.schemas import AgentRunRequest

    req = AgentRunRequest(
        url="https://example.com",
        univ_slug="test",
        year=2026,
    )
    assert req.auto_paginate is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_agent_api_entrypoints.py::test_agent_run_request_accepts_auto_paginate -v`
Expected: FAIL — field doesn't exist.

- [ ] **Step 3: Add `auto_paginate` to `AgentRunRequest`**

In `src/api/schemas.py`, add after the `dry_run` field in `AgentRunRequest`:

```python
    auto_paginate: bool = Field(
        default=False,
        description="When True, agent auto-paginates index pages and collects courses from all pages with quality checks",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_agent_api_entrypoints.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/schemas.py tests/test_agent_api_entrypoints.py
git commit -m "feat(pagination): add auto_paginate field to AgentRunRequest schema"
```

---

### Task 7: Extension UI — Auto-Paginate Checkbox

**Files:**
- Modify: `extension/src/popup.html`
- Modify: `extension/src/popup/dom.ts`
- Modify: `extension/src/popup/crawlApi.ts`
- Modify: `extension/src/popup/crawlFlow.ts`

- [ ] **Step 1: Add checkbox to popup.html**

In `extension/src/popup.html`, after the page-type `<select>` block (after line 49 `</div>`), add:

```html
            <div class="field checkbox-field" id="auto-paginate-field">
                <label>
                    <input id="auto-paginate" type="checkbox" />
                    Auto-paginate (collect all pages)
                </label>
            </div>
```

- [ ] **Step 2: Add DOM export**

In `extension/src/popup/dom.ts`, add after the `pageTypeSelect` export:

```typescript
export const autoPaginateCheckbox = document.getElementById("auto-paginate") as HTMLInputElement;
export const autoPaginateField = document.getElementById("auto-paginate-field") as HTMLDivElement;
```

- [ ] **Step 3: Update crawlApi.ts**

In `extension/src/popup/crawlApi.ts`, add to `SubmitAgentRunOpts`:

```typescript
export interface SubmitAgentRunOpts {
    url: string;
    slug: string;
    year: number;
    pageType: string;
    autoPaginate?: boolean;  // new
}
```

In the `submitAgentRun` function body, add `auto_paginate` to the payload object:

```typescript
    const payload = {
        // ...existing fields...
        auto_paginate: opts.autoPaginate ?? false,  // new
    };
```

- [ ] **Step 4: Wire checkbox in crawlFlow.ts**

In `extension/src/popup/crawlFlow.ts`, add the import:

```typescript
import { autoPaginateCheckbox, autoPaginateField } from "./dom";
```

Inside `initCrawlFlow`, add visibility toggle logic — after `sendBtn.addEventListener("click", async () => {` and before the agent mode block, the checkbox should be visible only for index/auto page types. Add this at the top of `initCrawlFlow`:

```typescript
    // Show/hide auto-paginate based on page type
    const updateAutoPaginateVisibility = () => {
        const pageType = pageTypeSelect.value;
        autoPaginateField.style.display =
            pageType === "detail" ? "none" : "block";
    };
    pageTypeSelect.addEventListener("change", updateAutoPaginateVisibility);
    updateAutoPaginateVisibility();
```

In the agent mode block of the `sendBtn` click handler, pass `autoPaginate`:

```typescript
                const taskId = await submitAgentRun(
                    { url, slug, year, pageType, autoPaginate: autoPaginateCheckbox.checked },
                    apiBase,
                    apiCallbacks,
                );
```

- [ ] **Step 5: Build extension to verify no errors**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent/extension && npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 6: Commit**

```bash
git add extension/src/popup.html extension/src/popup/dom.ts extension/src/popup/crawlApi.ts extension/src/popup/crawlFlow.ts
git commit -m "feat(pagination): add auto-paginate checkbox to extension UI"
```

---

### Task 8: Integration Test with Golden Samples

**Files:**
- Test: `tests/test_pagination_detector.py` (extend with golden sample HTML)

- [ ] **Step 1: Write golden sample integration tests**

Append to `tests/test_pagination_detector.py`:

```python
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden_samples" / "cases"


class TestGoldenSampleDetection:
    """Test pagination detection against actual golden sample HTML files."""

    def test_edinburgh_real_html(self):
        html_path = GOLDEN_DIR / "edinburgh_undergrad_accounting_business" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://study.ed.ac.uk/programmes/undergraduate?page=0"
        )
        assert result.pagination_type == "url_param"
        assert result.total_pages is not None and result.total_pages >= 70
        assert len(result.page_urls) >= 70
        assert result.confidence >= 0.8

    def test_leeds_real_html(self):
        html_path = GOLDEN_DIR / "leeds_masters_ai_business" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://courses.leeds.ac.uk/course-search/masters-courses"
        )
        assert result.pagination_type == "url_param"
        assert result.total_pages is not None and result.total_pages >= 15
        assert result.confidence >= 0.8

    def test_ucl_real_html(self):
        html_path = GOLDEN_DIR / "ucl_undergrad_anthropology" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://www.ucl.ac.uk/prospective-students/undergraduate/degrees"
        )
        assert result.pagination_type == "single_page"

    def test_polyu_real_html(self):
        html_path = GOLDEN_DIR / "polyu_masters_asset_wealth" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://www.polyu.edu.hk/study/pg/taught-postgraduate"
        )
        assert result.pagination_type == "single_page"

    def test_manchester_real_html(self):
        html_path = GOLDEN_DIR / "manchester_masters_business_psychology" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://www.manchester.ac.uk/study/masters/courses/list/"
        )
        assert result.pagination_type == "single_page"
```

- [ ] **Step 2: Run golden sample tests**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_pagination_detector.py::TestGoldenSampleDetection -v`
Expected: All 5 tests PASS (or skip if golden samples missing).

- [ ] **Step 3: Fix any detection issues found**

If any golden sample fails, adjust the regex patterns in `pagination_detector.py`. Common issues:
- PolyU `swiper-pagination` matching Strategy 1 → add exclusion for `swiper-pagination` class
- Manchester having no pagination but having page-like URLs in navigation → ensure Strategy 2 threshold (>= 3 distinct values) filters these out

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/seal/Documents/coding/uni-admission-agent && uv run pytest tests/test_pagination_detector.py tests/test_quality_circuit_breaker.py tests/test_paginated_crawl_skill.py tests/test_agent_skill_registry.py tests/test_tool_trimming.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_pagination_detector.py
git commit -m "test(pagination): add golden sample integration tests for pagination detector"
```

---

Plan complete and saved to `docs/superpowers/plans/2026-04-12-auto-pagination.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?