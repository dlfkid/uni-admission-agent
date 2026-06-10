"""Strategy-first candidate discovery for the full ingestion pipeline.

Wraps :func:`crawl_index` into a result the pipeline seam understands:
``{detail_url: authoritative_name}``.  Strategy-first, LLM-scout fallback —
``matched=False`` (unsupported / nothing crawlable / ANY exception) means the
caller proceeds exactly as before this feature existed.  Discovery may only
upgrade a crawl, never break one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from src.services.crawl_strategy.orchestrator import crawl_index
from src.services.crawl_strategy.types import CrawlRange

logger = logging.getLogger(__name__)


def resolve_crawl_range(limit: Optional[int], crawl_all: bool) -> CrawlRange:
    """Map limit/crawl_all surface params to a CrawlRange. Mutually exclusive."""
    if crawl_all and limit is not None:
        raise ValueError("limit and crawl_all are mutually exclusive")
    if crawl_all:
        return CrawlRange.all_()
    if limit is not None:
        return CrawlRange.of(limit)
    return CrawlRange.default()


@dataclass
class DiscoveryResult:
    """Outcome of strategy-first discovery over a programme-index URL."""

    matched: bool
    link_texts: Dict[str, str] = field(default_factory=dict)
    nameless_count: int = 0
    names_total: int = 0
    strategy_used: Optional[str] = None
    stopped_reason: str = ""
    pages_fetched: int = 0
    report_zip: Optional[str] = None


def discover_candidates(
    index_url: str,
    crawl_range: CrawlRange,
    *,
    server_fetch,
    client_fetch,
    api_fetch,
    report_out,
    timestamp: str,
) -> DiscoveryResult:
    """Run the crawl-strategy system; map its outcome onto the pipeline seam.

    ``matched=True`` only when at least one item carries a detail URL —
    items with a name but no URL cannot be detail-crawled and are counted
    in ``nameless_count`` (reported, never persisted as empty records).
    """
    try:
        outcome = crawl_index(
            index_url, crawl_range=crawl_range,
            server_fetch=server_fetch, client_fetch=client_fetch,
            api_fetch=api_fetch, report_out=report_out, timestamp=timestamp)
    except Exception:  # pylint: disable=broad-except
        logger.exception("strategy discovery failed for %s — falling back to scout",
                         index_url)
        return DiscoveryResult(matched=False)

    if outcome.status != "ok":
        return DiscoveryResult(matched=False, report_zip=outcome.report_zip,
                               stopped_reason=outcome.stopped_reason)

    link_texts = {i.detail_url: i.name_en for i in outcome.items if i.detail_url}
    nameless = sum(1 for i in outcome.items if not i.detail_url)
    return DiscoveryResult(
        matched=bool(link_texts),
        link_texts=link_texts,
        nameless_count=nameless,
        names_total=len(outcome.items),
        strategy_used=outcome.strategy_used,
        stopped_reason=outcome.stopped_reason,
        pages_fetched=outcome.pages_fetched,
    )


def discover_with_default_adapters(
    index_url: str, crawl_range: CrawlRange,
) -> DiscoveryResult:
    """Convenience wrapper wiring the real fetch adapters + default report dir."""
    # Imported here so importing discovery stays cheap for unit tests.
    from datetime import datetime, timezone  # pylint: disable=import-outside-toplevel
    from src.core.paths import get_data_dir  # pylint: disable=import-outside-toplevel
    from src.services.crawl_strategy import fetch_adapters  # pylint: disable=import-outside-toplevel

    return discover_candidates(
        index_url, crawl_range,
        server_fetch=fetch_adapters.server_fetch,
        client_fetch=fetch_adapters.client_fetch,
        api_fetch=fetch_adapters.api_fetch,
        report_out=str(get_data_dir() / "reports"),
        timestamp=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
    )
