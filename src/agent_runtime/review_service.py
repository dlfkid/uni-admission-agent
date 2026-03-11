"""Low-confidence onhold review confirmation helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


def _normalize_onhold_rows(onhold_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for pos, raw in enumerate(list(onhold_items or []), start=1):
        row = dict(raw or {})
        try:
            index = int(row.get("index") or pos)
        except (TypeError, ValueError):
            index = pos
        if index <= 0:
            index = pos
        normalized.append(
            {
                "index": index,
                "item_id": str(row.get("item_id") or f"hold-{index}"),
                "source_url": str(row.get("source_url") or row.get("url") or "").strip(),
                "program_name_candidate": (
                    str(row.get("program_name_candidate")).strip()
                    if row.get("program_name_candidate") is not None
                    else None
                ),
                "confidence": float(row.get("confidence") or 0.0),
                "hold_reason": str(row.get("hold_reason") or "low_confidence"),
            }
        )
    normalized.sort(key=lambda item: int(item.get("index") or 0))
    return normalized


def _split_selected_onhold_items(
    *,
    onhold_items: list[dict[str, Any]],
    selected_indices: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
    items = _normalize_onhold_rows(onhold_items)
    index_map: dict[int, dict[str, Any]] = {}
    for item in items:
        idx = int(item.get("index") or 0)
        if idx > 0 and idx not in index_map:
            index_map[idx] = item

    selected_set: set[int] = set()
    invalid_indices: list[int] = []

    for raw_index in list(selected_indices or []):
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if index <= 0 or index not in index_map:
            if index not in invalid_indices:
                invalid_indices.append(index)
            continue
        selected_set.add(index)

    selected_items = [index_map[idx] for idx in sorted(selected_set)]
    discarded_items = [item for idx, item in sorted(index_map.items()) if idx not in selected_set]
    return selected_items, discarded_items, invalid_indices


async def run_agent_review_confirmation(
    *,
    task_payload: dict[str, Any],
    onhold_items: list[dict[str, Any]],
    selected_indices: list[int],
    crawl_executor: Callable[..., Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Apply selected onhold indices and default-discard unselected items."""
    selected_items, discarded_items, invalid_indices = _split_selected_onhold_items(
        onhold_items=list(onhold_items or []),
        selected_indices=list(selected_indices or []),
    )

    applied_result: dict[str, Any] = {}
    if selected_items:
        selected_urls = [
            str(item.get("source_url") or "").strip()
            for item in selected_items
            if str(item.get("source_url") or "").strip()
        ]
        selected_link_texts = {
            str(item.get("source_url") or "").strip(): str(item.get("program_name_candidate") or "").strip()
            for item in selected_items
            if str(item.get("source_url") or "").strip() and str(item.get("program_name_candidate") or "").strip()
        }

        if selected_urls:
            if crawl_executor is None:
                from src.services import crawler as crawler_service

                crawl_executor = crawler_service.crawl_url
            crawl_result = await crawl_executor(
                url=str((task_payload or {}).get("url") or "").strip(),
                univ_slug=str((task_payload or {}).get("univ_slug") or "").strip().lower(),
                year=int((task_payload or {}).get("year") or 0),
                page_type_hint="index",
                selected_urls=selected_urls,
                selected_link_texts=selected_link_texts,
                browser_provider=str((task_payload or {}).get("browser_provider") or "auto"),
                strict_client=bool((task_payload or {}).get("strict_client")),
                candidate_taxonomy_filter_enabled=False,
            )
            applied_result = crawl_result.model_dump(mode="json")

    return {
        "total_onhold": len(_normalize_onhold_rows(onhold_items)),
        "selected_count": len(selected_items),
        "discarded_count": len(discarded_items),
        "invalid_indices": invalid_indices,
        "applied_items": selected_items,
        "discarded_items": discarded_items,
        "applied_result": applied_result,
    }
