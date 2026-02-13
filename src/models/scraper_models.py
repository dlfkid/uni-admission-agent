"""
Pydantic models for the scraping engine output.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class CrawlPageResult(BaseModel):
    """Structured result from a single page crawl."""

    url: str = Field(..., description="The URL that was crawled")
    markdown: str = Field(..., description="Page content converted to Markdown")
    char_count: int = Field(..., description="Character count of the Markdown content")
    links: List[str] = Field(default_factory=list, description="All links found on the page")
    status_code: Optional[int] = Field(default=None, description="HTTP status code")


class ExtractedLinks(BaseModel):
    """Structured LLM output for link extraction."""

    links: List[str] = Field(
        default_factory=list,
        description="List of URLs that are likely admission program detail pages",
    )


class ScoutedLink(BaseModel):
    """A single link evaluated by the Heuristic Scout LLM."""

    url: str = Field(..., description="URL identified as potentially valuable")
    reason: str = Field(..., description="Why this link is considered high-value")
    confidence: str = Field(
        ...,
        description="Confidence level: high, medium, or low",
    )


class ScoutedLinks(BaseModel):
    """Structured LLM output for heuristic scout evaluation."""

    links: List[ScoutedLink] = Field(
        default_factory=list,
        description="Top-3 high-potential links identified by heuristic analysis",
    )


class ScoutReport(BaseModel):
    """Terminal report summarizing crawl results for human review."""

    explored_urls: List[str] = Field(default_factory=list)
    failed_urls: List[str] = Field(default_factory=list)
    scouted_links: List[ScoutedLink] = Field(default_factory=list)
    depth_reached: int = Field(default=0)
    programs_imported: int = Field(default=0)


class PageType(str, Enum):
    """Page classification for intelligent crawling."""

    INDEX = "index"  # Course listing page
    DETAIL = "detail"  # Single program detail page


class PageTypeResult(BaseModel):
    """LLM output for page type detection."""

    page_type: PageType = Field(..., description="Detected page type: index or detail")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    reasoning: str = Field(..., max_length=100, description="Brief explanation")


class ProgramContext(BaseModel):
    """Historical context for a program to aid matching."""
    name_en: str
    program_group_code: str
    faculty: Optional[str] = None
    tuition_amount: Optional[float] = None
    currency: Optional[str] = None
    frequency: Optional[str] = None # e.g. "per year" - inferred? simplified for now.
    
    def normalize_name(self) -> str:
        import re
        s = self.name_en.lower()
        return re.sub(r'[^a-z0-9]', '', s)
