"""Quality gate — last barrier between extraction and persistence.

Refuses to commit extracted program records that are obviously broken:
- no usable name (empty / whitespace / too short)
- name matches the project's noise heuristic (sidebar / breadcrumb / boilerplate)
- "empty shell": a name but no identifying content (no tuition, deadline, or
  requirement)

Rejected records are routed to the program_quarantine table so they remain
visible for diagnosis and possible manual recovery, without polluting the
main ``program`` table that downstream consumers query.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from src.scrapers.helpers import is_noise_program_name


MIN_NAME_LENGTH = 3


class QuarantineReason(str, Enum):
    # Bad data made it to the gate but failed validation.
    EMPTY_NAME = "empty_name"
    NAME_TOO_SHORT = "name_too_short"
    NOISE_NAME = "noise_name"
    EMPTY_SHELL = "empty_shell"
    # Extraction failed before producing any data — used when the page
    # had no usable markdown or the cleaner returned None outright. These
    # paths used to fail silently with no DB trace.
    NO_MARKDOWN = "no_markdown"
    EXTRACTION_FAILED = "extraction_failed"


@dataclass(frozen=True)
class QualityVerdict:
    """Outcome of evaluating one extracted program record."""

    passed: bool
    reason: Optional[QuarantineReason] = None
    signals: Dict[str, Any] = field(default_factory=dict)


def _collect_signals(program_data: Dict[str, Any]) -> Dict[str, Any]:
    name = str(program_data.get("name_en") or "").strip()
    return {
        "name_length": len(name),
        "has_tuition": program_data.get("tuition_amount") is not None,
        "deadline_count": len(program_data.get("deadlines") or []),
        "requirement_count": len(program_data.get("requirements") or []),
        "study_option_count": len(program_data.get("study_options") or []),
    }


def evaluate_extraction(program_data: Dict[str, Any]) -> QualityVerdict:
    """Return a verdict for one extracted program record.

    Order of checks matters: name failures are reported before content
    failures because name is the most actionable root cause (you can't
    review a record you can't identify).
    """
    signals = _collect_signals(program_data)
    name = str(program_data.get("name_en") or "").strip()

    if not name:
        return QualityVerdict(False, QuarantineReason.EMPTY_NAME, signals)
    if len(name) < MIN_NAME_LENGTH:
        return QualityVerdict(False, QuarantineReason.NAME_TOO_SHORT, signals)
    if is_noise_program_name(name):
        return QualityVerdict(False, QuarantineReason.NOISE_NAME, signals)

    has_content = (
        signals["has_tuition"]
        or signals["deadline_count"] > 0
        or signals["requirement_count"] > 0
    )
    if not has_content:
        return QualityVerdict(False, QuarantineReason.EMPTY_SHELL, signals)

    return QualityVerdict(True, None, signals)
