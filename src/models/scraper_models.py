"""
Pydantic models for the scraping engine output.
"""

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
