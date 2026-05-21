"""Persistent extraction cache for LLM cleaner outputs.

Avoids redundant LLM calls when the same markdown (with the same hints
and version) is processed more than once — across retries, restarts,
and repeat crawls of the same page.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Iterator, List, Optional, Union

from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, select


logger = logging.getLogger(__name__)


class ExtractionCacheEntry(SQLModel, table=True):
    """Single cached LLM cleaner output, keyed by content hash."""

    __tablename__ = "extraction_cache"

    cache_key: str = Field(primary_key=True, max_length=64)
    payload: str = Field(description="Serialized ParsedProgramData JSON")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExtractionCacheRepo:
    """Thin repository wrapping ExtractionCacheEntry CRUD.

    Accepts either a long-lived ``Session`` (typically in tests where the
    caller controls lifecycle) or an ``Engine`` (typical in production —
    a fresh session is opened and closed per operation, so the repo can
    safely outlive any single request).
    """

    def __init__(self, session_or_engine: Union[Session, Engine]) -> None:
        self._handle = session_or_engine

    @contextlib.contextmanager
    def _scoped_session(self) -> Iterator[Session]:
        if isinstance(self._handle, Session):
            yield self._handle
        else:
            with Session(self._handle) as session:
                yield session

    def get(self, cache_key: str) -> Optional["ParsedProgramData"]:  # noqa: F821
        # Import lazily to avoid a circular dependency with cleaner_agent.
        from src.agents.cleaner_agent import ParsedProgramData

        with self._scoped_session() as session:
            entry = session.get(ExtractionCacheEntry, cache_key)
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
        with self._scoped_session() as session:
            existing = session.get(ExtractionCacheEntry, cache_key)
            if existing is None:
                session.add(
                    ExtractionCacheEntry(cache_key=cache_key, payload=payload)
                )
            else:
                existing.payload = payload
                existing.created_at = datetime.now(timezone.utc)
            session.commit()


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
