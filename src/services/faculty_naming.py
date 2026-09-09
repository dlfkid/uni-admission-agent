"""Join a bare academic-unit name to the prefixed form its siblings use.

``Program.faculty`` is stored as extraction returned it, and one CUHK crawl
returned both 'Faculty of Social Science' (27 programmes) and a bare 'Social
Science' (2) for the same unit — enough to split it in two wherever the field
is grouped or filtered on.

The rule is deliberately narrow: a value with no unit prefix adopts a sibling
that is exactly ``<unit> of <value>``, and only when exactly one sibling
qualifies. It never adds a prefix of its own invention — which unit word is
right belongs to the university, not to English ('School of Energy and
Environment' at CityU, 'College of Science and Engineering' at Edinburgh,
'Graduate School' at EdUHK) — and it never touches a value that already
carries a prefix, so HKU's joint 'Faculty of Arts / Faculty of Law' cannot
swallow the plain 'Faculty of Arts'.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# The unit words seen across the corpus. Kept as a closed list rather than a
# generic "<word> of <rest>" pattern so a programme name that happens to read
# that way ("Master of Architecture") can never be treated as a unit.
_UNIT_WORDS = (
    "Faculty",
    "School",
    "College",
    "Academy",
    "Division",
    "Institute",
    "Department",
)

_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:" + "|".join(_UNIT_WORDS) + r")\s+of\s+(?:the\s+)?(?P<rest>.+)$",
    re.IGNORECASE,
)


def _squash(text: str) -> str:
    """Fold case and runs of whitespace so spelling variants compare equal."""
    return " ".join(text.split()).casefold()


def canonicalize_faculty(value: Optional[str], known: Iterable[str]) -> Optional[str]:
    """Return *value*, or the one sibling in *known* that prefixes it.

    Args:
        value: The faculty as extracted; returned unchanged when empty, when
            it already carries a unit prefix, or when no single sibling
            matches.
        known: Faculty values seen elsewhere in the same batch.

    Returns:
        The sibling's spelling when exactly one sibling reads
        ``<unit> of <value>``; otherwise *value* untouched.
    """
    if not value or not value.strip():
        return value
    if _PREFIX_RE.match(value.strip()):
        # Already prefixed: not a candidate, and never a lengthening target.
        return value

    wanted = _squash(value)
    matches = {
        candidate
        for candidate in known
        if (m := _PREFIX_RE.match(str(candidate or "").strip()))
        and _squash(m.group("rest")) == wanted
    }
    if len(matches) == 1:
        return next(iter(matches))
    # Zero matches: nothing to join to. More than one: two units could claim
    # the name, and choosing between them would be a guess.
    return value
