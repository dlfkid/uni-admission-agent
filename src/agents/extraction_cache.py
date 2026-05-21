"""Persistent extraction cache for LLM cleaner outputs.

Avoids redundant LLM calls when the same markdown (with the same hints
and version) is processed more than once — across retries, restarts,
and repeat crawls of the same page.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlmodel import Field, Session, SQLModel, select


logger = logging.getLogger(__name__)


class ExtractionCacheEntry(SQLModel, table=True):
    """Single cached LLM cleaner output, keyed by content hash."""

    __tablename__ = "extraction_cache"

    cache_key: str = Field(primary_key=True, max_length=64)
    payload: str = Field(description="Serialized ParsedProgramData JSON")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExtractionCacheRepo:
    """Thin repository wrapping ExtractionCacheEntry CRUD."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, cache_key: str) -> Optional["ParsedProgramData"]:  # noqa: F821
        # Import lazily to avoid a circular dependency with cleaner_agent.
        from src.agents.cleaner_agent import ParsedProgramData

        entry = self._session.get(ExtractionCacheEntry, cache_key)
        if entry is None:
            return None
        try:
            data = json.loads(entry.payload)
            return ParsedProgramData.model_validate(data)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "extraction_cache: failed to decode entry %s: %s", cache_key, exc
            )
            return None

    def put(self, cache_key: str, parsed: "ParsedProgramData") -> None:  # noqa: F821
        payload = parsed.model_dump_json()
        existing = self._session.get(ExtractionCacheEntry, cache_key)
        if existing is None:
            self._session.add(
                ExtractionCacheEntry(cache_key=cache_key, payload=payload)
            )
        else:
            existing.payload = payload
            existing.created_at = datetime.now(timezone.utc)
        self._session.commit()


def _normalize_markdown(text: str) -> str:
    """Stabilize markdown for cache keying.

    Folds CRLF to LF and trims trailing whitespace so cosmetic
    differences do not cause cache misses.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).rstrip()


def _normalize_hints(hints: Optional[List[str]]) -> List[str]:
    """Sort hints so order does not affect the cache key."""
    if not hints:
        return []
    return sorted(str(h) for h in hints)


def compute_cache_key(
    *,
    markdown: str,
    name_hints: Optional[List[str]],
    academic_year: int,
    version: str,
) -> str:
    """Return a stable sha256 hex digest for the given extraction inputs."""
    payload = json.dumps(
        {
            "markdown": _normalize_markdown(markdown),
            "name_hints": _normalize_hints(name_hints),
            "academic_year": int(academic_year),
            "version": version,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
