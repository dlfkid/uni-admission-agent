# Schema-Based Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-page LLM extraction with CSS-selector-based extraction learned from the first page, with LLM fallback for missing fields.

**Architecture:** A new `src/scrapers/schema_extractor.py` module provides `SelectorSchema`, `SchemaLearner`, `SelectorExtractor`, and `SchemaManager`. The existing `_auto_fetch_and_extract()` in `common.py` is modified to use selector extraction for pages 2-N after learning from page 1.

**Tech Stack:** Python 3.12+, BeautifulSoup4, pydantic, existing RouterAgent/LLMCleanerAgent

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/scrapers/schema_extractor.py` | All 4 components: SelectorSchema, SchemaLearner, SelectorExtractor, SchemaManager |
| Create | `tests/test_schema_extractor.py` | Unit tests |
| Modify | `src/agent_runtime/skills/impl/common.py:376-479` | Integrate schema extraction into `_auto_fetch_and_extract()` |

---

### Task 1: SelectorSchema data model + SchemaManager file I/O

**Files:**
- Create: `src/scrapers/schema_extractor.py`
- Create: `tests/test_schema_extractor.py`

- [ ] **Step 1: Write failing tests for SelectorSchema and SchemaManager**

```python
"""Tests for src.scrapers.schema_extractor."""

import json
import time
from pathlib import Path

import pytest

from src.scrapers.schema_extractor import FieldSpec, SelectorSchema, SchemaManager


class TestSelectorSchema:
    """Tests for the SelectorSchema data model."""

    def test_create_schema_with_fields(self):
        schema = SelectorSchema(
            version=1,
            univ_slug="edinburgh",
            page_pattern="postgraduate-taught",
            source_url="https://example.com/page1",
            baseline_score=0.83,
            total_fields=6,
            fields={
                "faculty": FieldSpec(selector="div.school a", attribute="text", sample_value="Medical School"),
                "name_en": FieldSpec(selector="h1.page-title", attribute="text"),
            },
        )
        assert schema.univ_slug == "edinburgh"
        assert schema.baseline_score == 0.83
        assert schema.fields["faculty"].selector == "div.school a"

    def test_schema_roundtrip_json(self, tmp_path):
        schema = SelectorSchema(
            version=1,
            univ_slug="edinburgh",
            page_pattern="postgraduate-taught",
            source_url="https://example.com/page1",
            baseline_score=0.85,
            total_fields=6,
            fields={
                "name_en": FieldSpec(selector="h1", attribute="text"),
                "faculty": FieldSpec(selector="div.school", attribute="text", sample_value="Engineering"),
            },
        )
        path = tmp_path / "test_schema.json"
        path.write_text(schema.model_dump_json(indent=2), encoding="utf-8")
        loaded = SelectorSchema.model_validate_json(path.read_text(encoding="utf-8"))
        assert loaded.univ_slug == "edinburgh"
        assert loaded.fields["faculty"].sample_value == "Engineering"


class TestSchemaManager:
    """Tests for SchemaManager file operations."""

    def test_save_and_load(self, tmp_path):
        mgr = SchemaManager(schemas_dir=tmp_path)
        schema = SelectorSchema(
            version=1,
            univ_slug="edinburgh",
            page_pattern="postgraduate-taught",
            source_url="https://example.com",
            baseline_score=0.80,
            total_fields=6,
            fields={"name_en": FieldSpec(selector="h1", attribute="text")},
        )
        mgr.save(schema)
        loaded = mgr.load("edinburgh", "postgraduate-taught")
        assert loaded is not None
        assert loaded.baseline_score == 0.80

    def test_load_nonexistent_returns_none(self, tmp_path):
        mgr = SchemaManager(schemas_dir=tmp_path)
        assert mgr.load("nonexistent", "pattern") is None

    def test_deprecate_renames_file(self, tmp_path):
        mgr = SchemaManager(schemas_dir=tmp_path)
        schema = SelectorSchema(
            version=1,
            univ_slug="ucl",
            page_pattern="taught-degrees",
            source_url="https://example.com",
            baseline_score=0.90,
            total_fields=6,
            fields={"name_en": FieldSpec(selector="h1", attribute="text")},
        )
        mgr.save(schema)
        mgr.deprecate("ucl", "taught-degrees")
        assert mgr.load("ucl", "taught-degrees") is None
        deprecated = list(tmp_path.glob("*.deprecated.json"))
        assert len(deprecated) == 1

    def test_load_corrupted_json_returns_none(self, tmp_path):
        mgr = SchemaManager(schemas_dir=tmp_path)
        bad_file = tmp_path / "bad_schema.json"
        bad_file.write_text("{not valid json", encoding="utf-8")
        # Direct file load should handle gracefully
        schema_file = tmp_path / "edinburgh_postgraduate-taught.json"
        schema_file.write_text("{broken", encoding="utf-8")
        assert mgr.load("edinburgh", "postgraduate-taught") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.scrapers.schema_extractor'`

- [ ] **Step 3: Implement SelectorSchema and SchemaManager**

```python
"""
Schema-based extraction for university detail pages.

Learns CSS selectors from the first page via LLM, then applies them
to subsequent pages without LLM calls. Falls back to LLM for missing fields.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class FieldSpec(BaseModel):
    """Describes how to extract one field from HTML via CSS selector."""
    selector: str
    attribute: str = "text"
    sample_value: Optional[str] = None
    is_list: bool = False
    post_process: Optional[str] = None


class SelectorSchema(BaseModel):
    """Maps program data fields to CSS selectors for a specific university page type."""
    version: int = 1
    univ_slug: str
    page_pattern: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_url: str = ""
    baseline_score: float = 0.0
    total_fields: int = 6
    fields: dict[str, FieldSpec] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# SchemaManager — file I/O, deprecation
# ---------------------------------------------------------------------------

class SchemaManager:
    """Manages SelectorSchema JSON files on disk."""

    def __init__(self, schemas_dir: Path | None = None) -> None:
        if schemas_dir is None:
            schemas_dir = Path.cwd() / ".adm-agent" / "schemas"
        self.schemas_dir = schemas_dir

    def _schema_path(self, univ_slug: str, page_pattern: str) -> Path:
        return self.schemas_dir / f"{univ_slug}_{page_pattern}.json"

    def load(self, univ_slug: str, page_pattern: str) -> SelectorSchema | None:
        path = self._schema_path(univ_slug, page_pattern)
        if not path.exists():
            return None
        try:
            return SelectorSchema.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Corrupted schema file %s, deleting", path)
            path.unlink(missing_ok=True)
            return None

    def save(self, schema: SelectorSchema) -> Path:
        self.schemas_dir.mkdir(parents=True, exist_ok=True)
        path = self._schema_path(schema.univ_slug, schema.page_pattern)
        path.write_text(schema.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Schema saved: %s (baseline=%.2f)", path.name, schema.baseline_score)
        return path

    def deprecate(self, univ_slug: str, page_pattern: str) -> None:
        path = self._schema_path(univ_slug, page_pattern)
        if path.exists():
            deprecated = path.with_suffix(".deprecated.json")
            path.rename(deprecated)
            logger.info("Schema deprecated: %s → %s", path.name, deprecated.name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema_extractor.py -v`
Expected: All 5 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/scrapers/schema_extractor.py tests/test_schema_extractor.py
git commit -m "feat: add SelectorSchema data model and SchemaManager file I/O"
```

---

### Task 2: SelectorExtractor — CSS selector extraction + post-processors

**Files:**
- Modify: `src/scrapers/schema_extractor.py`
- Modify: `tests/test_schema_extractor.py`

- [ ] **Step 1: Write failing tests for SelectorExtractor**

Append to `tests/test_schema_extractor.py`:

```python
from src.scrapers.schema_extractor import SelectorExtractor, FieldSpec, SelectorSchema


SAMPLE_HTML = """
<html>
<body>
<h1 class="page-title">Cognitive Science MSc</h1>
<div class="field-school"><a href="/school">School of Informatics</a></div>
<div class="tuition-fee">£12,500 per year</div>
<div class="study-options">
  <div class="option">Full-time | 1 year</div>
  <div class="option">Part-time | 2 years</div>
</div>
<table class="deadlines">
  <tr><td>Round 1</td><td>31 January 2026</td></tr>
  <tr><td>Round 2</td><td>30 June 2026</td></tr>
</table>
<div class="entry-requirements">
  <p>A UK 2:1 honours degree in psychology, linguistics, or related discipline.</p>
</div>
</body>
</html>
"""


class TestSelectorExtractor:
    """Tests for CSS selector extraction."""

    def test_extract_text_field(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"name_en": FieldSpec(selector="h1.page-title", attribute="text")},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert result["name_en"] == "Cognitive Science MSc"

    def test_extract_missing_field_returns_none(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"faculty": FieldSpec(selector="div.nonexistent", attribute="text")},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert result["faculty"] is None

    def test_extract_list_field(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"study_options": FieldSpec(
                selector="div.study-options div.option",
                attribute="text", is_list=True,
            )},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert len(result["study_options"]) == 2
        assert "Full-time" in result["study_options"][0]

    def test_extract_href_attribute(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"school_link": FieldSpec(
                selector="div.field-school a", attribute="href",
            )},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert result["school_link"] == "/school"

    def test_post_process_extract_decimal(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"tuition_amount": FieldSpec(
                selector="div.tuition-fee", attribute="text",
                post_process="extract_decimal",
            )},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert result["tuition_amount"] == Decimal("12500")

    def test_compute_score(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test", total_fields=3,
            fields={
                "name_en": FieldSpec(selector="h1.page-title", attribute="text"),
                "faculty": FieldSpec(selector="div.nonexistent", attribute="text"),
                "tuition_amount": FieldSpec(selector="div.tuition-fee", attribute="text"),
            },
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        score = SelectorExtractor.compute_score(result, schema)
        # 2 out of 3 fields have values
        assert abs(score - 2 / 3) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema_extractor.py::TestSelectorExtractor -v`
Expected: FAIL — `ImportError: cannot import name 'SelectorExtractor'`

- [ ] **Step 3: Implement SelectorExtractor with post-processors**

Append to `src/scrapers/schema_extractor.py`:

```python
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Post-processors
# ---------------------------------------------------------------------------

_DECIMAL_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def _post_extract_decimal(text: str) -> Decimal | None:
    """Extract first decimal number from text like '£12,500 per year'."""
    m = _DECIMAL_RE.search(text)
    if not m:
        return None
    try:
        return Decimal(m.group().replace(",", ""))
    except InvalidOperation:
        return None


def _post_parse_study_option(text: str) -> dict[str, Any] | None:
    """Parse 'Full-time | 1 year' into {mode, duration_months}."""
    text = text.strip()
    if not text:
        return None
    mode = "Unknown"
    lower = text.lower()
    if "full-time" in lower or "full time" in lower:
        mode = "FullTime"
    elif "part-time" in lower or "part time" in lower:
        mode = "PartTime"
    elif "online" in lower:
        mode = "PartTime"
    duration_months = 0
    dur_match = re.search(r"(\d+)\s*year", lower)
    if dur_match:
        duration_months = int(dur_match.group(1)) * 12
    else:
        dur_match = re.search(r"(\d+)\s*month", lower)
        if dur_match:
            duration_months = int(dur_match.group(1))
    return {"mode": mode, "duration_months": duration_months}


def _post_parse_deadline(text: str) -> dict[str, Any] | None:
    """Parse deadline text into {description, cutoff_date}."""
    text = text.strip()
    if not text:
        return None
    # Try to find a date pattern
    date_match = re.search(
        r"(\d{1,2}\s+\w+\s+\d{4})", text
    )
    cutoff_date = None
    if date_match:
        from dateutil import parser as dateparser
        try:
            cutoff_date = dateparser.parse(date_match.group(1)).isoformat()
        except (ValueError, TypeError):
            pass
    return {"description": text, "cutoff_date": cutoff_date}


def _post_parse_requirements(text: str) -> str:
    """Clean up requirement text — just strip whitespace."""
    return re.sub(r"\s+", " ", text).strip()


_POST_PROCESSORS: dict[str, Any] = {
    "extract_decimal": _post_extract_decimal,
    "parse_study_option": _post_parse_study_option,
    "parse_deadline": _post_parse_deadline,
    "parse_requirements": _post_parse_requirements,
}


# ---------------------------------------------------------------------------
# SelectorExtractor
# ---------------------------------------------------------------------------

class SelectorExtractor:
    """Extracts program data from HTML using CSS selectors from a SelectorSchema."""

    @staticmethod
    def extract(html: str, schema: SelectorSchema) -> dict[str, Any]:
        """Run all selectors against HTML, return {field_name: value_or_None}."""
        soup = BeautifulSoup(html, "html.parser")
        results: dict[str, Any] = {}
        for field_name, spec in schema.fields.items():
            try:
                elements = soup.select(spec.selector)
            except Exception:
                results[field_name] = None
                continue

            if not elements:
                results[field_name] = None
                continue

            if spec.is_list:
                raw_values = [_extract_element_value(el, spec) for el in elements]
            else:
                raw_values = [_extract_element_value(elements[0], spec)]

            # Apply post-processor if specified
            processor = _POST_PROCESSORS.get(spec.post_process) if spec.post_process else None
            if processor:
                if spec.is_list:
                    results[field_name] = [processor(v) for v in raw_values if v]
                else:
                    results[field_name] = processor(raw_values[0]) if raw_values[0] else None
            else:
                if spec.is_list:
                    results[field_name] = raw_values
                else:
                    results[field_name] = raw_values[0]

        return results

    @staticmethod
    def compute_score(result: dict[str, Any], schema: SelectorSchema) -> float:
        """Compute extraction score: fraction of fields with non-None values."""
        if not schema.fields:
            return 0.0
        hit = sum(1 for v in result.values() if v is not None)
        return hit / len(schema.fields)

    @staticmethod
    def missing_fields(result: dict[str, Any]) -> list[str]:
        """Return field names that have None values."""
        return [k for k, v in result.items() if v is None]


def _extract_element_value(el: Any, spec: FieldSpec) -> str | None:
    """Extract a value from a BeautifulSoup element based on FieldSpec."""
    if spec.attribute == "text":
        text = el.get_text(strip=True)
        return text if text else None
    else:
        val = el.get(spec.attribute)
        return str(val) if val else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema_extractor.py -v`
Expected: All 11 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/scrapers/schema_extractor.py tests/test_schema_extractor.py
git commit -m "feat: add SelectorExtractor with CSS extraction and post-processors"
```

---

### Task 3: SchemaLearner — LLM-based selector inference

**Files:**
- Modify: `src/scrapers/schema_extractor.py`
- Modify: `tests/test_schema_extractor.py`

- [ ] **Step 1: Write failing test for SchemaLearner**

Append to `tests/test_schema_extractor.py`:

```python
from unittest.mock import MagicMock, patch
from src.scrapers.schema_extractor import SchemaLearner


class TestSchemaLearner:
    """Tests for LLM-based selector learning."""

    def test_learn_produces_schema_with_validated_selectors(self):
        """SchemaLearner should call LLM and validate returned selectors."""
        html = SAMPLE_HTML
        extracted_data = {
            "name_en": "Cognitive Science MSc",
            "faculty": "School of Informatics",
            "tuition_amount": "£12,500 per year",
        }
        # Mock the LLM to return known-good selectors
        mock_llm_response = {
            "fields": {
                "name_en": {"selector": "h1.page-title", "attribute": "text"},
                "faculty": {"selector": "div.field-school a", "attribute": "text"},
                "tuition_amount": {"selector": "div.tuition-fee", "attribute": "text", "post_process": "extract_decimal"},
            }
        }

        mock_router = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps(mock_llm_response)
        mock_router.generate.return_value = mock_response

        schema = SchemaLearner.learn(
            html=html,
            extracted_data=extracted_data,
            router=mock_router,
            univ_slug="edinburgh",
            page_pattern="postgraduate-taught",
            source_url="https://example.com/page1",
        )

        assert schema is not None
        assert schema.univ_slug == "edinburgh"
        assert "name_en" in schema.fields
        # Baseline score should reflect validation (all 3 selectors work on the HTML)
        assert schema.baseline_score > 0

    def test_learn_returns_none_on_llm_failure(self):
        """If LLM fails, learn() should return None."""
        mock_router = MagicMock()
        mock_router.generate.side_effect = Exception("LLM timeout")

        schema = SchemaLearner.learn(
            html="<html></html>",
            extracted_data={"name_en": "Test"},
            router=mock_router,
            univ_slug="test",
            page_pattern="test",
            source_url="https://example.com",
        )
        assert schema is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema_extractor.py::TestSchemaLearner -v`
Expected: FAIL — `ImportError: cannot import name 'SchemaLearner'`

- [ ] **Step 3: Implement SchemaLearner**

Append to `src/scrapers/schema_extractor.py`:

```python
# ---------------------------------------------------------------------------
# SchemaLearner — LLM selector inference
# ---------------------------------------------------------------------------

_LEARNER_SYSTEM_PROMPT = """\
You are an HTML structure analysis expert. Return ONLY valid JSON matching the schema below.

{
  "fields": {
    "<field_name>": {
      "selector": "<css_selector>",
      "attribute": "text" | "href" | "<attr_name>",
      "is_list": true | false,
      "post_process": null | "extract_decimal" | "parse_study_option" | "parse_deadline" | "parse_requirements"
    }
  }
}
"""

_LEARNER_USER_PROMPT = """\
I have a university course page's HTML and structured data already extracted from it.

For each extracted field, find where its value appears in the HTML and return a CSS selector
that can reliably locate that value on other pages with the same template.

Requirements:
- Use class/id-based selectors. Avoid fragile positional selectors (div > div > span).
- If a value appears in multiple places, prefer the most semantically clear location.
- If a field's value cannot be found in the HTML, omit that field entirely.
- For numeric fields (tuition), set post_process to "extract_decimal".
- For study option lists, set is_list=true and post_process="parse_study_option".
- For deadline lists, set is_list=true and post_process="parse_deadline".
- For requirements text, set post_process="parse_requirements".

Extracted data:
{extracted_data}

HTML:
{html}
"""


class _LearnerOutput(BaseModel):
    """Schema for LLM response in selector learning."""
    fields: dict[str, FieldSpec]


class SchemaLearner:
    """Learns CSS selectors by asking LLM to reverse-engineer HTML structure."""

    @staticmethod
    def learn(
        html: str,
        extracted_data: dict[str, Any],
        router: Any,
        univ_slug: str,
        page_pattern: str,
        source_url: str,
    ) -> SelectorSchema | None:
        """Infer CSS selectors from HTML + extracted data via LLM, then validate.

        Returns a validated SelectorSchema, or None if LLM fails.
        """
        # Strip boilerplate to reduce HTML size
        stripped = _strip_for_learner(html)

        # Build prompt with only non-empty extracted fields
        filtered_data = {k: v for k, v in extracted_data.items() if v}
        if not filtered_data:
            logger.warning("[SchemaLearner] No extracted data to learn from")
            return None

        prompt = _LEARNER_USER_PROMPT.format(
            extracted_data=json.dumps(filtered_data, indent=2, default=str),
            html=stripped,
        )

        try:
            response = router.generate(
                prompt=_LEARNER_SYSTEM_PROMPT + "\n\n" + prompt,
                schema=_LearnerOutput,
            )
            # Parse response — handle both structured output and raw JSON
            if hasattr(response, "content") and isinstance(response.content, str):
                parsed = json.loads(response.content)
                fields = {
                    k: FieldSpec(**v) if isinstance(v, dict) else v
                    for k, v in parsed.get("fields", {}).items()
                }
            elif hasattr(response, "parsed") and response.parsed:
                fields = response.parsed.fields
            else:
                logger.warning("[SchemaLearner] Unexpected LLM response format")
                return None
        except Exception as exc:
            logger.warning("[SchemaLearner] LLM call failed: %s", exc)
            return None

        if not fields:
            logger.warning("[SchemaLearner] LLM returned no selectors")
            return None

        # Build schema and validate selectors against the same HTML
        schema = SelectorSchema(
            univ_slug=univ_slug,
            page_pattern=page_pattern,
            source_url=source_url,
            total_fields=len(fields),
            fields=fields,
        )

        # Validate: run selectors on the learning page
        result = SelectorExtractor.extract(html, schema)
        score = SelectorExtractor.compute_score(result, schema)
        schema.baseline_score = score

        logger.info(
            "[SchemaLearner] Learned %d selectors, baseline=%.2f for %s_%s",
            len(fields), score, univ_slug, page_pattern,
        )
        return schema


def _strip_for_learner(html: str, max_chars: int = 30000) -> str:
    """Reduce HTML size for the learner prompt.

    1. Remove <script>, <style>, <nav>, <header>, <footer> blocks.
    2. If still over max_chars, truncate (keeping head + tail).
    """
    # Remove script/style/nav/header/footer
    html = re.sub(r"<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # Collapse whitespace
    html = re.sub(r"\n{3,}", "\n\n", html)

    if len(html) <= max_chars:
        return html

    # Keep first half + last portion
    half = max_chars // 2
    return html[:half] + "\n<!-- ... truncated ... -->\n" + html[-half:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema_extractor.py -v`
Expected: All 13 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/scrapers/schema_extractor.py tests/test_schema_extractor.py
git commit -m "feat: add SchemaLearner for LLM-based selector inference"
```

---

### Task 4: Fallback logic — field-level and full-page LLM fallback

**Files:**
- Modify: `src/scrapers/schema_extractor.py`
- Modify: `tests/test_schema_extractor.py`

- [ ] **Step 1: Write failing tests for fallback**

Append to `tests/test_schema_extractor.py`:

```python
from src.scrapers.schema_extractor import FallbackHandler


class TestFallbackHandler:
    """Tests for field-level and full-page LLM fallback."""

    def test_no_fallback_when_all_fields_present(self):
        result = {"name_en": "Test", "faculty": "Engineering", "tuition_amount": Decimal("10000")}
        decision = FallbackHandler.decide(result, total_fields=3)
        assert decision == "none"

    def test_field_level_fallback_when_few_missing(self):
        result = {"name_en": "Test", "faculty": None, "tuition_amount": None}
        decision = FallbackHandler.decide(result, total_fields=3)
        assert decision == "field"

    def test_full_page_fallback_when_many_missing(self):
        result = {
            "name_en": None, "faculty": None,
            "tuition_amount": None, "deadlines": None,
        }
        decision = FallbackHandler.decide(result, total_fields=4)
        assert decision == "full"

    def test_field_fallback_threshold_is_3(self):
        # Exactly 3 missing → field-level
        result = {"a": "ok", "b": None, "c": None, "d": None, "e": "ok", "f": "ok"}
        decision = FallbackHandler.decide(result, total_fields=6)
        assert decision == "field"

    def test_four_missing_triggers_full(self):
        # 4 missing → full-page
        result = {"a": "ok", "b": None, "c": None, "d": None, "e": None, "f": "ok"}
        decision = FallbackHandler.decide(result, total_fields=6)
        assert decision == "full"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema_extractor.py::TestFallbackHandler -v`
Expected: FAIL — `ImportError: cannot import name 'FallbackHandler'`

- [ ] **Step 3: Implement FallbackHandler**

Append to `src/scrapers/schema_extractor.py`:

```python
# ---------------------------------------------------------------------------
# FallbackHandler
# ---------------------------------------------------------------------------

_FIELD_FALLBACK_SYSTEM = """\
You are a data extraction assistant. Return ONLY valid JSON matching the requested fields.
If a field is not found on the page, use null."""

_FIELD_FALLBACK_USER = """\
Extract ONLY the following fields from this university programme page HTML.
Return JSON with only these keys.

Fields to extract: {field_names}

Field definitions:
- name_en: The full English name of the programme (e.g., "Cognitive Science MSc")
- faculty: The school, faculty, or department offering the programme
- tuition_amount: Annual tuition fee as a number (omit currency symbol)
- study_options: List of study modes with duration (e.g., "Full-time, 1 year")
- deadlines: Application deadline dates
- requirements: Entry requirements text

HTML:
{html}
"""


class _FieldFallbackOutput(BaseModel):
    """Dynamic output — fields are optional strings/lists."""
    class Config:
        extra = "allow"


class FallbackHandler:
    """Decides and executes LLM fallback for missing fields."""

    FIELD_THRESHOLD = 3  # ≤ 3 missing → field-level, > 3 → full-page

    @staticmethod
    def decide(result: dict[str, Any], total_fields: int) -> str:
        """Return 'none', 'field', or 'full' based on missing field count."""
        missing = sum(1 for v in result.values() if v is None)
        if missing == 0:
            return "none"
        if missing <= FallbackHandler.FIELD_THRESHOLD:
            return "field"
        return "full"

    @staticmethod
    def field_fallback(
        html: str,
        missing_field_names: list[str],
        router: Any,
    ) -> dict[str, Any]:
        """Ask LLM to extract only the missing fields from HTML."""
        stripped = _strip_for_learner(html)
        prompt = _FIELD_FALLBACK_USER.format(
            field_names=", ".join(missing_field_names),
            html=stripped,
        )
        try:
            response = router.generate(
                prompt=_FIELD_FALLBACK_SYSTEM + "\n\n" + prompt,
                schema=_FieldFallbackOutput,
            )
            if hasattr(response, "content") and isinstance(response.content, str):
                return json.loads(response.content)
            elif hasattr(response, "parsed") and response.parsed:
                return response.parsed.model_dump()
            return {}
        except Exception as exc:
            logger.warning("[FallbackHandler] Field fallback failed: %s", exc)
            return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema_extractor.py -v`
Expected: All 18 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/scrapers/schema_extractor.py tests/test_schema_extractor.py
git commit -m "feat: add FallbackHandler with field-level and full-page LLM fallback"
```

---

### Task 5: page_pattern derivation utility

**Files:**
- Modify: `src/scrapers/schema_extractor.py`
- Modify: `tests/test_schema_extractor.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_schema_extractor.py`:

```python
from src.scrapers.schema_extractor import derive_page_pattern


class TestDerivePagePattern:
    def test_edinburgh(self):
        assert derive_page_pattern(
            "https://study.ed.ac.uk/programmes/postgraduate-taught?page=5"
        ) == "postgraduate-taught"

    def test_ucl(self):
        assert derive_page_pattern(
            "https://www.ucl.ac.uk/prospective-students/graduate/taught-degrees"
        ) == "graduate_taught-degrees"

    def test_strips_query_params(self):
        result = derive_page_pattern("https://example.com/courses/masters?filter=cs&page=2")
        assert "?" not in result
        assert result == "masters"

    def test_empty_path_returns_default(self):
        assert derive_page_pattern("https://example.com/") == "default"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema_extractor.py::TestDerivePagePattern -v`
Expected: FAIL — `ImportError: cannot import name 'derive_page_pattern'`

- [ ] **Step 3: Implement derive_page_pattern**

Append to `src/scrapers/schema_extractor.py`:

```python
# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def derive_page_pattern(index_url: str) -> str:
    """Derive a schema file key from an index URL.

    Examples:
        .../programmes/postgraduate-taught?page=5 → "postgraduate-taught"
        .../graduate/taught-degrees → "graduate_taught-degrees"
    """
    from urllib.parse import urlparse
    parsed = urlparse(index_url)
    path = parsed.path.rstrip("/")
    if not path:
        return "default"
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "default"
    # Take last 1-2 meaningful segments
    if len(segments) >= 2:
        last_two = segments[-2:]
        # Skip generic segments like "programmes", "prospective-students"
        generic = {"programmes", "courses", "prospective-students", "students", "study"}
        if last_two[0].lower() in generic:
            return last_two[1]
        return f"{last_two[0]}_{last_two[1]}"
    return segments[-1]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema_extractor.py -v`
Expected: All 22 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/scrapers/schema_extractor.py tests/test_schema_extractor.py
git commit -m "feat: add derive_page_pattern utility for schema file keys"
```

---

### Task 6: Integrate into `_auto_fetch_and_extract()`

**Files:**
- Modify: `src/agent_runtime/skills/impl/common.py:376-479`

- [ ] **Step 1: Write integration test**

Append to `tests/test_schema_extractor.py`:

```python
class TestExtractWithSchemaIntegration:
    """Integration test: schema extraction + fallback produces program data."""

    def test_extract_with_schema_returns_all_fields(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test", total_fields=3,
            baseline_score=1.0,
            fields={
                "name_en": FieldSpec(selector="h1.page-title", attribute="text"),
                "faculty": FieldSpec(selector="div.field-school a", attribute="text"),
                "tuition_amount": FieldSpec(
                    selector="div.tuition-fee", attribute="text",
                    post_process="extract_decimal",
                ),
            },
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        score = SelectorExtractor.compute_score(result, schema)
        decision = FallbackHandler.decide(result, total_fields=3)

        assert result["name_en"] == "Cognitive Science MSc"
        assert result["faculty"] == "School of Informatics"
        assert result["tuition_amount"] == Decimal("12500")
        assert score == 1.0
        assert decision == "none"
```

- [ ] **Step 2: Run integration test**

Run: `uv run pytest tests/test_schema_extractor.py::TestExtractWithSchemaIntegration -v`
Expected: PASS

- [ ] **Step 3: Modify `_auto_fetch_and_extract()` in common.py**

Replace the Step 2 section (LLM extraction, lines ~423-479) of `_auto_fetch_and_extract()` with schema-aware extraction:

```python
def _auto_fetch_and_extract(
    urls: list[str],
    link_texts: dict[str, str],
    bridge: ClientAutomationBridge,
    max_workers: int = 5,
    univ_slug: str = "",
    index_url: str = "",
) -> dict:
    """Fetch detail pages in parallel and extract structured data.

    Uses schema-based CSS extraction when possible, falling back to LLM.
    """
    import concurrent.futures
    from src.agents.factory import create_router
    from src.agents.cleaner_agent import LLMCleanerAgent
    from src.models.scraper_models import CrawlPageResult
    from src.scrapers.page_processor import extract_program_data_from_page
    from src.scrapers.schema_extractor import (
        SchemaManager, SchemaLearner, SelectorExtractor,
        FallbackHandler, derive_page_pattern,
    )

    # Step 1: Parallel fetch all detail pages (unchanged)
    def _fetch_one(url: str) -> CrawlPageResult | None:
        try:
            output = bridge.fetch_browser_payload(
                BrowserFetchInput(url=url, page_type_hint="detail")
            )
            raw_html = output.html_content or ""
            md = _html_to_markdown(raw_html, url) if raw_html else ""
            return CrawlPageResult(
                url=url, html=raw_html, markdown=md,
                char_count=len(md), links=[],
            )
        except Exception as exc:
            logger.warning("[AutoExtract] Failed to fetch %s: %s", url, exc)
            return None

    pages: list[CrawlPageResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, url): url for url in urls}
        for future in concurrent.futures.as_completed(futures):
            page = future.result()
            if page and page.html:
                pages.append(page)

    logger.info("[AutoExtract] Fetched %d/%d detail pages", len(pages), len(urls))
    if not pages:
        return {"programs": []}

    # Step 2: Schema-aware extraction
    page_pattern = derive_page_pattern(index_url) if index_url else "default"
    mgr = SchemaManager()
    schema = mgr.load(univ_slug, page_pattern) if univ_slug else None

    # Score tracking for deprecation
    sum_scores = 0.0
    page_count = 0

    def _extract_with_llm(page: CrawlPageResult) -> tuple[dict | None, dict[str, Any]]:
        """Full LLM extraction — returns (program_data, extracted_fields)."""
        router = create_router()
        cleaner = LLMCleanerAgent(router=router)
        anchor_text = link_texts.get(page.url)
        trimmed_md = _strip_boilerplate(page.markdown)
        trimmed_html = _strip_html_boilerplate(page.html) if page.html else page.html
        trimmed_page = CrawlPageResult(
            url=page.url, html=trimmed_html, markdown=trimmed_md,
            char_count=len(trimmed_md), links=page.links,
        )
        try:
            program_data, error = extract_program_data_from_page(
                page=trimmed_page, cleaner=cleaner,
                univ_slug=univ_slug or "", year=0,
                current_depth=0, from_browser=True,
                selected_anchor_text=anchor_text,
            )
            if program_data:
                program_data.pop("academic_year", None)
                program_data["source_url"] = page.url
                return program_data, program_data
            return None, {}
        except Exception as exc:
            logger.warning("[AutoExtract] LLM failed %s: %s", page.url, exc)
            return None, {}

    def _extract_with_schema(page: CrawlPageResult, schema) -> dict | None:
        """Schema-based extraction with fallback."""
        nonlocal sum_scores, page_count

        result = SelectorExtractor.extract(page.html, schema)
        score = SelectorExtractor.compute_score(result, schema)
        sum_scores += score
        page_count += 1

        decision = FallbackHandler.decide(result, total_fields=schema.total_fields)

        if decision == "field":
            missing = SelectorExtractor.missing_fields(result)
            router = create_router()
            supplement = FallbackHandler.field_fallback(page.html, missing, router)
            for field_name in missing:
                if field_name in supplement and supplement[field_name] is not None:
                    result[field_name] = supplement[field_name]
            logger.info("[AutoExtract] Schema + field fallback for %s (score=%.2f, filled %d/%d missing)",
                        page.url, score, len([f for f in missing if result.get(f) is not None]), len(missing))
        elif decision == "full":
            logger.info("[AutoExtract] Schema score too low (%.2f), full LLM for %s", score, page.url)
            program_data, _ = _extract_with_llm(page)
            return program_data
        else:
            logger.info("[AutoExtract] Schema hit all fields for %s (score=%.2f)", page.url, score)

        # Build program_data dict from selector results
        anchor_text = link_texts.get(page.url)
        program_data = {"source_url": page.url}
        if result.get("name_en"):
            program_data["name_en"] = result["name_en"]
        else:
            # Name extraction fallback (existing logic)
            from src.scrapers.helpers import extract_program_name
            name = extract_program_name(page.markdown) if page.markdown else ""
            if not name and anchor_text:
                name = anchor_text
            program_data["name_en"] = name or ""

        if result.get("faculty"):
            program_data["faculty"] = result["faculty"]
        if result.get("tuition_amount"):
            program_data["tuition_amount"] = result["tuition_amount"]
        if result.get("study_options"):
            program_data["study_options"] = result["study_options"]
        if result.get("deadlines"):
            program_data["deadlines"] = result["deadlines"]
        if result.get("requirements"):
            program_data["requirements"] = result["requirements"]

        program_data["extra_metadata"] = {"source_url": page.url, "from_browser": True, "schema_score": score}
        return program_data if program_data.get("name_en") else None

    programs: list[dict] = []

    # --- Page 1: Learn or validate schema ---
    first_page = pages[0]
    remaining_pages = pages[1:]

    if schema:
        # Validate existing schema on first page
        result = SelectorExtractor.extract(first_page.html, schema)
        score = SelectorExtractor.compute_score(result, schema)
        if score < schema.baseline_score * 0.8:
            logger.info("[AutoExtract] Schema validation failed (%.2f < %.2f), rebuilding",
                        score, schema.baseline_score * 0.8)
            mgr.deprecate(univ_slug, page_pattern)
            schema = None

    if not schema:
        # Learn from first page via LLM
        program_data, extracted_fields = _extract_with_llm(first_page)
        if program_data:
            programs.append(program_data)
            # Try to learn schema
            if univ_slug and extracted_fields:
                router = create_router()
                schema = SchemaLearner.learn(
                    html=first_page.html,
                    extracted_data=extracted_fields,
                    router=router,
                    univ_slug=univ_slug,
                    page_pattern=page_pattern,
                    source_url=first_page.url,
                )
                if schema:
                    mgr.save(schema)
    else:
        # Schema validated — use it for first page too
        program_data = _extract_with_schema(first_page, schema)
        if program_data:
            programs.append(program_data)

    # --- Pages 2-N: parallel schema extraction (or LLM fallback) ---
    if remaining_pages:
        if schema:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_extract_with_schema, page, schema): page for page in remaining_pages}
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result:
                        programs.append(result)

            # Check for schema deprecation
            if page_count >= 3 and (sum_scores / page_count) < schema.baseline_score * 0.8:
                logger.warning("[AutoExtract] Schema degraded (avg=%.2f, baseline=%.2f), deprecating",
                               sum_scores / page_count, schema.baseline_score)
                mgr.deprecate(univ_slug, page_pattern)
        else:
            # No schema — fall back to full LLM for all remaining pages
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_extract_with_llm, page): page for page in remaining_pages}
                for future in concurrent.futures.as_completed(futures):
                    program_data, _ = future.result()
                    if program_data:
                        programs.append(program_data)

    logger.info("[AutoExtract] Total programs extracted: %d/%d", len(programs), len(pages))
    return {"programs": programs}
```

- [ ] **Step 4: Update callers to pass `univ_slug` and `index_url`**

In `common.py`, find where `_auto_fetch_and_extract()` is called (in `browser_automation_skill_handler`) and add the new parameters. Search for the call site and add:

```python
auto_result = _auto_fetch_and_extract(
    urls=filtered_urls,
    link_texts=filtered_texts,
    bridge=bridge,
    univ_slug=getattr(payload, 'univ_slug', '') or '',
    index_url=payload.url,
)
```

Note: The `univ_slug` may need to come from the task context. Check the skill input model for `BrowserAutomationSkillInput` — if it doesn't have `univ_slug`, pass empty string and schema learning will be skipped (graceful degradation).

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/test_schema_extractor.py -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/scrapers/schema_extractor.py src/agent_runtime/skills/impl/common.py tests/test_schema_extractor.py
git commit -m "feat: integrate schema-based extraction into auto_fetch_and_extract"
```

---

### Task 7: Add `.adm-agent/` to `.gitignore`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add entry**

Append to `.gitignore`:

```
# Schema extraction runtime artifacts
.adm-agent/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore .adm-agent/ schema artifacts"
```

---

### Task 8: E2E smoke test verification

- [ ] **Step 1: Run Edinburgh smoke test**

Run: `uv run python scripts/e2e_agent_smoke.py --univ edinburgh --url "https://study.ed.ac.uk/programmes/postgraduate-taught?page=5" --page-type index --timeout 900`

Expected:
- Status: DONE
- Programs extracted: >= 8 (out of 10 on the page)
- Schema file created at `.adm-agent/schemas/edinburgh_postgraduate-taught.json`

- [ ] **Step 2: Verify schema file exists and looks reasonable**

```bash
cat .adm-agent/schemas/edinburgh_postgraduate-taught.json | python -m json.tool
```

Expected: JSON with `fields` containing CSS selectors, `baseline_score` > 0.5

- [ ] **Step 3: Run Edinburgh again (reuse schema)**

Run same command again. This time it should:
- Load existing schema (log: "Schema saved" should NOT appear)
- Extract faster (fewer LLM calls)
- Produce similar program count

---

## Spec Coverage Check

| Spec Requirement | Task |
|---|---|
| SelectorSchema data model | Task 1 |
| SchemaManager file I/O + deprecation | Task 1 |
| CSS selector extraction (BeautifulSoup) | Task 2 |
| Post-processors (extract_decimal, parse_study_option, etc.) | Task 2 |
| SchemaLearner LLM inference | Task 3 |
| Immediate validation of learned selectors | Task 3 |
| Field-level fallback (≤ 3 missing) | Task 4 |
| Full-page fallback (> 3 missing) | Task 4 |
| Fallback threshold = 3 | Task 4 |
| page_pattern derivation from URL | Task 5 |
| Integration into _auto_fetch_and_extract() | Task 6 |
| Schema validation on reuse | Task 6 |
| Rolling average deprecation | Task 6 |
| .adm-agent/ in .gitignore | Task 7 |
| Smoke test verification | Task 8 |
