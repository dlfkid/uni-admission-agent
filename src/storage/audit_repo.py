"""Repository for ExtractionAudit funnel rows.

Append-only: each crawl event is a separate row so trends are visible
over time. Cleanup, when needed, is by deletion through CLI / REST
(not implemented in this initial cut).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlmodel import Session, col, select

from src.models.extraction_audit import ExtractionAudit, ExtractionAuditLink


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
        recovered_count: int = 0,
        job_uid: Optional[str] = None,
        dropped_links: Optional[List[Dict[str, Any]]] = None,
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
            recovered_count=int(recovered_count),
            job_uid=job_uid,
        )
        self._session.add(entry)
        self._session.commit()
        self._session.refresh(entry)

        # Fan out dropped-link rows. Empty / None lists write nothing.
        for link in dropped_links or []:
            url = str(link.get("url") or "").strip()
            if not url:
                continue
            self._session.add(
                ExtractionAuditLink(
                    audit_id=entry.id,
                    url=url,
                    anchor_text=link.get("anchor_text") or None,
                    stage_dropped=str(link.get("stage_dropped") or "unknown"),
                )
            )
        if dropped_links:
            self._session.commit()

        return entry

    def list_dropped_links(self, *, audit_id: int) -> List[ExtractionAuditLink]:
        stmt = (
            select(ExtractionAuditLink)
            .where(ExtractionAuditLink.audit_id == audit_id)
            .order_by(col(ExtractionAuditLink.id).asc())
        )
        return list(self._session.exec(stmt).all())

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
