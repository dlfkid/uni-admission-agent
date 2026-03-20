"""Common skill handlers that wrap existing core services."""

from __future__ import annotations

import logging

from src.agent_bridge.client_automation_bridge import ClientAutomationBridge
from src.agent_bridge.contracts import BrowserFetchInput

logger = logging.getLogger(__name__)
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
    """Convert raw HTML to markdown for LLM-friendly context."""
    try:
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        md_obj = DefaultMarkdownGenerator().generate_markdown(
            input_html=html, base_url=url,
        )
        if md_obj and hasattr(md_obj, "raw_markdown"):
            result = str(md_obj.raw_markdown or "").strip()
            if result:
                return result
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("HTML→markdown conversion failed, returning raw: %s", exc)
    return html


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

    # Convert raw HTML to markdown to avoid context bloat
    html = result.get("html_content") or ""
    if html:
        result["html_content"] = _html_to_markdown(html, payload.url)

    return result
