"""Persistent cache for strategies learned from successful LLM-fallback crawls.

After the LLM agent loop successfully extracts programs for an unregistered
domain, we record the effective fetch mode so the next run can use the faster
``crawl_url`` pipeline path instead of the multi-roundtrip agent loop.

Cache file lives at ``<data_dir>/strategy_cache.json``.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_CACHE_FILENAME = "strategy_cache.json"


def _cache_path() -> Path:
    from src.core.paths import get_data_dir  # imported here to keep module cheap
    return get_data_dir() / _CACHE_FILENAME


def _domain_of(url: str) -> str:
    return urlsplit(str(url or "").strip()).netloc.lower()


def load_cache() -> dict:
    """Return the full cache dict (empty dict on missing/corrupt file)."""
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("strategy_cache.json unreadable; treating as empty", exc_info=True)
        return {}


def lookup(url: str) -> Optional[dict]:
    """Return the cached strategy entry for *url*'s domain, or None."""
    domain = _domain_of(url)
    if not domain:
        return None
    return load_cache().get(domain)


def record_success(url: str, fetch_mode: str = "server") -> None:
    """Record that a crawl of *url*'s domain succeeded with *fetch_mode*.

    Called after `run_agent_crawl` returns positive imported_count.
    Thread-safe; writes atomically via a temp file.
    """
    domain = _domain_of(url)
    if not domain:
        return
    with _lock:
        cache = load_cache()
        existing = cache.get(domain, {})
        cache[domain] = {
            **existing,
            "fetch_mode": fetch_mode,
            "last_success": datetime.now(timezone.utc).isoformat(),
            "success_count": int(existing.get("success_count", 0)) + 1,
        }
        _write_cache(cache)
    logger.info("strategy_cache: recorded success for domain=%s fetch_mode=%s", domain, fetch_mode)


def record_detail_pattern(url: str, pattern: str) -> None:
    """Record a learned page-layout pattern for *url*'s domain.

    E.g. ``pattern="thin_page_supplement"`` after the runtime detector
    found stub/hub detail pages on this domain and supplement expansion
    recovered real fields. Future crawls of the same domain can read this
    via :func:`lookup` and treat the layout as known rather than
    re-discovering it. Merges into any existing entry for the domain.
    """
    domain = _domain_of(url)
    if not domain or not pattern:
        return
    with _lock:
        cache = load_cache()
        existing = cache.get(domain, {})
        cache[domain] = {
            **existing,
            "detail_pattern": pattern,
            "detail_pattern_recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_cache(cache)
    logger.info(
        "strategy_cache: recorded detail_pattern=%s for domain=%s", pattern, domain
    )


def _write_cache(data: dict) -> None:
    path = _cache_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.warning("Failed to write strategy_cache.json", exc_info=True)
