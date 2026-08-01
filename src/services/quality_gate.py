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

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from src.scrapers.helpers import is_noise_program_name


MIN_NAME_LENGTH = 3

# Degree-type sanity check: catches cases where name resolution picked the
# wrong candidate (e.g. LLM hallucination) and produced a name whose degree
# type contradicts the URL slug's own unambiguous degree marker. Only fires
# on a clean single-token disagreement — combo slugs/names (e.g.
# "mphil-phd-") are skipped to avoid false positives on legitimately dual
# programmes.
_URL_DEGREE_RE = re.compile(
    r"(?:^|/)(ma|msc|mphil|phd|mba|llm|meng|march|mfin|mres|dphil|dba|edd|mssc|msocsc)-",
    re.IGNORECASE,
)
_NAME_DEGREE_RE = re.compile(
    r"^(MA|MSc|MPhil|PhD|MBA|LLM|MEng|MArch|MFin|MRes|DPhil|DBA|EdD|MSSc|MSocSc)\b",
    re.IGNORECASE,
)


class QuarantineReason(str, Enum):
    # Bad data made it to the gate but failed validation.
    EMPTY_NAME = "empty_name"
    NAME_TOO_SHORT = "name_too_short"
    NOISE_NAME = "noise_name"
    EMPTY_SHELL = "empty_shell"
    DEGREE_TYPE_MISMATCH = "degree_type_mismatch"
    # Extraction failed before producing any data — used when the page
    # had no usable markdown or the cleaner returned None outright. These
    # paths used to fail silently with no DB trace.
    NO_MARKDOWN = "no_markdown"
    EXTRACTION_FAILED = "extraction_failed"


def _degree_type_conflict(name: str, source_url: str) -> bool:
    if not source_url:
        return False
    path = urlsplit(source_url).path
    url_match = _URL_DEGREE_RE.search(path)
    name_match = _NAME_DEGREE_RE.match(name)
    if not url_match or not name_match:
        return False
    return url_match.group(1).lower() != name_match.group(1).lower()


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
    if _degree_type_conflict(name, str(program_data.get("source_url") or "")):
        return QualityVerdict(False, QuarantineReason.DEGREE_TYPE_MISMATCH, signals)

    has_content = (
        signals["has_tuition"]
        or signals["deadline_count"] > 0
        or signals["requirement_count"] > 0
    )
    if not has_content:
        return QualityVerdict(False, QuarantineReason.EMPTY_SHELL, signals)

    return QualityVerdict(True, None, signals)
