"""Tests for semantic version comparison — spec §4."""

import pytest

from src.services.upgrade.types import UnparseableVersionError
from src.services.upgrade.versions import is_newer, parse_tag


# ── parse_tag ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v0.10.0", "0.10.0"),
        ("0.10.0", "0.10.0"),
        ("v0.7.5-alpha", "0.7.5a0"),
        ("v0.0.0-dev", "0.0.0.dev0"),
    ],
)
def test_parse_tag_accepts_every_historical_shape(tag: str, expected: str) -> None:
    """Every tag shape this repo has ever published must parse."""
    parsed = parse_tag(tag)
    assert parsed is not None
    assert str(parsed) == expected


@pytest.mark.parametrize("tag", ["", "latest", "v", "not-a-version"])
def test_parse_tag_returns_none_for_garbage(tag: str) -> None:
    assert parse_tag(tag) is None


# ── is_newer ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current,latest,expected",
    [
        # THE regression guard: string comparison said False here (spec §1.1).
        ("v0.9.0", "v0.10.0", True),
        ("v0.8.0", "v0.10.0", True),
        ("v0.10.0", "v0.9.1", False),
        ("v0.10.0", "v0.10.0", False),
        ("v0.10.0", "v0.11.0", True),
        ("v0.7.5-alpha", "v0.8.0", True),
        ("v1.0.0-alpha", "v1.0.0", True),
        ("v1.0.0", "v1.0.0-rc1", False),
        ("v0.0.0-dev", "v0.10.0", True),
    ],
)
def test_is_newer_orders_semantically(current: str, latest: str, expected: bool) -> None:
    assert is_newer(current, latest) is expected


@pytest.mark.parametrize(
    "current,latest",
    [("v0.10.0", "garbage"), ("garbage", "v0.10.0"), ("", "")],
)
def test_is_newer_raises_on_unparseable(current: str, latest: str) -> None:
    """Never crash, never guess — raise a typed error carrying both raws."""
    with pytest.raises(UnparseableVersionError) as exc:
        is_newer(current, latest)
    assert exc.value.current == current
    assert exc.value.latest == latest
