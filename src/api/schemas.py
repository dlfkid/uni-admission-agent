"""
Pydantic request/response schemas for the REST API.

Keeps HTTP-layer models separate from the service-layer result models
in ``src.services.crawler``.
"""

from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field, model_validator


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
    page_type_hint: str = Field(
        default="auto",
        description="Page type hint: 'auto', 'index', or 'detail'. Used to skip auto-detection.",
    )
    export_md: bool = Field(
        default=False,
        description="Whether to export crawled markdown files to disk",
    )
    export_path: Optional[str] = Field(
        default=None,
        description="Path to export markdown files (required if export_md=True)",
    )
    html_content: Optional[str] = Field(
        default=None,
        description="Pre-rendered HTML content from browser (bypasses crawling)",
    )
    selected_urls: Optional[List[str]] = Field(
        default=None,
        description="User-selected URLs to crawl (from index page analysis)",
    )
    taxonomy_enabled: Optional[bool] = Field(
        default=None,
        description="Enable taxonomy-guided name matching for this crawl",
    )
    taxonomy_low_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum score to inject taxonomy hints",
    )
    taxonomy_high_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum score to allow high-confidence name override",
    )
    taxonomy_hint_top_k: Optional[int] = Field(
        default=None,
        ge=1,
        le=5,
        description="Maximum taxonomy hints injected into cleaner prompt",
    )
    taxonomy_override_enabled: Optional[bool] = Field(
        default=None,
        description="Enable high-confidence taxonomy override of extracted program name",
    )

    @model_validator(mode="after")
    def _validate_taxonomy_thresholds(self) -> "CrawlRequest":
        low = self.taxonomy_low_threshold
        high = self.taxonomy_high_threshold
        if low is not None and high is not None and low > high:
            raise ValueError("taxonomy_low_threshold must be <= taxonomy_high_threshold")
        return self


class AnalyzeRequest(BaseModel):
    """Body for ``POST /analyze``."""

    url: str = Field(description="Page URL to analyze")
    html_content: str = Field(description="Pre-rendered HTML content from browser")
    page_type_hint: str = Field(
        default="auto",
        description="Page type hint: 'auto', 'index', or 'detail'",
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


class TestConnectionRequest(BaseModel):
    """Body for ``POST /config/test-connection``."""

    base_url: str = Field(description="LLM API base URL")
    api_key: str = Field(default="", description="API key (may be empty for local models)")
    model_name: str = Field(default="", description="Model name to test with")
    temperature: float = Field(default=0.3, description="Sampling temperature")
    max_tokens: int = Field(default=64, description="Max tokens for test request")


class TestConnectionResponse(BaseModel):
    """Response for ``POST /config/test-connection``."""

    success: bool = Field(description="Whether the connection test passed")
    message: str = Field(description="Human-readable result")


# ---------------------------------------------------------------------------
#  Responses
# ---------------------------------------------------------------------------


class LinkCandidate(BaseModel):
    """A candidate link found on an index page."""

    url: str = Field(description="Absolute URL")
    text: str = Field(description="Anchor / display text")


class AnalyzeResponse(BaseModel):
    """Response for ``POST /analyze``."""

    page_type: str = Field(description="'index' or 'detail'")
    links: List[LinkCandidate] = Field(
        default_factory=list,
        description="Candidate course detail links (only for index pages)",
    )
    total_found: int = Field(
        default=0,
        description="Total links found before LLM filtering",
    )


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
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Task parameters (url, univ_slug, year, etc.)",
    )
    tokens_used: int = Field(
        default=0,
        description="Total LLM tokens consumed by this task",
    )
    progress_percent: float = Field(
        default=0.0,
        description="Progress percentage (0-100) for UI rendering",
    )
    progress_meta: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured progress metadata for fine-grained UI display",
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
    study_options: list = Field(default_factory=list)
    deadlines: list = Field(default_factory=list)
    requirements: list = Field(default_factory=list)
    requirement_version: Optional[Dict[str, Any]] = None
    source_url: Optional[str] = None


class ConfigResponse(BaseModel):
    """Current configuration content (Raw)."""

    content: str = Field(description="Raw .env file content")


class CancelResponse(BaseModel):
    """Result of a cancellation request."""

    task_id: str
    cancelled: bool
    message: str


class UniversityResponse(BaseModel):
    """A university entry for the slug dropdown."""

    slug: str
    name: str
    updated_at: str = Field(description="ISO-8601 timestamp of last activity")


class ExportRequest(BaseModel):
    """Body for ``POST /export``."""

    univ_slug: str = Field(description="University slug (a-z0-9-)")
    year: Optional[int] = Field(default=None, description="Academic year filter (omit for all years)")


class IngestionTaskEntry(BaseModel):
    """One stage task state from the Phase 2 ingestion pipeline."""

    stage: str
    state: str
    attempt_count: int
    max_retries: int
    idempotency_key: Optional[str] = None
    error_message: Optional[str] = None
    next_retry_at: Optional[str] = None


class IngestionJobResponse(BaseModel):
    """Phase 2 ingestion job details."""

    job_uid: str
    status: str
    univ_slug: str
    academic_year: int
    source_url: str
    current_stage: Optional[str] = None
    resume_from_stage: Optional[str] = None
    error_message: Optional[str] = None
    request_payload: Dict[str, Any] = Field(default_factory=dict)
    context_payload: Dict[str, Any] = Field(default_factory=dict)
    tasks: List[IngestionTaskEntry] = Field(default_factory=list)


class IngestionResumeRequest(BaseModel):
    """Resume payload for ``POST /ingestion/jobs/{job_uid}/resume``."""

    resume_from_stage: Optional[str] = Field(
        default=None,
        description="Optional stage override: fetch_raw/extract_structured/validate_rules/persist_versioned",
    )
