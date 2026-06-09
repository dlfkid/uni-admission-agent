"""Shared types for the crawl-strategy subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


class FetchMode(str, Enum):
    """Transport/rendering mode used to retrieve a page."""

    SERVER = "server"
    CLIENT = "client"
    CLIENT_WAIT = "client_wait"
    API = "api"


class ExtractKind(str, Enum):
    """Algorithm used to pull programme names from the retrieved page."""

    HEADING_LINK = "heading_link"
    INLINE_DEGREE = "inline_degree"
    MERGED_COLUMNS = "merged_columns"
    BLOB = "blob"
    TEXT_HEADING = "text_heading"
    JSON_API = "json_api"
    LLM = "llm"


class PaginateMode(str, Enum):
    """How to obtain more programmes than the first screen holds."""

    NONE = "none"
    SCROLL = "scroll"
    URL_PAGES = "url_pages"


@dataclass(frozen=True)
class Strategy:
    """Immutable pairing of a fetch mode and an extract kind, plus free-form params."""

    fetch: FetchMode
    extract: ExtractKind
    params: Dict[str, Any] = field(default_factory=dict)
    paginate: PaginateMode = PaginateMode.NONE

    def label(self) -> str:
        """Return a short human-readable identifier for this strategy."""
        return f"{self.fetch.value}×{self.extract.value}"


@dataclass(frozen=True)
class CrawlRange:
    """How much of an index to crawl. ``limit=None`` means all (safety-capped)."""

    limit: Optional[int] = 30
    paginate: bool = False

    @classmethod
    def default(cls) -> "CrawlRange":
        """First batch only, capped at 30 — the no-argument default."""
        return cls(limit=30, paginate=False)

    @classmethod
    def of(cls, n: int) -> "CrawlRange":
        """Crawl the first ``n`` programmes, paginating as needed."""
        return cls(limit=n, paginate=True)

    @classmethod
    def all_(cls) -> "CrawlRange":
        """Crawl everything, paginating to exhaustion (safety-capped)."""
        return cls(limit=None, paginate=True)


@dataclass
class ExtractItem:
    """A single extracted programme entry."""

    name_en: str
    detail_url: Optional[str] = None


@dataclass
class FetchResult:
    """Raw output from a fetch operation."""

    html: str
    markdown: str
    level_used: str
    levels_tried: List[str] = field(default_factory=list)


@dataclass
class CrawlOutcome:
    """Aggregated result returned to the caller after a full crawl cycle."""

    status: Literal["ok", "llm_fallback", "unsupported"]
    university: str
    names: List[str] = field(default_factory=list)
    items: List[ExtractItem] = field(default_factory=list)
    names_count: int = 0
    details_imported: int = 0
    quarantined: int = 0
    strategy_used: Optional[str] = None
    report_zip: Optional[str] = None
    message_for_user: str = ""
    pages_fetched: int = 0
    stopped_reason: str = ""
