"""Tests for src/scrapers/scout.py – scout_links, run_scout, print_scout_report.

All LLM calls are mocked.
"""

import json
from typing import Set
from unittest.mock import MagicMock, patch

import pytest

from src.models.scraper_models import CrawlPageResult, ScoutedLink, ScoutedLinks
from src.scrapers.scout import (
    MAX_SCOUT_CALLS,
    print_scout_report,
    run_scout,
    scout_links,
)


# ── helpers ──────────────────────────────────────────────────────────

def _make_router_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


def _scouted_json(links: list[dict]) -> str:
    return ScoutedLinks(links=[ScoutedLink(**l) for l in links]).model_dump_json()


# ── scout_links ──────────────────────────────────────────────────────

def test_scout_links_returns_candidates() -> None:
    router = MagicMock()
    payload = _scouted_json([
        {"url": "https://example.com/a", "reason": "tuition page", "confidence": "high"},
    ])
    router.generate.return_value = _make_router_response(payload)

    with patch("src.scrapers.scout.load_prompt", return_value="<prompt>{base_url}{markdown_summary}{link_list}"):
        links, count = scout_links(
            router, "# Page", ["https://example.com/a"], "https://example.com", 0,
        )

    assert len(links) == 1
    assert links[0].url == "https://example.com/a"
    assert count == 1


def test_scout_links_resolve_relative_urls() -> None:
    router = MagicMock()
    payload = _scouted_json([
        {"url": "/relative/path", "reason": "good page", "confidence": "medium"},
    ])
    router.generate.return_value = _make_router_response(payload)

    with patch("src.scrapers.scout.load_prompt", return_value="{base_url}{markdown_summary}{link_list}"):
        links, count = scout_links(
            router, "md", ["/relative/path"], "https://base.com/page", 0,
        )

    assert links[0].url.startswith("https://base.com/")
    assert count == 1


def test_scout_links_at_limit() -> None:
    router = MagicMock()
    links, count = scout_links(router, "md", ["u"], "base", MAX_SCOUT_CALLS)
    assert links == []
    assert count == MAX_SCOUT_CALLS
    router.generate.assert_not_called()


def test_scout_links_empty_response() -> None:
    router = MagicMock()
    resp = MagicMock()
    resp.text = ""
    router.generate.return_value = resp

    with patch("src.scrapers.scout.load_prompt", return_value="{base_url}{markdown_summary}{link_list}"):
        links, count = scout_links(router, "md", ["u"], "base", 0)

    assert links == []
    assert count == 1


def test_scout_links_llm_exception() -> None:
    router = MagicMock()
    router.generate.side_effect = RuntimeError("LLM down")

    with patch("src.scrapers.scout.load_prompt", return_value="{base_url}{markdown_summary}{link_list}"):
        links, count = scout_links(router, "md", ["u"], "base", 0)

    assert links == []
    assert count == 1


def test_scout_links_truncates_inputs() -> None:
    """Links capped at 50, markdown at 3000 chars."""
    router = MagicMock()
    resp = MagicMock()
    resp.text = ""
    router.generate.return_value = resp

    many_links = [f"https://example.com/{i}" for i in range(100)]
    long_md = "x" * 5000

    with patch("src.scrapers.scout.load_prompt", return_value="{base_url}{markdown_summary}{link_list}") as mock_load:
        scout_links(router, long_md, many_links, "base", 0)

    call_args = router.generate.call_args[0][0]
    link_lines = [l for l in call_args.split("\n") if l.startswith("- ")]
    assert len(link_lines) <= 50


# ── run_scout ────────────────────────────────────────────────────────

def test_run_scout_collects_deeper_urls() -> None:
    router = MagicMock()
    page = CrawlPageResult(
        url="https://example.com/fail",
        markdown="no data",
        char_count=7,
        links=["https://example.com/deeper"],
    )

    scouted_link = ScoutedLink(
        url="https://example.com/deeper", reason="tuition data", confidence="high",
    )

    with patch("src.scrapers.scout.scout_links", return_value=([scouted_link], 1)):
        deeper, count, all_scouted = run_scout(
            router, [page], set(), 0, [],
        )

    assert deeper == ["https://example.com/deeper"]
    assert count == 1
    assert len(all_scouted) == 1


def test_run_scout_filters_visited() -> None:
    router = MagicMock()
    page = CrawlPageResult(
        url="https://example.com/fail",
        markdown="no data",
        char_count=7,
        links=["https://example.com/visited"],
    )
    scouted_link = ScoutedLink(
        url="https://example.com/visited", reason="already seen", confidence="high",
    )

    visited: Set[str] = {"https://example.com/visited"}

    with patch("src.scrapers.scout.scout_links", return_value=([scouted_link], 1)):
        deeper, count, all_scouted = run_scout(
            router, [page], visited, 0, [],
        )

    assert deeper == []  # Filtered out
    assert len(all_scouted) == 1


def test_run_scout_respects_limit() -> None:
    """Should stop scouting once limit is reached."""
    router = MagicMock()
    pages = [
        CrawlPageResult(url=f"https://example.com/{i}", markdown="md", char_count=2, links=[])
        for i in range(3)
    ]

    deeper, count, all_scouted = run_scout(
        router, pages, set(), MAX_SCOUT_CALLS, [],
    )

    assert deeper == []
    assert count == MAX_SCOUT_CALLS


# ── print_scout_report ───────────────────────────────────────────────

def test_print_scout_report_basic(capsys) -> None:
    print_scout_report(
        univ_slug="hku",
        year=2025,
        depth_reached=2,
        imported=5,
        visited_urls={"https://a.com", "https://b.com"},
        failed_urls=["https://c.com"],
        all_scouted_links=[],
    )
    out = capsys.readouterr().out
    assert "hku" in out
    assert "2025" in out
    assert "5" in out   # programs found
    assert "Failed" in out


def test_print_scout_report_with_unexplored(capsys) -> None:
    sl = ScoutedLink(url="https://x.com/new", reason="potential", confidence="high")
    print_scout_report(
        univ_slug="cuhk", year=2025, depth_reached=1, imported=0,
        visited_urls={"https://x.com/old"},
        failed_urls=[],
        all_scouted_links=[sl],
    )
    out = capsys.readouterr().out
    assert "Unexplored" in out
    assert "https://x.com/new" in out


def test_print_scout_report_all_explored(capsys) -> None:
    sl = ScoutedLink(url="https://x.com/visited", reason="done", confidence="medium")
    print_scout_report(
        univ_slug="ust", year=2025, depth_reached=3, imported=10,
        visited_urls={"https://x.com/visited"},
        failed_urls=[],
        all_scouted_links=[sl],
    )
    out = capsys.readouterr().out
    assert "All scouted links were explored" in out
