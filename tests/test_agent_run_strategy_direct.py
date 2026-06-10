"""/agent/run short-circuit: discovery matched → crawl_url direct, no LLM loop."""
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.api.server as server_mod
import src.services.crawl_strategy.discovery as disc_mod
from src.api.schemas import AgentRunRequest
from src.services.crawl_strategy.discovery import DiscoveryResult


def test_agent_run_request_accepts_range():
    req = AgentRunRequest(url="https://x.edu/p", univ_slug="x", year=2026,
                          limit=20)
    assert req.limit == 20 and req.crawl_all is False


def test_agent_run_request_rejects_both():
    with pytest.raises(ValueError):
        AgentRunRequest(url="https://x.edu/p", univ_slug="x", year=2026,
                        limit=5, crawl_all=True)


@pytest.mark.asyncio
async def test_matched_short_circuit_skips_agent_loop(monkeypatch):
    matched = DiscoveryResult(
        matched=True, link_texts={"https://x.edu/a": "A MSc"},
        names_total=1, strategy_used="server×heading_link",
        stopped_reason="exhausted", pages_fetched=1)
    monkeypatch.setattr(
        server_mod, "_probe_strategy_discovery", lambda body: matched)

    crawl_result = MagicMock(imported_count=1)
    crawl_spy = AsyncMock(return_value=crawl_result)
    agent_spy = AsyncMock()
    monkeypatch.setattr(server_mod, "crawl_url", crawl_spy)
    monkeypatch.setattr(server_mod, "run_agent_crawl", agent_spy)

    # autonomous=True required: strategy-direct only fires for autonomous,
    # non-dry-run runs (crawl_url always persists).
    body = AgentRunRequest(url="https://x.edu/p", univ_slug="x", year=2026,
                           page_type_hint="index", limit=10, autonomous=True)
    events = []
    result = await server_mod._execute_agent_job(body, events.append)

    agent_spy.assert_not_awaited()
    assert crawl_spy.await_args.kwargs["discovery"] is matched
    assert result["mode"] == "strategy_direct"
    assert result["strategy_used"] == "server×heading_link"
    assert result["imported_count"] == 1
    assert any(e.get("type") == "strategy_direct_started" for e in events)


@pytest.mark.asyncio
async def test_unmatched_runs_agent_loop_unchanged(monkeypatch):
    monkeypatch.setattr(
        server_mod, "_probe_strategy_discovery",
        lambda body: DiscoveryResult(matched=False))
    crawl_spy = AsyncMock()
    agent_spy = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(server_mod, "crawl_url", crawl_spy)
    monkeypatch.setattr(server_mod, "run_agent_crawl", agent_spy)

    body = AgentRunRequest(url="https://x.edu/p", univ_slug="x", year=2026,
                           page_type_hint="index")
    result = await server_mod._execute_agent_job(body, lambda e: None)

    crawl_spy.assert_not_awaited()
    agent_spy.assert_awaited_once()
    assert result == {"status": "ok"}
    kw = agent_spy.await_args.kwargs
    assert kw == {
        "url": "https://x.edu/p",
        "univ_slug": "x",
        "year": 2026,
        "page_type_hint": "index",
        "runtime_mode": None,
        "autonomous": False,
        "dry_run": False,
        "event_sink": kw["event_sink"],
        "policy_profile": None,
        "auto_paginate": False,
        "max_pages": None,
    }


@pytest.mark.asyncio
async def test_dry_run_or_review_mode_skips_short_circuit(monkeypatch):
    # Even on a strategy-recognized page, dry_run / review mode must use the
    # agent loop (crawl_url always persists — it has no dry-run semantics).
    calls = []
    monkeypatch.setattr(
        disc_mod, "discover_with_default_adapters",
        lambda *a, **k: calls.append(1))
    agent_spy = AsyncMock(return_value={"status": "ok"})
    crawl_spy = AsyncMock()
    monkeypatch.setattr(server_mod, "run_agent_crawl", agent_spy)
    monkeypatch.setattr(server_mod, "crawl_url", crawl_spy)

    for body in (
        AgentRunRequest(url="https://x.edu/p", univ_slug="x", year=2026,
                        page_type_hint="index", autonomous=True, dry_run=True),
        AgentRunRequest(url="https://x.edu/p", univ_slug="x", year=2026,
                        page_type_hint="index", autonomous=False),
    ):
        result = await server_mod._execute_agent_job(body, lambda e: None)
        assert result == {"status": "ok"}
    crawl_spy.assert_not_awaited()
    assert agent_spy.await_count == 2
    assert not calls          # discovery never probed the network
