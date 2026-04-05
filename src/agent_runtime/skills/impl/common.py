"""Common skill handlers that wrap existing core services."""

from __future__ import annotations

import logging

from src.agent_bridge.client_automation_bridge import ClientAutomationBridge
from src.agent_bridge.contracts import BrowserFetchInput

import threading
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM link-filter cache
# Keyed by (url, page_type_hint).  Entries expire after TTL seconds so that
# stale index pages are eventually re-analysed without penalising the common
# case where the agent mistakenly re-fetches the same index URL several times
# within a single task run.
# ---------------------------------------------------------------------------
_llm_filter_cache: dict[tuple[str, str], tuple[float, list, dict]] = {}
_llm_filter_lock = threading.Lock()
_LLM_FILTER_CACHE_TTL = 300  # seconds


def _get_cached_llm_filter(url: str, hint: str) -> tuple[list, dict] | None:
    key = (url, hint)
    with _llm_filter_lock:
        entry = _llm_filter_cache.get(key)
        if entry and (time.monotonic() - entry[0]) < _LLM_FILTER_CACHE_TTL:
            return entry[1], entry[2]
    return None


def _set_cached_llm_filter(url: str, hint: str, urls: list, texts: dict) -> None:
    key = (url, hint)
    with _llm_filter_lock:
        _llm_filter_cache[key] = (time.monotonic(), urls, texts)


from src.agent_runtime.skills.contracts import (
    BrowserAutomationSkillInput,
    PersistProgramsSkillInput,
    QueryDbSkillInput,
    ReviewPatchSkillInput,
    SelectDetailCandidatesSkillInput,
)
from src.services.crawler import (
    ingest_program_records_external,
    patch_program_snapshot,
    query_programs,
)


def select_detail_candidates_skill_handler(payload: SelectDetailCandidatesSkillInput) -> dict:
    """Select top-k detail URLs from analyzed link candidates."""
    selected_urls: list[str] = []
    for item in payload.links:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        selected_urls.append(url)
        if len(selected_urls) >= payload.top_k:
            break
    return {"selected_urls": selected_urls}


def persist_programs_skill_handler(payload: PersistProgramsSkillInput) -> dict:
    """Persist caller-structured program records using external-ingest path."""
    return ingest_program_records_external(
        univ_slug=payload.univ_slug,
        year=payload.year,
        programs=payload.programs,
        dry_run=payload.dry_run,
    )


def review_patch_skill_handler(payload: ReviewPatchSkillInput) -> dict:
    """Apply one review patch to a persisted program."""
    if not payload.patch:
        return {
            "updated": False,
            "program_id": payload.program_id,
            "summary": "empty patch",
        }

    updated_program = patch_program_snapshot(payload.program_id, payload.patch)
    if updated_program is None:
        return {
            "updated": False,
            "program_id": payload.program_id,
            "summary": "not found",
        }

    return {
        "updated": True,
        "program_id": payload.program_id,
        "summary": "updated 1 record",
    }


def query_db_skill_handler(payload: QueryDbSkillInput) -> dict:
    """Query stored programs for one university/year."""
    rows = query_programs(univ_slug=payload.univ_slug, year=payload.year)
    return {
        "programs": [row.model_dump(mode="json") for row in rows],
    }


def _html_to_markdown(html: str, url: str) -> str:
    """Convert raw HTML to lightweight text for LLM-friendly context.

    Uses a fast stdlib-based approach instead of crawl4ai which takes
    1-2 minutes on large (100-200K) HTML pages.
    """
    import re
    from html import unescape

    if not html:
        return html

    text = html
    # Remove script/style/noscript blocks
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Convert <br>, <hr> to newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<hr\s*/?>", "\n---\n", text, flags=re.IGNORECASE)
    # Convert block-level closing tags to newlines
    text = re.sub(r"</(?:p|div|tr|li|h[1-6]|section|article|header|footer|nav|main)>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities
    text = unescape(text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def browser_automation_skill_handler(
    payload: BrowserAutomationSkillInput,
    bridge: ClientAutomationBridge,
) -> dict:
    """Fetch browser payload from connected client runtime.

    HTML is converted to markdown before returning to keep the agent
    conversation context small enough for LLM API limits.
    """
    output = bridge.fetch_browser_payload(
        BrowserFetchInput(
            url=payload.url,
            page_type_hint=payload.page_type_hint,
            client_id=payload.client_id,
        )
    )
    result = output.model_dump(mode="json")

    # For index pages, replace the client-side heuristic selected_urls with
    # LLM-ranked candidates.  The client heuristic matches on URL keywords
    # (e.g. "/undergraduate") and returns links in DOM order — so navigation
    # links that appear early in the page fill the limit before real course
    # detail links are reached.  Running filter_links_by_llm on the raw HTML
    # gives the agent a clean, correctly-ranked candidate list.
    html = result.get("html_content") or ""
    if payload.page_type_hint == "index" and html:
        cached = _get_cached_llm_filter(payload.url, payload.page_type_hint)
        if cached is not None:
            cached_urls, cached_texts = cached
            result["selected_urls"] = cached_urls
            if cached_texts:
                result["selected_link_texts"] = cached_texts
            logger.info(
                "[BrowserSkill] LLM filter cache hit (%d links) for %s",
                len(cached_urls),
                payload.url,
            )
        else:
            try:
                from src.services.crawler import analyze_page
                analysis = analyze_page(payload.url, html, "index")
                llm_links = analysis.get("links") or []
                filtered_urls = [link["url"] for link in llm_links]
                filtered_texts = {
                    link["url"]: link["text"]
                    for link in llm_links
                    if link.get("text")
                }
                _set_cached_llm_filter(payload.url, payload.page_type_hint, filtered_urls, filtered_texts)
                if filtered_urls:
                    result["selected_urls"] = filtered_urls
                    result["selected_link_texts"] = filtered_texts
                    logger.info(
                        "[BrowserSkill] LLM filtered %d/%d candidate links for %s",
                        len(filtered_urls),
                        analysis.get("total_found", 0),
                        payload.url,
                    )
                else:
                    logger.info(
                        "[BrowserSkill] LLM found no detail links (total_found=%d) for %s",
                        analysis.get("total_found", 0),
                        payload.url,
                    )
            except Exception as exc:
                logger.warning("[BrowserSkill] LLM link filter failed, using heuristic: %s", exc)

    # Convert raw HTML to markdown to avoid context bloat
    if html:
        result["html_content"] = _html_to_markdown(html, payload.url)

    # For index pages with selected_urls, auto-fetch all detail pages in
    # parallel and extract structured program data via LLM pipeline.
    # The agent only needs to call persist_programs_skill once with the results.
    selected = result.get("selected_urls") or []
    if selected:
        # Cap detail pages to avoid timeout on large indexes.
        MAX_AUTO_EXTRACT = 10
        if len(selected) > MAX_AUTO_EXTRACT:
            logger.info(
                "[BrowserSkill] Capping detail pages from %d to %d",
                len(selected), MAX_AUTO_EXTRACT,
            )
            selected = selected[:MAX_AUTO_EXTRACT]
        link_texts = result.get("selected_link_texts") or {}
        extracted = _auto_fetch_and_extract(
            selected, link_texts, bridge, index_url=payload.url,
        )
        result["extracted_programs"] = extracted["programs"]
        result["html_content"] = (
            f"[Index page: {len(selected)} detail URLs found. "
            f"All pages fetched and parsed automatically. "
            f"{len(extracted['programs'])} programs extracted with full details. "
            f"Call persist_programs_skill ONCE with all programs below. "
            f"Do NOT call browser_automation_skill again.]"
        )
        logger.info(
            "[BrowserSkill] Auto-extracted %d/%d programs for %s",
            len(extracted["programs"]), len(selected), payload.url,
        )
    elif payload.page_type_hint == "index":
        md = result.get("html_content") or ""
        MAX_INDEX_HTML = 15_000
        if len(md) > MAX_INDEX_HTML:
            result["html_content"] = (
                f"[Index page HTML trimmed to {MAX_INDEX_HTML} chars. "
                f"Use analyze_page_skill with url and this html_content to extract detail links.]\n\n"
                + md[:MAX_INDEX_HTML]
                + "\n...(truncated)"
            )

    return result


def _strip_boilerplate(md: str) -> str:
    """Remove common university page boilerplate from markdown.

    Strips navigation menus, breadcrumbs, footers, and repeated header
    blocks that appear on every page. This typically reduces page size
    by 30-50%, often avoiding the need for multi-chunk LLM parsing.
    """
    import re

    lines = md.split("\n")
    filtered: list[str] = []

    # Common boilerplate patterns (case-insensitive line matching)
    _SKIP_PATTERNS = re.compile(
        r"^(?:"
        r"Skip to (?:main )?content|"
        r"Toggle Navigation|"
        r"Expand/collapse submenu|"
        r"Show/hide site search|"
        r"Submit search|"
        r"Breadcrumb|"
        r"Subsite (?:menu|mobile menu)|"
        r"User account menu|"
        r"CMS login|"
        r"Terms & conditions|"
        r"Privacy & cookies|"
        r"Complaints procedure|"
        r"Modern slavery|"
        r"Website accessibility|"
        r"Freedom of information|"
        r"Data protection|"
        r"Digital Sustainability|"
        r"MyEd login|"
        r"The University of Edinburgh is a charitable body|"
        r"Unless explicitly stated otherwise|"
        r"copyright ©"
        r")\s*$",
        re.IGNORECASE,
    )

    # Footer markers — skip everything after these
    _FOOTER_MARKERS = re.compile(
        r"^(?:On this page|Related degree programmes|You may also be interested in)\s*$",
        re.IGNORECASE,
    )

    for line in lines:
        stripped = line.strip()

        # Skip empty lines in sequences
        if not stripped:
            if filtered and not filtered[-1].strip():
                continue
            filtered.append(line)
            continue

        # Stop at footer sections
        if _FOOTER_MARKERS.match(stripped):
            break

        # Skip known boilerplate lines
        if _SKIP_PATTERNS.match(stripped):
            continue

        # Skip navigation-like lines (single words/short phrases)
        if stripped in (
            "Home", "Study", "Global", "Visit", "Research", "News",
            "About", "Alumni", "Local", "Staff", "Students",
            "Schools & departments", "MyEd", "Degree finder",
            "Search degree programmes", "Undergraduate degree programmes",
            "Postgraduate taught programmes", "Postgraduate research programmes",
            "A to Z of degree programmes", "Degree programmes by subject",
            "Undergraduate degree programmes by subject",
            "Postgraduate degree programmes by subject",
            "Postgraduate taught degree programmes A to Z",
            "Postgraduate research degree programmes A to Z",
            "Search", "Filter", "Refine results", "Close filters X",
            "Apply now", "Apply",
        ):
            continue

        filtered.append(line)

    result = "\n".join(filtered).strip()
    # Collapse excessive whitespace
    result = re.sub(r"\n{4,}", "\n\n\n", result)
    return result


def _strip_html_boilerplate(html: str) -> str:
    """Remove nav/header/footer HTML elements to reduce page size for LLM.

    Targets <nav>, <header>, <footer> tags and common id/class patterns.
    This is critical for sites like UCL where markdown conversion fails
    and the raw HTML (60K+) gets split into 4 LLM chunks.
    """
    import re

    if not html:
        return html

    # Remove <nav>, <header>, <footer> blocks entirely
    html = re.sub(
        r"<(?:nav|header|footer)[\s>].*?</(?:nav|header|footer)>",
        "", html, flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove elements with navigation-related class/id
    html = re.sub(
        r'<(?:div|section|aside|ul)[^>]*(?:class|id)\s*=\s*"[^"]*'
        r'(?:nav|breadcrumb|sidebar|footer|cookie|skip-link)[^"]*"[^>]*>.*?'
        r'</(?:div|section|aside|ul)>',
        "", html, flags=re.DOTALL | re.IGNORECASE,
    )
    # Collapse whitespace
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html


def _auto_fetch_and_extract(
    urls: list[str],
    link_texts: dict[str, str],
    bridge: ClientAutomationBridge,
    max_workers: int = 5,
    univ_slug: str = "",
    index_url: str = "",
) -> dict:
    """Fetch detail pages in parallel, then extract using schema or LLM.

    Schema-based extraction flow:
    1. Check for existing schema on disk
    2. If valid → CSS selector extraction for all pages (fast)
    3. If no schema → LLM extract page 1, learn schema, then CSS for rest
    4. Fallback to LLM for missing fields (≤3: field-level, >3: full-page)
    """
    import concurrent.futures
    from typing import Any
    from src.agents.factory import create_router
    from src.agents.cleaner_agent import LLMCleanerAgent
    from src.models.scraper_models import CrawlPageResult
    from src.scrapers.page_processor import extract_program_data_from_page
    from src.scrapers.schema_extractor import (
        SchemaManager, SchemaLearner, SelectorExtractor,
        FallbackHandler, derive_page_pattern,
    )

    # Step 1: Parallel fetch all detail pages (unchanged)
    def _fetch_one(url: str) -> CrawlPageResult | None:
        try:
            output = bridge.fetch_browser_payload(
                BrowserFetchInput(url=url, page_type_hint="detail")
            )
            raw_html = output.html_content or ""
            md = _html_to_markdown(raw_html, url) if raw_html else ""
            return CrawlPageResult(
                url=url, html=raw_html, markdown=md,
                char_count=len(md), links=[],
            )
        except Exception as exc:
            logger.warning("[AutoExtract] Failed to fetch %s: %s", url, exc)
            return None

    pages: list[CrawlPageResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, url): url for url in urls}
        for future in concurrent.futures.as_completed(futures):
            page = future.result()
            if page and (page.html or page.markdown):
                pages.append(page)

    logger.info("[AutoExtract] Fetched %d/%d detail pages", len(pages), len(urls))
    if not pages:
        return {"programs": []}

    # Step 2: Schema-aware extraction
    page_pattern = derive_page_pattern(index_url) if index_url else "default"
    mgr = SchemaManager()
    schema = mgr.load(univ_slug, page_pattern) if univ_slug else None

    # Score tracking for deprecation
    sum_scores = 0.0
    page_count = 0

    def _extract_with_llm(page: CrawlPageResult) -> tuple[dict | None, dict[str, Any]]:
        """Full LLM extraction — returns (program_data, raw_extracted_fields)."""
        router = create_router()
        cleaner = LLMCleanerAgent(router=router)
        anchor_text = link_texts.get(page.url)
        trimmed_md = _strip_boilerplate(page.markdown) if page.markdown else ""
        trimmed_html = _strip_html_boilerplate(page.html) if page.html else ""
        trimmed_page = CrawlPageResult(
            url=page.url, html=trimmed_html, markdown=trimmed_md,
            char_count=len(trimmed_md), links=page.links,
        )
        try:
            program_data, error = extract_program_data_from_page(
                page=trimmed_page, cleaner=cleaner,
                univ_slug=univ_slug or "", year=0,
                current_depth=0, from_browser=True,
                selected_anchor_text=anchor_text,
            )
            if program_data:
                program_data.pop("academic_year", None)
                program_data["source_url"] = page.url
                logger.info(
                    "[AutoExtract] LLM extracted: %s (%d fields)",
                    program_data.get("name_en", "?"),
                    len([v for v in program_data.values() if v]),
                )
                return program_data, program_data
            else:
                logger.warning("[AutoExtract] LLM no data from %s: %s", page.url, error)
                return None, {}
        except Exception as exc:
            logger.warning("[AutoExtract] LLM failed %s: %s", page.url, exc)
            return None, {}

    def _extract_with_schema(page: CrawlPageResult, schema) -> dict | None:
        """Schema-based extraction with fallback."""
        nonlocal sum_scores, page_count

        result = SelectorExtractor.extract(page.html, schema)
        score = SelectorExtractor.compute_score(result, schema)
        sum_scores += score
        page_count += 1

        decision = FallbackHandler.decide(result, total_fields=schema.total_fields)

        if decision == "field":
            missing = SelectorExtractor.missing_fields(result)
            router = create_router()
            supplement = FallbackHandler.field_fallback(page.html, missing, router)
            filled = 0
            for field_name in missing:
                if field_name in supplement and supplement[field_name] is not None:
                    result[field_name] = supplement[field_name]
                    filled += 1
            logger.info(
                "[AutoExtract] Schema + field fallback for %s (score=%.2f, filled %d/%d)",
                page.url, score, filled, len(missing),
            )
        elif decision == "full":
            logger.info("[AutoExtract] Schema score too low (%.2f), full LLM for %s", score, page.url)
            program_data, _ = _extract_with_llm(page)
            return program_data
        else:
            logger.info("[AutoExtract] Schema hit all fields for %s (score=%.2f)", page.url, score)

        # Build program_data dict from selector results
        anchor_text = link_texts.get(page.url)
        program_data: dict[str, Any] = {"source_url": page.url}
        if result.get("name_en"):
            program_data["name_en"] = result["name_en"]
        else:
            from src.scrapers.helpers import extract_program_name
            name = extract_program_name(page.markdown) if page.markdown else ""
            if not name and anchor_text:
                name = anchor_text
            program_data["name_en"] = name or ""

        for key in ("faculty", "tuition_amount", "currency", "study_options", "deadlines", "requirements"):
            if result.get(key) is not None:
                program_data[key] = result[key]

        program_data["extra_metadata"] = {
            "source_url": page.url, "from_browser": True, "schema_score": score,
        }
        return program_data if program_data.get("name_en") else None

    programs: list[dict] = []

    # --- Page 1: Learn or validate schema ---
    first_page = pages[0]
    remaining_pages = pages[1:]

    if schema:
        # Validate existing schema on first page
        result = SelectorExtractor.extract(first_page.html, schema)
        score = SelectorExtractor.compute_score(result, schema)
        threshold = schema.baseline_score * 0.8
        if score < threshold:
            logger.info(
                "[AutoExtract] Schema validation failed (%.2f < %.2f), rebuilding",
                score, threshold,
            )
            mgr.deprecate(univ_slug, page_pattern)
            schema = None
        else:
            logger.info("[AutoExtract] Schema validated (%.2f >= %.2f)", score, threshold)

    if not schema:
        # Learn from first page via LLM
        program_data, extracted_fields = _extract_with_llm(first_page)
        if program_data:
            programs.append(program_data)
            # Try to learn schema for remaining pages
            if univ_slug and extracted_fields and first_page.html:
                try:
                    router = create_router()
                    schema = SchemaLearner.learn(
                        html=first_page.html,
                        extracted_data=extracted_fields,
                        router=router,
                        univ_slug=univ_slug,
                        page_pattern=page_pattern,
                        source_url=first_page.url,
                    )
                    if schema:
                        mgr.save(schema)
                except Exception as exc:
                    logger.warning("[AutoExtract] Schema learning failed: %s", exc)
                    schema = None
    else:
        # Schema validated — use it for first page
        program_data = _extract_with_schema(first_page, schema)
        if program_data:
            programs.append(program_data)

    # --- Pages 2-N ---
    if remaining_pages:
        if schema:
            # Parallel schema extraction
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_extract_with_schema, page, schema): page
                    for page in remaining_pages
                }
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result:
                        programs.append(result)

            # Check for schema deprecation
            if page_count >= 3 and (sum_scores / page_count) < schema.baseline_score * 0.8:
                logger.warning(
                    "[AutoExtract] Schema degraded (avg=%.2f < baseline=%.2f×0.8), deprecating",
                    sum_scores / page_count, schema.baseline_score,
                )
                mgr.deprecate(univ_slug, page_pattern)
        else:
            # No schema — fall back to full LLM for all remaining
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_extract_with_llm, page): page
                    for page in remaining_pages
                }
                for future in concurrent.futures.as_completed(futures):
                    program_data, _ = future.result()
                    if program_data:
                        programs.append(program_data)

    logger.info("[AutoExtract] Total programs: %d/%d pages", len(programs), len(pages))
    return {"programs": programs}
