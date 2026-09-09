"""Turn the tuition rows a page publishes into the one coarse number the rest
of the system reads.

Pages price tuition along axes the single Program.tuition_amount column cannot
hold — by study mode (CUHK), by applicant scope (EdUHK, Leeds, Manchester,
UCL), or as a programme total next to a per-credit rate (PolyU). The LLM now
copies every statement into ParsedProgramData.tuition_fees; this module
decides which one becomes the headline, by a fixed priority, in code.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol, Sequence

from src.models.admission import CurrencyCode, StudyMode, TuitionBasis, TuitionScope

logger = logging.getLogger(__name__)

# Explicit "both" wordings win before any single-scope keyword is looked for:
# PolyU writes "for local and non-local students" under one figure.
_BOTH_RE = re.compile(r"\b(local|home|uk)\b.{0,12}\b(and|&|/)\b.{0,12}\b(non-?local|international|overseas)\b", re.I)
_NON_LOCAL_RE = re.compile(r"non-?local|international|overseas|\bEU\b|非本地|國際|国际", re.I)
_LOCAL_RE = re.compile(r"\blocal\b|\bhome\b|\bUK\b|domestic|本地", re.I)
_UK_EU_RE = re.compile(r"\b(UK|Home)\s*(/|and|&)\s*EU\b", re.I)
# Funding nature — a DIFFERENT axis from applicant scope. HK pages price a
# programme by whether the place is UGC-funded or self-financed; a
# self-financed place is open to local and non-local alike, so such a label
# carries no scope information and ALL is the correct reading. Checked only
# after the scope keywords, because a label can state both axes at once
# ("UGC-funded local students" is LOCAL).
_FUNDING_NATURE_RE = re.compile(
    r"self[-\s]?financ|self[-\s]?fund|UGC[-\s]?fund|government[-\s]?fund"
    r"|publicly[-\s]?fund|自資|自资|資助|资助",
    re.I,
)


def normalize_applicant_scope(label: Optional[str]) -> TuitionScope:
    """Map the page's applicant wording onto the three filterable values.

    Order matters: an explicit both-scopes phrase is ALL; the pre-Brexit
    "UK/EU" or "Home/EU" pairing is one home band (LOCAL); any non-local
    keyword is NON_LOCAL (bare "EU" included — UK pages now price EU with
    International); any local keyword is LOCAL. A label that states only
    funding nature (self-financed / UGC-funded) is ALL without a warning —
    that axis is orthogonal to scope, so ALL is right rather than unhandled.
    Anything else is ALL with a warning so the vocabulary can be extended.
    """
    text = " ".join(str(label or "").split())
    if not text:
        return TuitionScope.ALL
    if _BOTH_RE.search(text) or re.search(r"\ball\b", text, re.I):
        return TuitionScope.ALL
    if _UK_EU_RE.search(text):
        return TuitionScope.LOCAL
    if _NON_LOCAL_RE.search(text):
        return TuitionScope.NON_LOCAL
    if _LOCAL_RE.search(text):
        return TuitionScope.LOCAL
    if _FUNDING_NATURE_RE.search(text):
        return TuitionScope.ALL
    logger.warning("Unrecognised tuition applicant wording %r — stored as scope=all", text)
    return TuitionScope.ALL


_SCOPE_ORDER = (TuitionScope.NON_LOCAL, TuitionScope.ALL, TuitionScope.LOCAL)
_MODE_ORDER = (StudyMode.FULL_TIME, StudyMode.UNKNOWN, StudyMode.PART_TIME, StudyMode.HYBRID)


class FeeRow(Protocol):
    """What derive_headline_tuition needs from a fee; ParsedTuitionFee satisfies it."""
    amount: Decimal
    currency: CurrencyCode
    basis: TuitionBasis
    study_mode: StudyMode
    applicant_scope: TuitionScope
    credits: Optional[int]


@dataclass(frozen=True)
class DerivedFee:
    """A programme total computed from a per-annum or per-credit row. Written to
    the detail table with is_derived=True so the headline stays traceable."""
    amount: Decimal
    currency: CurrencyCode
    basis: TuitionBasis
    study_mode: StudyMode
    applicant_scope: TuitionScope
    source_text: str


@dataclass(frozen=True)
class HeadlineResult:
    amount: Optional[Decimal]
    currency: Optional[CurrencyCode]
    derived: tuple[DerivedFee, ...] = ()


_EMPTY = HeadlineResult(amount=None, currency=None)


def _years_for(mode: StudyMode, study_options: Sequence[tuple[StudyMode, Optional[int]]]) -> Optional[int]:
    """Whole years for *mode*; an undistinguished fee uses the full-time duration."""
    wanted = StudyMode.FULL_TIME if mode is StudyMode.UNKNOWN else mode
    for opt_mode, months in study_options:
        if opt_mode is wanted and months:
            return max(1, math.ceil(months / 12))
    return None


def _programme_total(fee: FeeRow, study_options) -> Optional[tuple[Decimal, Optional[DerivedFee]]]:
    """The programme-total reading of one fee row, deriving if the basis needs it."""
    if fee.basis is TuitionBasis.PER_PROGRAMME:
        return fee.amount, None
    if fee.basis is TuitionBasis.PER_ANNUM:
        years = _years_for(fee.study_mode, study_options)
        if years is None:
            # no duration on record: the per-annum figure, unconverted, no derived row
            return fee.amount, None
        total = fee.amount * years
        unit = "year" if years == 1 else "years"
        return total, DerivedFee(
            amount=total, currency=fee.currency, basis=TuitionBasis.PER_PROGRAMME,
            study_mode=fee.study_mode, applicant_scope=fee.applicant_scope,
            source_text=f"derived: {fee.amount} per annum × {years} {unit}",
        )
    if fee.basis is TuitionBasis.PER_CREDIT:
        if not fee.credits:
            return None
        total = fee.amount * fee.credits
        return total, DerivedFee(
            amount=total, currency=fee.currency, basis=TuitionBasis.PER_PROGRAMME,
            study_mode=fee.study_mode, applicant_scope=fee.applicant_scope,
            source_text=f"derived: {fee.amount} per credit × {fee.credits} credits",
        )
    return None                                # per_semester never feeds the headline


def derive_headline_tuition(
    fees: Sequence[FeeRow],
    study_options: Sequence[tuple[StudyMode, Optional[int]]],
) -> HeadlineResult:
    """Pick the coarse tuition from the page's fee rows by fixed priority.

    Scope non_local › all › local (the product's users are non-local
    applicants); mode FullTime › Unknown › PartTime › Hybrid; basis: a stated
    programme total, else per-annum × whole years of the same mode, else
    per-credit × credits. Per-semester rows are never used. Within one
    (scope, mode) cell a stated total beats a derived one.
    """
    for scope in _SCOPE_ORDER:
        for mode in _MODE_ORDER:
            cell = [f for f in fees if f.applicant_scope is scope and f.study_mode is mode]
            if not cell:
                continue
            cell.sort(key=lambda f: (f.basis is not TuitionBasis.PER_PROGRAMME,
                                     f.basis is not TuitionBasis.PER_ANNUM))
            for fee in cell:
                reading = _programme_total(fee, study_options)
                if reading is None:
                    continue
                amount, derived = reading
                return HeadlineResult(amount=amount, currency=fee.currency,
                                      derived=(derived,) if derived else ())
    return _EMPTY
