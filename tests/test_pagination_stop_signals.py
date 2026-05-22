"""Tests for cheap deterministic pagination stop signals.

These run BEFORE LLM extraction on each new page, so a drifted /
exhausted crawl is stopped without spending tokens.

Two signals in this PR:
  - extract_url_pattern + urls_diverged: detect when the next page's
    URL no longer matches the index page's URL shape.
  - should_stop_for_decreasing_yield: detect when program output per
    page has collapsed (exhaustion).
"""
from __future__ import annotations

import pytest

from src.scrapers.pagination_signals import (
    extract_url_pattern,
    should_stop_for_decreasing_yield,
    urls_diverged,
)


# ---------------------------------------------------------------------------
# URL pattern extraction
# ---------------------------------------------------------------------------


class TestExtractUrlPattern:
    def test_strips_query_string(self) -> None:
        assert extract_url_pattern("https://e.edu/programs?page=1") == \
            extract_url_pattern("https://e.edu/programs?page=2")

    def test_strips_fragment(self) -> None:
        assert extract_url_pattern("https://e.edu/programs#a") == \
            extract_url_pattern("https://e.edu/programs#b")

    def test_replaces_numeric_path_segments_with_placeholder(self) -> None:
        # /programs/page/2 and /programs/page/3 should normalize to same pattern.
        p1 = extract_url_pattern("https://e.edu/programs/page/2")
        p2 = extract_url_pattern("https://e.edu/programs/page/3")
        assert p1 == p2

    def test_keeps_alpha_path_segments(self) -> None:
        # /programs and /about should NOT normalize to same pattern.
        assert extract_url_pattern("https://e.edu/programs") != \
            extract_url_pattern("https://e.edu/about")

    def test_normalizes_trailing_slash(self) -> None:
        assert extract_url_pattern("https://e.edu/programs/") == \
            extract_url_pattern("https://e.edu/programs")

    def test_includes_host_in_pattern(self) -> None:
        # Same path on different hosts is NOT the same pattern.
        assert extract_url_pattern("https://a.edu/programs") != \
            extract_url_pattern("https://b.edu/programs")


# ---------------------------------------------------------------------------
# urls_diverged
# ---------------------------------------------------------------------------


class TestUrlsDiverged:
    def test_same_pattern_pagination_does_not_diverge(self) -> None:
        # Classic pagination: ?page=1 → ?page=2
        assert urls_diverged(
            "https://e.edu/programs?page=1",
            "https://e.edu/programs?page=2",
        ) is False

    def test_path_pagination_does_not_diverge(self) -> None:
        # /programs/page/1 → /programs/page/2
        assert urls_diverged(
            "https://e.edu/programs/page/1",
            "https://e.edu/programs/page/2",
        ) is False

    def test_drifted_to_unrelated_path_diverges(self) -> None:
        # AI clicked into a totally different section.
        assert urls_diverged(
            "https://e.edu/programs?page=5",
            "https://e.edu/about-us",
        ) is True

    def test_drifted_to_sibling_section_diverges(self) -> None:
        # /programs → /faculty changes the meaningful path.
        assert urls_diverged(
            "https://e.edu/programs/page/2",
            "https://e.edu/faculty/page/3",
        ) is True

    def test_cross_host_diverges(self) -> None:
        # Followed an external link.
        assert urls_diverged(
            "https://e.edu/programs?page=2",
            "https://other.edu/programs?page=3",
        ) is True

    def test_identical_urls_do_not_diverge(self) -> None:
        # Same URL twice (caller will detect loop separately).
        assert urls_diverged(
            "https://e.edu/programs?page=2",
            "https://e.edu/programs?page=2",
        ) is False


# ---------------------------------------------------------------------------
# Decreasing yield detector
# ---------------------------------------------------------------------------


class TestDecreasingYield:
    def test_no_stop_with_too_few_data_points(self) -> None:
        """Need at least 3 pages of history before judging trend."""
        assert should_stop_for_decreasing_yield([]) is False
        assert should_stop_for_decreasing_yield([10]) is False
        assert should_stop_for_decreasing_yield([10, 8]) is False

    def test_no_stop_when_stable(self) -> None:
        assert should_stop_for_decreasing_yield([10, 10, 10]) is False
        assert should_stop_for_decreasing_yield([10, 9, 11, 10]) is False

    def test_no_stop_when_increasing(self) -> None:
        assert should_stop_for_decreasing_yield([5, 10, 15]) is False

    def test_no_stop_on_single_low_dip(self) -> None:
        """One bad page shouldn't trigger stop — could be a layout glitch."""
        assert should_stop_for_decreasing_yield([10, 10, 0]) is False

    def test_stop_when_latest_far_below_average(self) -> None:
        """Latest page yield < 20% of historical average AND yield is
        in a clear downward trend across the window → stop."""
        # Avg of [10, 10, 10] = 10. Latest 1 = 10% of avg.
        assert should_stop_for_decreasing_yield([10, 10, 10, 1]) is True

    def test_stop_when_all_recent_are_zero(self) -> None:
        """Two consecutive empty pages = exhausted."""
        # Avg of [5, 5] = 5. Latest 0 = 0% of avg.
        assert should_stop_for_decreasing_yield([5, 5, 0, 0]) is True

    def test_no_stop_when_yield_recovered(self) -> None:
        """Dip then recovery → not exhausted."""
        assert should_stop_for_decreasing_yield([10, 10, 2, 10]) is False
