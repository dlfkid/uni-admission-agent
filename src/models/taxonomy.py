from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import SQLModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SubjectTaxonomy(SQLModel, table=True):
    __tablename__ = "subject_taxonomy"
    __table_args__ = (
        UniqueConstraint(
            "normalized_name",
            name="uq_subject_taxonomy_normalized_name",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    name_en: str = Field(index=True)
    normalized_name: str = Field(index=True)
    aliases: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    source: str = Field(default="seed", index=True)
    first_seen_url: Optional[str] = Field(default=None)
    confidence: Optional[float] = Field(default=None)
    status: str = Field(default="active", index=True)
    updated_at: datetime = Field(default_factory=_utc_now)
