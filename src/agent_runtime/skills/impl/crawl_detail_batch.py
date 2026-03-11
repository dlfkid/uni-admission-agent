"""Detail-batch crawl skill implementation."""

from __future__ import annotations

from src.core.async_utils import run_sync
from src.agent_runtime.skills.contracts import CrawlDetailBatchSkillInput
from src.services.crawler import crawl_selected_detail_urls_via_client


async def crawl_detail_batch_skill_handler_async(payload: CrawlDetailBatchSkillInput) -> dict:
    """Run client-driven detail batch crawl."""
    return await crawl_selected_detail_urls_via_client(
        index_url=payload.index_url,
        selected_urls=payload.selected_urls,
        univ_slug=payload.univ_slug,
        year=payload.year,
        batch_size=payload.batch_size,
        client_id=payload.client_id,
        strict_client=payload.strict_client,
        selected_link_texts=payload.selected_link_texts,
    )


def crawl_detail_batch_skill_handler(payload: CrawlDetailBatchSkillInput) -> dict:
    """Sync wrapper for detail-batch crawl execution."""
    return run_sync(
        lambda: crawl_detail_batch_skill_handler_async(payload),
        label="crawl_detail_batch_skill_handler()",
    )
