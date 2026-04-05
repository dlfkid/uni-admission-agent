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


def _extract_element_value(el: Any, spec: FieldSpec) -> str | None:
    """Extract a value from a BeautifulSoup element based on FieldSpec."""
    if spec.attribute == "text":
        text = el.get_text(strip=True)
        return text if text else None
    else:
        val = el.get(spec.attribute)
        return str(val) if val else None
