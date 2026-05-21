"""ProgramQuarantine: extraction results that failed the quality gate.

These records do NOT live in the main ``program`` table — they are held
here so the user can inspect why an extraction was rejected, and so
follow-up tooling (re-extract, manual repair, retry with different
provider) has a stable place to read from.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class ProgramQuarantine(SQLModel, table=True):
    """A single extracted-but-rejected program record."""

    __tablename__ = "program_quarantine"

    id: Optional[int] = Field(default=None, primary_key=True)
    university_slug: str = Field(index=True, max_length=120)
    academic_year: int = Field(index=True)
    source_url: str = Field(index=True, max_length=1024)
    extracted_name: Optional[str] = Field(default=None, max_length=512)
    payload: str = Field(description="Full extracted program_data JSON")
    quarantine_reason: str = Field(index=True, max_length=64)
    quarantine_signals: str = Field(default="{}", description="JSON diagnostics")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
