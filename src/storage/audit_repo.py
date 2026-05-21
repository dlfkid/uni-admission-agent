"""Repository for ExtractionAudit funnel rows.

Append-only: each crawl event is a separate row so trends are visible
over time. Cleanup, when needed, is by deletion through CLI / REST
(not implemented in this initial cut).
"""
from __future__ import annotations

from typing import List, Optional

from sqlmodel import Session, col, select

from src.models.extraction_audit import ExtractionAudit


class ExtractionAuditRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        university_slug: str,
        academic_year: int,
        index_url: str,
        raw_link_count: int,
        llm_filtered_count: int,
        candidate_count: int,
        extracted_count: int,
        quarantined_count: int,
        job_uid: Optional[str] = None,
    ) -> ExtractionAudit:
        entry = ExtractionAudit(
            university_slug=university_slug,
            academic_year=int(academic_year),
            index_url=index_url,
            raw_link_count=int(raw_link_count),
            llm_filtered_count=int(llm_filtered_count),
            candidate_count=int(candidate_count),
            extracted_count=int(extracted_count),
            quarantined_count=int(quarantined_count),
            job_uid=job_uid,
        )
        self._session.add(entry)
        self._session.commit()
        self._session.refresh(entry)
        return entry

    def list_for(
        self,
        *,
        university_slug: Optional[str] = None,
        year: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[ExtractionAudit]:
        stmt = select(ExtractionAudit).order_by(col(ExtractionAudit.id).desc())
        if university_slug is not None:
            stmt = stmt.where(ExtractionAudit.university_slug == university_slug)
        if year is not None:
            stmt = stmt.where(ExtractionAudit.academic_year == year)
        if limit is not None:
            stmt = stmt.limit(int(limit))
        return list(self._session.exec(stmt).all())
