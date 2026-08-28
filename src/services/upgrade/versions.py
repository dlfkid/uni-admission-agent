"""Semantic version comparison for upgrade decisions (spec §4).

Replaces a string comparison that reported ``v0.10.0 < v0.9.0`` and
permanently pinned every 0.8.x/0.9.x install (spec §1.1).
"""
from __future__ import annotations

from packaging.version import InvalidVersion, Version

from src.services.upgrade.types import UnparseableVersionError


def parse_tag(tag: str) -> Version | None:
    """Parse a ``vX.Y.Z``-style git tag. Return ``None`` if uninterpretable."""
    if not tag:
        return None
    candidate = tag[1:] if tag[0] in ("v", "V") else tag
    try:
        return Version(candidate)
    except InvalidVersion:
        return None


def is_newer(current: str, latest: str) -> bool:
    """Return ``True`` when *latest* supersedes *current*.

    Raises :class:`UnparseableVersionError` when either side is
    uninterpretable — never guesses, never crashes with a bare exception.
    """
    parsed_current = parse_tag(current)
    parsed_latest = parse_tag(latest)
    if parsed_current is None or parsed_latest is None:
        raise UnparseableVersionError(current=current, latest=latest)
    return parsed_latest > parsed_current
