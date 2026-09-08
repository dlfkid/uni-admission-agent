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
import re
from typing import Optional

from src.models.admission import TuitionScope

logger = logging.getLogger(__name__)

# Explicit "both" wordings win before any single-scope keyword is looked for:
# PolyU writes "for local and non-local students" under one figure.
_BOTH_RE = re.compile(r"\b(local|home|uk)\b.{0,12}\b(and|&|/)\b.{0,12}\b(non-?local|international|overseas)\b", re.I)
_NON_LOCAL_RE = re.compile(r"non-?local|international|overseas|\bEU\b|非本地|國際|国际", re.I)
_LOCAL_RE = re.compile(r"\blocal\b|\bhome\b|\bUK\b|domestic|本地", re.I)
_UK_EU_RE = re.compile(r"\bUK\s*(/|and|&)\s*EU\b", re.I)


def normalize_applicant_scope(label: Optional[str]) -> TuitionScope:
    """Map the page's applicant wording onto the three filterable values.

    Order matters: an explicit both-scopes phrase is ALL; the pre-Brexit
    "UK/EU" pairing is one home band (LOCAL); any non-local keyword is
    NON_LOCAL (bare "EU" included — UK pages now price EU with International);
    any local keyword is LOCAL. Anything else is ALL with a warning so the
    vocabulary can be extended.
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
    logger.warning("Unrecognised tuition applicant wording %r — stored as scope=all", text)
    return TuitionScope.ALL
