"""Repository for ProgramQuarantine CRUD.

Keeps the table append-then-replace per (university_slug, source_url):
re-extracting the same URL overwrites the prior quarantine row, so the
table tracks the latest verdict for each URL rather than every retry.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from src.models.quarantine import ProgramQuarantine
from src.services.quality_gate import QuarantineReason


class QuarantineRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        university_slug: str,
        program_data: Dict[str, Any],
        reason: QuarantineReason,
        signals: Dict[str, Any],
    ) -> ProgramQuarantine:
        """Upsert a quarantine row keyed by (university_slug, source_url)."""
        # Mirrors db_manager.upsert_program's fallback: page_processor's
        # generic extraction path only ever sets source_url under
        # extra_metadata, never as a top-level key. Without this fallback,
        # every such rejection resolves to source_url="" and the upsert-by
        # -source_url identity above collapses ALL of them into one row —
        # each new empty-shell/etc. rejection silently overwrites the last,
        # hiding every prior one with no error or log signal.
        source_url = str(program_data.get("source_url") or "").strip()
        if not source_url:
            extra_metadata = program_data.get("extra_metadata")
            if isinstance(extra_metadata, dict):
                source_url = str(extra_metadata.get("source_url") or "").strip()
        existing = self._session.exec(
            select(ProgramQuarantine)
            .where(ProgramQuarantine.university_slug == university_slug)
            .where(ProgramQuarantine.source_url == source_url)
        ).first()

        if existing is None:
            entry = ProgramQuarantine(
                university_slug=university_slug,
                academic_year=int(program_data.get("academic_year") or 0),
                source_url=source_url,
                extracted_name=str(program_data.get("name_en") or "") or None,
                payload=json.dumps(program_data, ensure_ascii=False, default=str),
                quarantine_reason=reason.value,
                quarantine_signals=json.dumps(signals, ensure_ascii=False, default=str),
            )
            self._session.add(entry)
        else:
            entry = existing
            entry.academic_year = int(program_data.get("academic_year") or 0)
            entry.extracted_name = str(program_data.get("name_en") or "") or None
            entry.payload = json.dumps(program_data, ensure_ascii=False, default=str)
            entry.quarantine_reason = reason.value
            entry.quarantine_signals = json.dumps(signals, ensure_ascii=False, default=str)
            entry.created_at = datetime.now(timezone.utc)

        self._session.commit()
        self._session.refresh(entry)
        return entry

    def list_for(
        self,
        *,
        university_slug: Optional[str] = None,
        year: Optional[int] = None,
    ) -> List[ProgramQuarantine]:
        stmt = select(ProgramQuarantine)
        if university_slug is not None:
            stmt = stmt.where(ProgramQuarantine.university_slug == university_slug)
        if year is not None:
            stmt = stmt.where(ProgramQuarantine.academic_year == year)
        return list(self._session.exec(stmt).all())

    def clear(
        self,
        *,
        university_slug: Optional[str] = None,
        source_url: Optional[str] = None,
        reason: Optional[QuarantineReason] = None,
        year: Optional[int] = None,
    ) -> int:
        """Delete quarantine rows matching the given filters.

        At least one filter must be supplied — we refuse to nuke the
        whole table accidentally. Returns the number of rows deleted.
        """
        if not any([university_slug, source_url, reason, year]):
            raise ValueError(
                "clear() requires at least one of university_slug, source_url, reason, year"
            )

        stmt = select(ProgramQuarantine)
        if university_slug is not None:
            stmt = stmt.where(ProgramQuarantine.university_slug == university_slug)
        if source_url is not None:
            stmt = stmt.where(ProgramQuarantine.source_url == source_url)
        if reason is not None:
            stmt = stmt.where(ProgramQuarantine.quarantine_reason == reason.value)
        if year is not None:
            stmt = stmt.where(ProgramQuarantine.academic_year == int(year))

        rows = list(self._session.exec(stmt).all())
        for row in rows:
            self._session.delete(row)
        self._session.commit()
        return len(rows)
