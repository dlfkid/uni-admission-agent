"""
Pydantic request/response schemas for the REST API.

Keeps HTTP-layer models separate from the service-layer result models
in ``src.services.crawler``.
"""

from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
#  Requests
# ---------------------------------------------------------------------------


class CrawlRequest(BaseModel):
    """Body for ``POST /crawl``."""

    url: str = Field(description="Starting URL to crawl")
    univ_slug: str = Field(description="University slug (a-z0-9-)")
    year: int = Field(description="Academic year (e.g. 2026)")
    continue_depth: int = Field(
        default=0,
        description="Extra depth for LLM-driven scouting",
    )


class QueryRequest(BaseModel):
    """Query parameters for ``GET /programs``."""

    univ_slug: str = Field(description="University slug")
    year: Optional[int] = Field(default=None, description="Academic year filter")


class ConfigRequest(BaseModel):
    """Configuration update payload (Raw)."""

    content: str = Field(description="New .env file content")


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider."""
    
    # Generic key-value pairs for the provider (e.g. API_KEY, MODEL_NAME)
    # We use a dict to be flexible, but strict validation can be added later
    settings: Dict[str, str] = Field(description="Key-value settings for this provider")


class StructuredConfig(BaseModel):
    """Structured configuration payload."""

    database_url: str = Field(description="Database connection URL")
    llm_priority: List[str] = Field(description="Ordered list of LLM providers")
    providers: Dict[str, Dict[str, str]] = Field(
        description="Map of provider name to its settings (key-value pairs)"
    )


# ---------------------------------------------------------------------------
#  Responses
# ---------------------------------------------------------------------------


class CrawlResponse(BaseModel):
    """Response for ``POST /crawl``."""

    task_id: str = Field(description="Unique task identifier for status polling")
    message: str = Field(default="Task submitted")


class TaskStatusResponse(BaseModel):
    """Response for ``GET /tasks/{task_id}``."""

    task_id: str
    state: str = Field(description="PENDING | RUNNING | DONE | FAILED")
    progress: Optional[str] = Field(
        default=None,
        description="Human-readable progress message",
    )
    result: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Final result when state=DONE",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message when state=FAILED",
    )
    logs: List[str] = Field(
        default_factory=list,
        description="Execution logs",
    )


class StatusResponse(BaseModel):
    """Response for ``GET /status``."""

    university_count: int = 0
    program_count: int = 0
    universities: List[Dict[str, Any]] = Field(default_factory=list)


class ProgramResponse(BaseModel):
    """Single program in query results."""

    id: Optional[int] = None
    name_en: str = ""
    name_zh: Optional[str] = None
    academic_year: int = 0
    faculty: Optional[str] = None
    program_group_code: Optional[str] = None
    tuition_amount: Optional[float] = None
    currency: Optional[str] = None


class ConfigResponse(BaseModel):
    """Current configuration content (Raw)."""

    content: str = Field(description="Raw .env file content")


class CancelResponse(BaseModel):
    """Result of a cancellation request."""

    task_id: str
    cancelled: bool
    message: str
