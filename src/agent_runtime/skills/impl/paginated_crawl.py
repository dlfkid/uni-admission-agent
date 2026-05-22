"""Paginated crawl skill handler.

Orchestrates multi-page crawling by:
1. Fetching page 1 HTML via the browser bridge
2. Detecting pagination (url_param / spa_button / single_page)
3. Looping through pages, extracting programs, running quality gates
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from src.agent_bridge.client_automation_bridge import ClientAutomationBridge
from src.agent_bridge.contracts import BrowserFetchInput
from src.agent_runtime.skills.contracts import PaginatedCrawlSkillInput
from src.agent_runtime.skills.impl.pagination_detector import detect_pagination
from src.agent_runtime.skills.impl.quality_circuit_breaker import quality_check
from src.agent_runtime.skills.impl.common import (
    _auto_fetch_and_extract,
    _get_cached_llm_filter,
    _set_cached_llm_filter,
)
from src.scrapers.pagination_signals import (
    should_stop_for_decreasing_yield,
    urls_diverged,
)
from src.storage.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_stop_result(
    *,
    status: str,
    stop_reason: str,
    pagination: Any,
    pages_processed: int,
    all_programs: list[dict[str, Any]],
    quality_scores: list[dict[str, Any]],
    summary: str,
) -> dict[str, Any]:
    """Compose a stop-mid-loop result dict with consistent fields."""
    return {
        "status": status,
        "stop_reason": stop_reason,
        "pagination_type": getattr(pagination, "pagination_type", "single_page"),
        "total_pages_detected": getattr(pagination, "total_pages", None),
        "pages_processed": pages_processed,
        "programs": all_programs,
        "extracted_programs": all_programs,
        "total_programs": len(all_programs),
        "quality_scores": quality_scores,
        "warning": None,
        "summary": summary,
    }


def _write_pagination_audit(
    *,
    univ_slug: str,
    year: int,
    index_url: str,
    pages_processed: int,
    all_programs: list[dict[str, Any]],
    stop_reason: str,
) -> None:
    """Persist a single extraction_audit row recording WHY the paginated
    crawl stopped. Failures here are logged but never block the skill —
    diagnostic data is best-effort.
    """
    try:
        db = DatabaseManager()
        db.record_extraction_audit(
            university_slug=univ_slug,
            academic_year=int(year),
            index_url=str(index_url),
            # Funnel counts: we don't have raw_link_count etc. in the
            # pagination path (it's index→detail not link-filter), so we
            # report what we know: pages processed maps to candidates,
            # successfully-extracted count maps to extracted.
            raw_link_count=int(pages_processed),
            llm_filtered_count=int(pages_processed),
            candidate_count=int(pages_processed),
            extracted_count=int(len(all_programs)),
            quarantined_count=0,
            pagination_stop_reason=stop_reason,
        )
    except Exception:  # pylint: disable=broad-except
        logger.exception(
            "[PaginatedCrawl] Failed to record audit (stop_reason=%s)",
            stop_reason,
        )


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

def paginated_crawl_skill_handler(
    payload: PaginatedCrawlSkillInput,
    bridge: ClientAutomationBridge,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Orchestrate paginated crawl with quality gates.

    Args:
        payload: Skill input (url, univ_slug, year, max_pages, batch_quality_size).
        bridge: Browser bridge used to fetch HTML.
        event_sink: Optional callable to receive progress/quality events.

    Returns:
        Dict matching PaginatedCrawlSkillOutput schema.
    """

    def emit(event: dict[str, Any]) -> None:
        if event_sink is not None:
            try:
                event_sink(event)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("[PaginatedCrawl] event_sink error: %s", exc)

    url = payload.url
    univ_slug = payload.univ_slug
    year = payload.year
    max_pages = payload.max_pages
    batch_quality_size = payload.batch_quality_size

    # ------------------------------------------------------------------
    # Step 1: Fetch page 1 HTML
    # ------------------------------------------------------------------
    logger.info("[PaginatedCrawl] Fetching page 1: %s", url)
    fetch_output = bridge.fetch_browser_payload(
        BrowserFetchInput(url=url, page_type_hint="index", client_id=None)
    )
    page1_html = fetch_output.html_content or ""

    # ------------------------------------------------------------------
    # Step 2: Detect pagination
    # ------------------------------------------------------------------
    pagination = detect_pagination(page1_html, url)
    logger.info(
        "[PaginatedCrawl] Detected pagination_type=%s total_pages=%s confidence=%.2f",
        pagination.pagination_type,
        pagination.total_pages,
        pagination.confidence,
    )

    emit({
        "type": "pagination_detected",
        "pagination_type": pagination.pagination_type,
        "total_pages": pagination.total_pages,
    })

    # ------------------------------------------------------------------
    # Step 3: Handle SPA button — process page 1 but mark unsupported
    # ------------------------------------------------------------------
    is_spa = pagination.pagination_type == "spa_button"

    # ------------------------------------------------------------------
    # Step 4: Build the list of page URLs to iterate
    # ------------------------------------------------------------------
    if pagination.pagination_type == "url_param":
        page_urls = pagination.page_urls[:max_pages]
    else:
        # single_page OR spa_button — only page 1
        page_urls = [url]

    total_pages = len(page_urls)

    # ------------------------------------------------------------------
    # Step 5: Page loop
    # ------------------------------------------------------------------
    all_programs: list[dict[str, Any]] = []
    quality_scores: list[dict[str, Any]] = []
    programs_since_last_check = 0
    batch_index = 0
    page_yield_history: list[int] = []
    index_url_for_pattern = url  # page 1 URL anchors the expected pattern

    for page_idx, page_url in enumerate(page_urls):
        # Pre-extraction stop signal: URL pattern divergence.
        # Fires BEFORE the LLM call so a drifted page costs zero tokens.
        if page_idx > 0 and urls_diverged(index_url_for_pattern, page_url):
            logger.warning(
                "[PaginatedCrawl] URL drift at page %d: %s no longer matches "
                "index pattern of %s — stopping",
                page_idx + 1, page_url, index_url_for_pattern,
            )
            emit({
                "type": "pagination_stopped",
                "reason": "url_drift",
                "page": page_idx + 1,
                "drifted_url": page_url,
            })
            _write_pagination_audit(
                univ_slug=univ_slug, year=year, index_url=url,
                pages_processed=page_idx,
                all_programs=all_programs, stop_reason="url_drift",
            )
            return _build_stop_result(
                status="url_drift",
                stop_reason="url_drift",
                pagination=pagination,
                pages_processed=page_idx,  # this page wasn't processed
                all_programs=all_programs,
                quality_scores=quality_scores,
                summary=(
                    f"URL drift at page {page_idx + 1}: {page_url!r} "
                    f"does not match index pattern"
                ),
            )

        # Fetch HTML (reuse page 1 HTML for index 0)
        if page_idx == 0:
            html_content = page1_html
        else:
            logger.info("[PaginatedCrawl] Fetching page %d: %s", page_idx + 1, page_url)
            try:
                fetch_out = bridge.fetch_browser_payload(
                    BrowserFetchInput(url=page_url, page_type_hint="index", client_id=None)
                )
                html_content = fetch_out.html_content or ""
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "[PaginatedCrawl] Failed to fetch page %d (%s): %s",
                    page_idx + 1, page_url, exc,
                )
                html_content = ""

        # Extract programs from this index page
        page_programs = _process_single_index_page(
            html_content, page_url, bridge, univ_slug, year
        )
        logger.info(
            "[PaginatedCrawl] Page %d/%d extracted %d programs",
            page_idx + 1, total_pages, len(page_programs),
        )

        all_programs.extend(page_programs)
        programs_since_last_check += len(page_programs)
        page_yield_history.append(len(page_programs))

        # Post-extraction stop signal: decreasing yield trend. Keep the
        # data we already extracted (it's still valid) but don't fetch
        # further pages.
        if should_stop_for_decreasing_yield(page_yield_history):
            logger.warning(
                "[PaginatedCrawl] Decreasing yield trend at page %d "
                "(history=%s) — stopping",
                page_idx + 1, page_yield_history,
            )
            emit({
                "type": "pagination_stopped",
                "reason": "decreasing_yield",
                "page": page_idx + 1,
                "yield_history": page_yield_history,
            })
            _write_pagination_audit(
                univ_slug=univ_slug, year=year, index_url=url,
                pages_processed=page_idx + 1,
                all_programs=all_programs, stop_reason="decreasing_yield",
            )
            return _build_stop_result(
                status="decreasing_yield",
                stop_reason="decreasing_yield",
                pagination=pagination,
                pages_processed=page_idx + 1,
                all_programs=all_programs,
                quality_scores=quality_scores,
                summary=(
                    f"Yield collapsed at page {page_idx + 1}: history={page_yield_history}"
                ),
            )

        emit({
            "type": "pagination_progress",
            "page": page_idx + 1,
            "total_pages": total_pages,
            "programs_so_far": len(all_programs),
        })

        # Quality gate: check every batch_quality_size programs
        if programs_since_last_check >= batch_quality_size:
            batch_to_check = all_programs[-programs_since_last_check:]
            qr = quality_check(
                batch_to_check,
                page_index=page_idx + 1,
                total_program_count=len(all_programs),
            )
            quality_scores.append({
                "batch_index": batch_index,
                "verdict": qr.verdict,
                "heuristic_score": qr.heuristic_score,
                "reason": qr.reason,
            })
            batch_index += 1
            programs_since_last_check = 0

            if qr.verdict == "pass":
                emit({
                    "type": "quality_check_passed",
                    "batch_index": batch_index - 1,
                    "heuristic_score": qr.heuristic_score,
                })
            else:
                emit({
                    "type": "quality_check_failed",
                    "batch_index": batch_index - 1,
                    "reason": qr.reason,
                })
                logger.warning(
                    "[PaginatedCrawl] Quality check FAILED at page %d: %s",
                    page_idx + 1, qr.reason,
                )
                _write_pagination_audit(
                    univ_slug=univ_slug, year=year, index_url=url,
                    pages_processed=page_idx + 1,
                    all_programs=all_programs, stop_reason="quality_failed",
                )
                return {
                    "status": "quality_failed",
                    "stop_reason": "quality_failed",
                    "pagination_type": pagination.pagination_type,
                    "total_pages_detected": pagination.total_pages,
                    "pages_processed": page_idx + 1,
                    "programs": all_programs,
                    "extracted_programs": all_programs,
                    "total_programs": len(all_programs),
                    "quality_scores": quality_scores,
                    "warning": None,
                    "summary": (
                        f"Quality gate failed at page {page_idx + 1}: {qr.reason}"
                    ),
                }

    # ------------------------------------------------------------------
    # Step 6: Final quality check on remaining unchecked programs
    # Only run if there are enough remaining programs for a meaningful check
    # (avoids false negatives from the single-item duplicate-ratio heuristic).
    # ------------------------------------------------------------------
    if programs_since_last_check >= batch_quality_size:
        batch_to_check = all_programs[-programs_since_last_check:]
        qr = quality_check(
            batch_to_check,
            page_index=total_pages,
            total_program_count=len(all_programs),
        )
        quality_scores.append({
            "batch_index": batch_index,
            "verdict": qr.verdict,
            "heuristic_score": qr.heuristic_score,
            "reason": qr.reason,
        })
        batch_index += 1

        if qr.verdict == "pass":
            emit({
                "type": "quality_check_passed",
                "batch_index": batch_index - 1,
                "heuristic_score": qr.heuristic_score,
            })
        else:
            emit({
                "type": "quality_check_failed",
                "batch_index": batch_index - 1,
                "reason": qr.reason,
            })
            logger.warning("[PaginatedCrawl] Final quality check FAILED: %s", qr.reason)
            _write_pagination_audit(
                univ_slug=univ_slug, year=year, index_url=url,
                pages_processed=total_pages,
                all_programs=all_programs, stop_reason="quality_failed",
            )
            return {
                "status": "quality_failed",
                "stop_reason": "quality_failed",
                "pagination_type": pagination.pagination_type,
                "total_pages_detected": pagination.total_pages,
                "pages_processed": total_pages,
                "programs": all_programs,
                "extracted_programs": all_programs,
                "total_programs": len(all_programs),
                "quality_scores": quality_scores,
                "warning": None,
                "summary": f"Final quality gate failed: {qr.reason}",
            }

    # ------------------------------------------------------------------
    # Step 7: Build final result
    # ------------------------------------------------------------------
    status = "pagination_not_supported" if is_spa else "done"
    warning = (
        "SPA button pagination detected; only page 1 was processed."
        if is_spa
        else None
    )

    # Distinguish "ran to the hard cap" from "naturally consumed all detected
    # pages" — both succeed, but the diagnostic story differs.
    if is_spa:
        stop_reason = "pagination_not_supported"
    elif (
        pagination.total_pages is not None
        and total_pages >= max_pages
        and pagination.total_pages > max_pages
    ):
        stop_reason = "max_pages"
    else:
        stop_reason = "exhausted"

    logger.info(
        "[PaginatedCrawl] Completed. status=%s stop_reason=%s pages=%d programs=%d",
        status, stop_reason, total_pages, len(all_programs),
    )

    _write_pagination_audit(
        univ_slug=univ_slug, year=year, index_url=url,
        pages_processed=total_pages,
        all_programs=all_programs, stop_reason=stop_reason,
    )

    return {
        "status": status,
        "stop_reason": stop_reason,
        "pagination_type": pagination.pagination_type,
        "total_pages_detected": pagination.total_pages,
        "pages_processed": total_pages,
        "programs": all_programs,
        "extracted_programs": all_programs,
        "total_programs": len(all_programs),
        "quality_scores": quality_scores,
        "warning": warning,
        "summary": (
            f"Processed {total_pages} page(s), extracted {len(all_programs)} programs."
        ),
    }


# ---------------------------------------------------------------------------
# Helper: extract programs from one index page
# ---------------------------------------------------------------------------

def _process_single_index_page(
    html: str,
    url: str,
    bridge: ClientAutomationBridge,
    univ_slug: str,
    year: int,
) -> list[dict[str, Any]]:
    """Extract programs from one index page HTML.

    1. Check LLM filter cache for pre-computed detail URLs.
    2. If not cached, call analyze_page to LLM-filter link candidates.
    3. Cache results.
    4. Fetch and extract detail pages via _auto_fetch_and_extract.

    Args:
        html: Raw HTML of the index page.
        url: Canonical URL of the index page.
        bridge: Browser bridge for detail page fetching.
        univ_slug: University slug for schema management.
        year: Academic year.

    Returns:
        List of extracted program dicts.
    """
    if not html:
        logger.warning("[PaginatedCrawl] Empty HTML for %s, skipping", url)
        return []

    hint = "index"

    # Step 1: Check cache
    cached = _get_cached_llm_filter(url, hint)
    if cached is not None:
        detail_urls, link_texts = cached
        logger.info(
            "[PaginatedCrawl] LLM filter cache hit (%d links) for %s",
            len(detail_urls), url,
        )
    else:
        # Step 2: Analyze page to get LLM-filtered detail links
        try:
            from src.services.crawler import analyze_page
            analysis = analyze_page(url, html, hint)
            links = analysis.get("links") or []
            detail_urls = [link["url"] for link in links]
            link_texts = {
                link["url"]: link["text"]
                for link in links
                if link.get("text")
            }
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[PaginatedCrawl] analyze_page failed for %s: %s", url, exc)
            detail_urls = []
            link_texts = {}

        # Step 3: Cache
        _set_cached_llm_filter(url, hint, detail_urls, link_texts)
        logger.info(
            "[PaginatedCrawl] LLM filtered %d detail links for %s",
            len(detail_urls), url,
        )

    if not detail_urls:
        logger.info("[PaginatedCrawl] No detail links found for %s", url)
        return []

    # Step 4: Fetch and extract detail pages
    result = _auto_fetch_and_extract(
        detail_urls,
        link_texts,
        bridge,
        index_url=url,
        univ_slug=univ_slug,
        year=year,
    )
    return result.get("programs") or []
