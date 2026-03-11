"""Detail-batch crawl skill implementation."""

from __future__ import annotations

import asyncio

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
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(crawl_detail_batch_skill_handler_async(payload))
    raise RuntimeError(
        "crawl_detail_batch_skill_handler() cannot run inside an active event loop; "
        "use crawl_detail_batch_skill_handler_async() instead."
    )
