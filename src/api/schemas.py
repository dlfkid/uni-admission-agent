"""
Pydantic request/response schemas for the REST API.

Keeps HTTP-layer models separate from the service-layer result models
in ``src.services.crawler``.
"""

from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator


# ---------------------------------------------------------------------------
#  Requests
# ---------------------------------------------------------------------------


class DetailPagePayload(BaseModel):
    """Browser-collected detail page payload."""

    url: str = Field(description="Detail page URL")
    html_content: str = Field(description="Full HTML content captured in browser context")
    selected_anchor_text: Optional[str] = Field(
        default=None,
        description="Optional selected link text from the source index page",
    )


class PolicyProfilePayload(BaseModel):
    """Client-side policy profile overrides for one crawl request."""

    auto_run_max_candidates: Optional[int] = Field(default=None, ge=1, le=200)
    taxonomy_auto_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    taxonomy_keep_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    prefer_browser_provider: Optional[str] = Field(default=None)
    require_manual_review_when_low_confidence: Optional[bool] = Field(default=None)
    llm_fallback_enabled: Optional[bool] = Field(default=None)
    batch_size: Optional[int] = Field(default=None, ge=1, le=50)
    detail_concurrency: Optional[int] = Field(default=None, ge=1, le=20)


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
    selected_link_texts: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional mapping of selected URL to anchor text",
    )
    browser_automation_enabled: bool = Field(
        default=False,
        description="Whether index detail pages are collected in browser before submit",
    )
    detail_pages_batch: Optional[List[DetailPagePayload]] = Field(
        default=None,
        description="Batch payload of detail page HTML collected from browser tabs",
    )
    batch_index: Optional[int] = Field(
        default=None,
        description="Current 1-based batch index in a multi-batch crawl session",
    )
    batch_total: Optional[int] = Field(
        default=None,
        description="Total batch count in a multi-batch crawl session",
    )
    browser_provider: str = Field(
        default="auto",
        description="Browser HTML provider: auto, server, or client",
    )
    client_id: Optional[str] = Field(
        default=None,
        description="Optional connected client id for browser automation dispatch",
    )
    strict_client: bool = Field(
        default=False,
        description="When true, fail instead of falling back if client automation is unavailable",
    )
    candidate_taxonomy_filter_enabled: bool = Field(
        default=False,
        description="Enable taxonomy scoring filter for index/auto candidate detail links",
    )
    candidate_taxonomy_filter_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Minimum taxonomy score to keep a candidate detail link",
    )
    candidate_taxonomy_filter_top_k: int = Field(
        default=30,
        ge=1,
        le=200,
        description="Maximum candidate links retained after taxonomy filter",
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
    name_resolution_llm_enabled: Optional[bool] = Field(
        default=None,
        description="Enable low-confidence program-name LLM fallback in index->detail flow",
    )
    name_resolution_low_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold before triggering name-resolution fallback",
    )
    name_resolution_conflict_delta: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum top-candidate score gap required to avoid fallback",
    )
    policy_profile: Optional[PolicyProfilePayload] = Field(
        default=None,
        description="Optional per-request policy profile overrides from client",
    )

    @model_validator(mode="after")
    def _validate_taxonomy_thresholds(self) -> "CrawlRequest":
        low = self.taxonomy_low_threshold
        high = self.taxonomy_high_threshold
        if low is not None and high is not None and low > high:
            raise ValueError("taxonomy_low_threshold must be <= taxonomy_high_threshold")
        return self

    @field_validator("browser_provider")
    @classmethod
    def _validate_browser_provider(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"auto", "server", "client"}:
            raise ValueError("browser_provider must be one of: auto, server, client")
        return normalized


class AgentRunRequest(BaseModel):
    """Body for ``POST /agent/run``."""

    url: str = Field(description="Starting URL to crawl via agent runtime")
    univ_slug: str = Field(description="University slug (a-z0-9-)")
    year: int = Field(description="Academic year (e.g. 2026)")
    page_type_hint: str = Field(
        default="auto",
        description="Page type hint: auto/index/detail",
    )
    runtime: Optional[str] = Field(
        default=None,
        description="Optional runtime override: legacy or pydanticai",
    )
    policy_profile: Optional[PolicyProfilePayload] = Field(
        default=None,
        description="Optional per-request policy profile overrides from client",
    )


class AgentReviewConfirmRequest(BaseModel):
    """Body for ``POST /agent/review/confirm``."""

    task_id: str = Field(description="Existing agent task id with onhold review context")
    selection_text: Optional[str] = Field(
        default=None,
        description="Optional free-form user selection text (e.g. 'continue 3,6,18')",
    )
    selected_indices: Optional[list[int]] = Field(
        default=None,
        description="Optional explicit onhold indices to continue",
    )


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


class ProgramPatchRequest(BaseModel):
    """Body for ``PATCH /programs/{program_id}``."""

    model_config = ConfigDict(extra="forbid")

    # Editable fields
    name_en: Optional[str] = Field(default=None)
    name_zh: Optional[str] = Field(default=None)
    faculty: Optional[str] = Field(default=None)
    program_group_code: Optional[str] = Field(default=None)
    tuition_amount: Optional[float] = Field(default=None)
    currency: Optional[str] = Field(default=None)
    study_options: Optional[List[Dict[str, Any]]] = Field(default=None)
    deadlines: Optional[List[Dict[str, Any]]] = Field(default=None)
    requirements: Optional[List[Dict[str, Any]]] = Field(default=None)
    source_url: Optional[str] = Field(default=None)

    # Blocked fields (accepted only to return explicit 400 detail)
    id: Optional[int] = Field(default=None)
    university_id: Optional[int] = Field(default=None)
    program_catalog_id: Optional[int] = Field(default=None)
    academic_year: Optional[int] = Field(default=None)


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


class AgentRunResponse(BaseModel):
    """Response for ``POST /agent/run``."""

    task_id: str = Field(description="Unique task identifier for agent task polling")
    message: str = Field(default="Agent task submitted")


class AgentReviewConfirmResponse(BaseModel):
    """Response for ``POST /agent/review/confirm``."""

    task_id: str = Field(description="Agent task identifier")
    selected_indices: List[int] = Field(default_factory=list)
    invalid_indices: List[int] = Field(default_factory=list)
    invalid_tokens: List[str] = Field(default_factory=list)
    selected_count: int = 0
    discarded_count: int = 0
    total_onhold: int = 0


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


class ClientInfoResponse(BaseModel):
    """Connected client status for browser automation dispatch."""

    client_id: str = Field(description="Stable client identifier")
    client_name: str = Field(description="Human-readable client label")
    platform: str = Field(description="Client OS platform")
    arch: str = Field(description="Client CPU architecture")
    workdir: str = Field(description="Client working directory")
    capabilities: Dict[str, Any] = Field(
        default_factory=dict,
        description="Client capability map",
    )
    last_seen_epoch: float = Field(
        description="UNIX epoch timestamp of last heartbeat",
    )


class StatusResponse(BaseModel):
    """Response for ``GET /status``."""

    university_count: int = 0
    program_count: int = 0
    client_count: int = Field(default=0, description="Number of currently connected browser clients")
    client_ids: List[str] = Field(default_factory=list, description="List of connected client identifiers")
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


class DeleteProgramResponse(BaseModel):
    """Response for ``DELETE /programs/{program_id}``."""

    program_id: int
    deleted: bool
    message: str


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
