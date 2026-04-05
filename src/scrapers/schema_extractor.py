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
