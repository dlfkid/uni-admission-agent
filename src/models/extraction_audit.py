"""ExtractionAudit: per-crawl funnel record for index→detail extraction.

Captures how many links were on the source index page, how many survived
filtering, and how many ended up as committed / quarantined programs.
Without this, "I crawled HKU and only got 5 programs" is invisible noise;
with it, the user can see exactly where in the funnel programs were lost.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class ExtractionAuditLink(SQLModel, table=True):
    """One link that was dropped at some funnel stage during an audit.

    Stored separately so a single audit row can fan out to many dropped
    links without bloating the parent record. The ``stage_dropped`` field
    pinpoints WHERE in the funnel the URL was rejected, which is what the
    user needs to decide if the filter was wrong.
    """

    __tablename__ = "extraction_audit_link"

    id: Optional[int] = Field(default=None, primary_key=True)
    audit_id: int = Field(index=True, foreign_key="extraction_audit.id")
    url: str = Field(max_length=1024)
    anchor_text: Optional[str] = Field(default=None, max_length=512)
    stage_dropped: str = Field(
        index=True, max_length=32,
        description=(
            "Funnel stage where the URL was dropped: 'llm_filter' or "
            "'taxonomy_filter'"
        ),
    )


class ExtractionAudit(SQLModel, table=True):
    """One row per index-page crawl event."""

    __tablename__ = "extraction_audit"

    id: Optional[int] = Field(default=None, primary_key=True)
    university_slug: str = Field(index=True, max_length=120)
    academic_year: int = Field(index=True)
    index_url: str = Field(max_length=1024)

    raw_link_count: int = Field(
        description="Total <a href> found on the index page before any filtering"
    )
    llm_filtered_count: int = Field(
        description="Links retained after the LLM/heuristic link filter"
    )
    candidate_count: int = Field(
        description=(
            "Final list of detail URLs to crawl after dedupe and any "
            "taxonomy-score filtering"
        )
    )
    extracted_count: int = Field(
        description="Programs successfully committed to the program table"
    )
    quarantined_count: int = Field(
        description="Detail crawls that failed the quality gate"
    )
    recovered_count: int = Field(
        default=0,
        description=(
            "URLs the LLM filter critique retry rescued back into the candidate "
            "set after the first-pass filter rejected them. Non-zero means the "
            "auto-recovery mechanism kicked in for this crawl."
        ),
    )
    job_uid: Optional[str] = Field(
        default=None, max_length=64,
        description="Optional link back to the originating ingestion_job",
    )
    pagination_stop_reason: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "When the index page was auto-paginated, the reason the loop "
            "stopped: 'exhausted' (all pages processed), 'max_pages' "
            "(hit hard cap), 'url_drift' (next page outside index pattern), "
            "'decreasing_yield' (program output collapsed), or "
            "'quality_failed' (post-extract quality breaker). Null when no "
            "pagination occurred."
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True
    )
