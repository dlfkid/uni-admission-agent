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
    date_match = re.search(r"(\d{1,2}\s+\w+\s+\d{4})", text)
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
        stripped = _strip_for_learner(html)
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

        schema = SelectorSchema(
            univ_slug=univ_slug, page_pattern=page_pattern,
            source_url=source_url, total_fields=len(fields), fields=fields,
        )
        result = SelectorExtractor.extract(html, schema)
        score = SelectorExtractor.compute_score(result, schema)
        schema.baseline_score = score

        logger.info("[SchemaLearner] Learned %d selectors, baseline=%.2f for %s_%s",
                    len(fields), score, univ_slug, page_pattern)
        return schema


def _strip_for_learner(html: str, max_chars: int = 30000) -> str:
    html = re.sub(r"<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"\n{3,}", "\n\n", html)
    if len(html) <= max_chars:
        return html
    half = max_chars // 2
    return html[:half] + "\n<!-- ... truncated ... -->\n" + html[-half:]


def _extract_element_value(el: Any, spec: FieldSpec) -> str | None:
    """Extract a value from a BeautifulSoup element based on FieldSpec."""
    if spec.attribute == "text":
        text = el.get_text(strip=True)
        return text if text else None
    else:
        val = el.get(spec.attribute)
        return str(val) if val else None
