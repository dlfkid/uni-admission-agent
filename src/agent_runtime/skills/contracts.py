"""Typed skill input/output contracts for the agent runtime."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class AnalyzePageSkillInput(BaseModel):
    """Input payload for page analysis skill."""

    url: str = Field(min_length=1)
    page_type_hint: str = "auto"
    html_content: str = ""


class AnalyzePageSkillOutput(BaseModel):
    """Output payload for page analysis skill."""

    page_type: str
    links: list[dict[str, Any]] = Field(default_factory=list)
    total_found: int = 0


class SelectDetailCandidatesSkillInput(BaseModel):
    """Input payload for candidate selection skill."""

    links: list[dict[str, Any]] = Field(default_factory=list)
    top_k: int = Field(default=20, ge=1, le=200)


class SelectDetailCandidatesSkillOutput(BaseModel):
    """Output payload for candidate selection skill."""

    selected_urls: list[str] = Field(default_factory=list)


class CrawlDetailBatchSkillInput(BaseModel):
    """Input payload for detail-batch crawl skill."""

    index_url: str = Field(min_length=1)
    selected_urls: list[str] = Field(default_factory=list)
    univ_slug: str = Field(min_length=1)
    year: int = Field(gt=0)
    batch_size: int = Field(default=4, ge=1, le=50)
    client_id: Optional[str] = None
    strict_client: bool = True
    selected_link_texts: dict[str, str] = Field(default_factory=dict)


class CrawlDetailBatchSkillOutput(BaseModel):
    """Output payload for detail-batch crawl skill."""

    imported_count: int = 0
    total_selected: int = 0
    batch_total: int = 0
    failed_urls: list[str] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)


class PersistProgramsSkillInput(BaseModel):
    """Input payload for persistence skill."""

    univ_slug: str = Field(min_length=1)
    year: int = Field(gt=0)
    programs: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = Field(default=False)


class PersistProgramsSkillOutput(BaseModel):
    """Output payload for persistence skill."""

    imported_count: int = 0
    updated_count: int = 0
    total_submitted: int = 0
    failed_items: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = False
    parsed_programs: list[dict[str, Any]] = Field(default_factory=list)


class ReviewPatchSkillInput(BaseModel):
    """Input payload for review patch skill."""

    program_id: int = Field(gt=0)
    patch: dict[str, Any] = Field(default_factory=dict)


class ReviewPatchSkillOutput(BaseModel):
    """Output payload for review patch skill."""

    updated: bool = False
    program_id: int
    summary: Optional[str] = None


class QueryDbSkillInput(BaseModel):
    """Input payload for db query skill."""

    univ_slug: str = Field(min_length=1)
    year: Optional[int] = Field(default=None, gt=0)


class QueryDbSkillOutput(BaseModel):
    """Output payload for db query skill."""

    programs: list[dict[str, Any]] = Field(default_factory=list)


class BrowserAutomationSkillInput(BaseModel):
    """Input payload for browser automation skill."""

    url: str = Field(min_length=1)
    page_type_hint: str = "auto"
    client_id: Optional[str] = None


class BrowserAutomationSkillOutput(BaseModel):
    """Output payload for browser automation skill."""

    html_content: Optional[str] = None
    detail_pages_batch: list[dict[str, Any]] = Field(default_factory=list)
    selected_urls: list[str] = Field(default_factory=list)
    selected_link_texts: dict[str, str] = Field(default_factory=dict)
    extracted_programs: list[dict[str, Any]] = Field(default_factory=list)


class PaginationInfo(BaseModel):
    """Pagination metadata extracted from an index page."""

    pagination_type: Literal["url_param", "single_page", "spa_button"] = "single_page"
    page_urls: list[str] = Field(default_factory=list)
    total_pages: Optional[int] = None
    current_page: int = 1
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class QualityCheckResult(BaseModel):
    """Result of a quality circuit breaker check on a batch of programs."""

    verdict: Literal["pass", "fail"]
    heuristic_score: float = Field(ge=0.0, le=1.0)
    llm_used: bool = False
    reason: str = ""
    failed_at_page: Optional[int] = None
    failed_at_program_count: Optional[int] = None


class PaginatedCrawlSkillInput(BaseModel):
    """Input payload for paginated crawl skill."""

    url: str = Field(min_length=1)
    univ_slug: str = Field(min_length=1)
    year: int = Field(gt=0)
    max_pages: int = Field(default=50, ge=1, le=200)
    batch_quality_size: int = Field(default=10, ge=5, le=50)
    client_id: Optional[str] = None


class PaginatedCrawlSkillOutput(BaseModel):
    """Output payload for paginated crawl skill."""

    status: Literal["done", "quality_failed", "pagination_not_supported"] = "done"
    pagination_type: str = "single_page"
    total_pages_detected: Optional[int] = None
    pages_processed: int = 0
    programs: list[dict[str, Any]] = Field(default_factory=list)
    total_programs: int = 0
    quality_scores: list[dict[str, Any]] = Field(default_factory=list)
    warning: Optional[str] = None
    summary: str = ""
