from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Enum as SqlEnum, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IngestionStage(str, Enum):
    FETCH_RAW = "fetch_raw"
    EXTRACT_STRUCTURED = "extract_structured"
    VALIDATE_RULES = "validate_rules"
    PERSIST_VERSIONED = "persist_versioned"


def _enum_values(enum_cls: type[Enum]) -> List[str]:
    return [member.value for member in enum_cls]


INGESTION_STAGE_ENUM = SqlEnum(
    IngestionStage,
    name="ingestionstage",
    values_callable=_enum_values,
)


class IngestionJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    POISONED = "POISONED"
    CANCELLED = "CANCELLED"


class IngestionTaskState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    POISONED = "POISONED"
    SKIPPED = "SKIPPED"


class IngestionJob(SQLModel, table=True):
    __tablename__ = "ingestion_job"
    __table_args__ = (
        UniqueConstraint("job_uid", name="uq_ingestion_job_uid"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_uid: str = Field(index=True)
    univ_slug: str = Field(index=True)
    academic_year: int = Field(index=True)
    source_url: str = Field(default="")
    continue_depth: int = Field(default=0)
    page_type_hint: str = Field(default="auto")

    status: IngestionJobStatus = Field(default=IngestionJobStatus.PENDING, index=True)
    current_stage: Optional[IngestionStage] = Field(
        default=None,
        sa_column=Column(INGESTION_STAGE_ENUM, nullable=True, index=True),
    )
    resume_from_stage: Optional[IngestionStage] = Field(
        default=None,
        sa_column=Column(INGESTION_STAGE_ENUM, nullable=True, index=True),
    )

    request_payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    context_payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))

    error_message: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=_utc_now, index=True)
    updated_at: datetime = Field(default_factory=_utc_now, index=True)
    started_at: Optional[datetime] = Field(default=None, index=True)
    finished_at: Optional[datetime] = Field(default=None, index=True)

    tasks: List["IngestionTask"] = Relationship(back_populates="job")


class IngestionTask(SQLModel, table=True):
    __tablename__ = "ingestion_task"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", name="uq_ingestion_task_job_stage"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="ingestion_job.id", index=True)

    stage: IngestionStage = Field(
        sa_column=Column(INGESTION_STAGE_ENUM, nullable=False, index=True),
    )
    state: IngestionTaskState = Field(default=IngestionTaskState.PENDING, index=True)
    idempotency_key: Optional[str] = Field(default=None, index=True)

    input_payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    output_payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    error_message: Optional[str] = Field(default=None)

    attempt_count: int = Field(default=0)
    max_retries: int = Field(default=2)
    backoff_seconds: int = Field(default=0)
    next_retry_at: Optional[datetime] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=_utc_now, index=True)
    updated_at: datetime = Field(default_factory=_utc_now, index=True)
    started_at: Optional[datetime] = Field(default=None, index=True)
    finished_at: Optional[datetime] = Field(default=None, index=True)

    job: IngestionJob = Relationship(back_populates="tasks")
