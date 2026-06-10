# Strategy Discovery → Ingestion Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the crawl-strategy system (accurate names, registry, NUS API, CrawlRange) the discovery layer of the full ingestion pipeline, so `/agent/run`, REST `/crawl`, and CLI `crawl` produce accurate, range-controlled, DB-persisted results.

**Architecture:** A new `discovery.py` wraps `crawl_index()` into `discover_candidates() -> DiscoveryResult`. `crawl_url()` gains `limit`/`crawl_all`/`discovery` params: when the request targets an index page and no URLs were pre-selected, it runs discovery; `matched` → inject `selected_urls`+`selected_link_texts` (the pipeline's existing authoritative-name seam); not matched / any exception → today's scout path byte-for-byte. The `/agent/run` handler probes discovery before the LLM loop: matched → call `crawl_url` directly (deterministic, no LLM orchestration); else → `run_agent_crawl` unchanged.

**Tech Stack:** Python 3.12, pytest (monkeypatch-injected fakes), FastAPI/pydantic schemas, typer CLI.

**Spec:** `docs/superpowers/specs/2026-06-10-strategy-discovery-integration-design.md`

**Branch:** `feat/strategy-discovery-integration` (created; spec committed).

---

### Task 1: `discovery.py` — DiscoveryResult, resolve_crawl_range, discover_candidates

**Files:**
- Create: `src/services/crawl_strategy/discovery.py`
- Test: `tests/test_crawl_strategy/test_discovery.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_crawl_strategy/test_discovery.py`:

```python
import pytest

import src.services.crawl_strategy.discovery as disc
from src.services.crawl_strategy.types import CrawlOutcome, CrawlRange, ExtractItem


def _ok_outcome(items):
    return CrawlOutcome(
        status="ok", university="leeds",
        names=[i.name_en for i in items], items=items,
        names_count=len(items), strategy_used="server×heading_link",
        pages_fetched=2, stopped_reason="reached_limit",
    )


def _fake_fetches():
    return dict(
        server_fetch=lambda u: ("", ""),
        client_fetch=lambda u, **k: ("", ""),
        api_fetch=lambda e, **k: "",
    )


def test_resolve_crawl_range():
    assert disc.resolve_crawl_range(None, False) == CrawlRange.default()
    assert disc.resolve_crawl_range(50, False) == CrawlRange.of(50)
    assert disc.resolve_crawl_range(None, True) == CrawlRange.all_()
    with pytest.raises(ValueError):
        disc.resolve_crawl_range(5, True)


def test_ok_outcome_maps_to_matched(monkeypatch, tmp_path):
    items = [
        ExtractItem("Accounting MSc", "https://x.edu/acc"),
        ExtractItem("Finance MSc", "https://x.edu/fin"),
        ExtractItem("Nameless Programme", None),   # name but no detail link
    ]
    monkeypatch.setattr(disc, "crawl_index", lambda *a, **k: _ok_outcome(items))

    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.of(10),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.matched is True
    assert r.link_texts == {
        "https://x.edu/acc": "Accounting MSc",
        "https://x.edu/fin": "Finance MSc",
    }
    assert r.nameless_count == 1
    assert r.names_total == 3
    assert r.strategy_used == "server×heading_link"
    assert r.stopped_reason == "reached_limit"
    assert r.pages_fetched == 2


def test_ok_but_all_nameless_is_not_matched(monkeypatch, tmp_path):
    items = [ExtractItem("Only Name", None)]
    monkeypatch.setattr(disc, "crawl_index", lambda *a, **k: _ok_outcome(items))
    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.default(),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.matched is False          # nothing to detail-crawl
    assert r.nameless_count == 1


def test_unsupported_maps_to_fallback(monkeypatch, tmp_path):
    out = CrawlOutcome(status="unsupported", university="x",
                       report_zip="/tmp/x.zip", message_for_user="m")
    monkeypatch.setattr(disc, "crawl_index", lambda *a, **k: out)
    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.default(),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.matched is False
    assert r.report_zip == "/tmp/x.zip"


def test_exception_maps_to_fallback(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("network exploded")
    monkeypatch.setattr(disc, "crawl_index", boom)
    r = disc.discover_candidates(
        "https://x.edu/p", CrawlRange.default(),
        report_out=tmp_path, timestamp="t", **_fake_fetches())
    assert r.matched is False
    assert r.report_zip is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_discovery.py -v`
Expected: FAIL (`ModuleNotFoundError: discovery`).

- [ ] **Step 3: Implement**

Create `src/services/crawl_strategy/discovery.py`:

```python
"""Strategy-first candidate discovery for the full ingestion pipeline.

Wraps :func:`crawl_index` into a result the pipeline seam understands:
``{detail_url: authoritative_name}``.  Strategy-first, LLM-scout fallback —
``matched=False`` (unsupported / nothing crawlable / ANY exception) means the
caller proceeds exactly as before this feature existed.  Discovery may only
upgrade a crawl, never break one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from src.services.crawl_strategy.orchestrator import crawl_index
from src.services.crawl_strategy.types import CrawlRange

logger = logging.getLogger(__name__)


def resolve_crawl_range(limit: Optional[int], crawl_all: bool) -> CrawlRange:
    """Map limit/crawl_all surface params to a CrawlRange. Mutually exclusive."""
    if crawl_all and limit is not None:
        raise ValueError("limit and crawl_all are mutually exclusive")
    if crawl_all:
        return CrawlRange.all_()
    if limit is not None:
        return CrawlRange.of(limit)
    return CrawlRange.default()


@dataclass
class DiscoveryResult:
    """Outcome of strategy-first discovery over a programme-index URL."""

    matched: bool
    link_texts: Dict[str, str] = field(default_factory=dict)
    nameless_count: int = 0
    names_total: int = 0
    strategy_used: Optional[str] = None
    stopped_reason: str = ""
    pages_fetched: int = 0
    report_zip: Optional[str] = None


def discover_candidates(
    index_url: str,
    crawl_range: CrawlRange,
    *,
    server_fetch,
    client_fetch,
    api_fetch,
    report_out,
    timestamp: str,
) -> DiscoveryResult:
    """Run the crawl-strategy system; map its outcome onto the pipeline seam.

    ``matched=True`` only when at least one item carries a detail URL —
    items with a name but no URL cannot be detail-crawled and are counted
    in ``nameless_count`` (reported, never persisted as empty records).
    """
    try:
        outcome = crawl_index(
            index_url, crawl_range=crawl_range,
            server_fetch=server_fetch, client_fetch=client_fetch,
            api_fetch=api_fetch, report_out=report_out, timestamp=timestamp)
    except Exception:  # pylint: disable=broad-except
        logger.exception("strategy discovery failed for %s — falling back to scout",
                         index_url)
        return DiscoveryResult(matched=False)

    if outcome.status != "ok":
        return DiscoveryResult(matched=False, report_zip=outcome.report_zip,
                               stopped_reason=outcome.stopped_reason)

    link_texts = {i.detail_url: i.name_en for i in outcome.items if i.detail_url}
    nameless = sum(1 for i in outcome.items if not i.detail_url)
    return DiscoveryResult(
        matched=bool(link_texts),
        link_texts=link_texts,
        nameless_count=nameless,
        names_total=len(outcome.items),
        strategy_used=outcome.strategy_used,
        stopped_reason=outcome.stopped_reason,
        pages_fetched=outcome.pages_fetched,
    )


def discover_with_default_adapters(
    index_url: str, crawl_range: CrawlRange,
) -> DiscoveryResult:
    """Convenience wrapper wiring the real fetch adapters + default report dir."""
    # Imported here so importing discovery stays cheap for unit tests.
    from datetime import datetime, timezone  # pylint: disable=import-outside-toplevel
    from src.core.paths import get_data_dir  # pylint: disable=import-outside-toplevel
    from src.services.crawl_strategy import fetch_adapters  # pylint: disable=import-outside-toplevel

    return discover_candidates(
        index_url, crawl_range,
        server_fetch=fetch_adapters.server_fetch,
        client_fetch=fetch_adapters.client_fetch,
        api_fetch=fetch_adapters.api_fetch,
        report_out=str(get_data_dir() / "reports"),
        timestamp=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_discovery.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: pylint**

Run: `uv run pylint src/services/crawl_strategy/discovery.py tests/test_crawl_strategy/test_discovery.py`
Expected: 10.00/10 exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/services/crawl_strategy/discovery.py tests/test_crawl_strategy/test_discovery.py
git commit -m "feat(discovery): strategy-first candidate discovery with scout-fallback contract"
```

---

### Task 2: `crawl_url` integration — gate, inject, fallback unchanged

**Files:**
- Modify: `src/services/crawler.py` (the `crawl_url` function, ~line 433)
- Test: `tests/test_crawler_discovery.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_crawler_discovery.py`:

```python
"""crawl_url × strategy discovery: inject when matched, byte-identical fallback."""
from unittest.mock import AsyncMock, patch

import pytest

import src.services.crawler as crawler_mod
from src.services.crawl_strategy.discovery import DiscoveryResult


def _matched():
    return DiscoveryResult(
        matched=True,
        link_texts={"https://x.edu/a": "A MSc", "https://x.edu/b": "B MSc"},
        names_total=2, strategy_used="server×heading_link",
        stopped_reason="exhausted", pages_fetched=1)


@pytest.fixture()
def run_new_job_spy(monkeypatch):
    spy = AsyncMock(return_value={"imported_count": 0, "persisted_program_ids": []})
    monkeypatch.setattr(
        crawler_mod.IngestionPipeline, "run_new_job", spy, raising=True)
    # Browser-provider resolution is out of scope here — identity passthrough.
    monkeypatch.setattr(
        crawler_mod.browser_provider_service, "resolve_browser_inputs",
        AsyncMock(return_value={}))
    monkeypatch.setattr(crawler_mod, "_build_review_items", lambda **k: [])
    return spy


@pytest.mark.asyncio
async def test_matched_discovery_injects_selected_urls(run_new_job_spy, monkeypatch):
    monkeypatch.setattr(
        crawler_mod, "discover_with_default_adapters", lambda url, rng: _matched())

    await crawler_mod.crawl_url(
        "https://x.edu/p", "xuni", 2026, page_type_hint="index", limit=10)

    kwargs = run_new_job_spy.call_args.kwargs
    assert sorted(kwargs["selected_urls"]) == ["https://x.edu/a", "https://x.edu/b"]
    assert kwargs["selected_link_texts"] == _matched().link_texts


@pytest.mark.asyncio
async def test_unmatched_discovery_falls_back_unchanged(run_new_job_spy, monkeypatch):
    monkeypatch.setattr(
        crawler_mod, "discover_with_default_adapters",
        lambda url, rng: DiscoveryResult(matched=False))

    await crawler_mod.crawl_url(
        "https://x.edu/p", "xuni", 2026, page_type_hint="index")

    kwargs = run_new_job_spy.call_args.kwargs
    assert kwargs["selected_urls"] is None        # today's scout path, untouched
    assert kwargs["selected_link_texts"] is None


@pytest.mark.asyncio
async def test_no_discovery_when_urls_preselected(run_new_job_spy, monkeypatch):
    def boom(url, rng):
        raise AssertionError("discovery must not run when caller pre-selected URLs")
    monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", boom)

    await crawler_mod.crawl_url(
        "https://x.edu/p", "xuni", 2026, page_type_hint="index",
        selected_urls=["https://x.edu/manual"],
        selected_link_texts={"https://x.edu/manual": "Manual MSc"})

    kwargs = run_new_job_spy.call_args.kwargs
    assert kwargs["selected_urls"] == ["https://x.edu/manual"]


@pytest.mark.asyncio
async def test_no_discovery_for_detail_pages(run_new_job_spy, monkeypatch):
    def boom(url, rng):
        raise AssertionError("discovery must not run for detail pages")
    monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", boom)

    await crawler_mod.crawl_url(
        "https://x.edu/one-course", "xuni", 2026, page_type_hint="detail")
    assert run_new_job_spy.call_args.kwargs["selected_urls"] is None


@pytest.mark.asyncio
async def test_precomputed_discovery_is_used_without_rerun(run_new_job_spy, monkeypatch):
    def boom(url, rng):
        raise AssertionError("must use the precomputed DiscoveryResult")
    monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", boom)

    await crawler_mod.crawl_url(
        "https://x.edu/p", "xuni", 2026, page_type_hint="index",
        discovery=_matched())
    kwargs = run_new_job_spy.call_args.kwargs
    assert kwargs["selected_link_texts"] == _matched().link_texts
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_crawler_discovery.py -v`
Expected: FAIL (`crawl_url` has no `limit`/`discovery` params).

- [ ] **Step 3: Implement**

In `src/services/crawler.py`:

(a) Add imports near the other service imports:

```python
from src.services.crawl_strategy.discovery import (
    DiscoveryResult, discover_with_default_adapters, resolve_crawl_range,
)
```

(b) Add four keyword params to `crawl_url`'s signature (after `selected_link_texts`):

```python
    limit: Optional[int] = None,
    crawl_all: bool = False,
    discovery: Optional[DiscoveryResult] = None,
```

and document them in the docstring Args block:

```
        limit: Crawl only the first N programmes discovered on an index page.
        crawl_all: Crawl every programme discovered (safety-capped upstream).
        discovery: Precomputed DiscoveryResult (e.g. from the /agent/run
            short-circuit) — used as-is, never recomputed.
```

(c) Insert the discovery gate AFTER the `resolve_browser_inputs` block (after
the `if "selected_link_texts" in resolved_browser_inputs:` lines) and BEFORE
`pipeline = IngestionPipeline()`:

```python
    # Strategy-first discovery: known/classifiable index pages get accurate
    # {detail_url: name} candidates from the crawl-strategy system; anything
    # else falls through to today's LLM-scout path untouched.
    if (
        discovery is None
        and page_type_hint == "index"
        and not selected_urls
        and not detail_pages_batch
        and html_content is None
    ):
        crawl_range = resolve_crawl_range(limit, crawl_all)
        discovery = await asyncio.to_thread(
            discover_with_default_adapters, url, crawl_range)
    if discovery is not None and discovery.matched:
        selected_urls = list(discovery.link_texts)
        selected_link_texts = dict(discovery.link_texts)
        logger.info(
            "strategy discovery matched url=%s strategy=%s names=%d nameless=%d "
            "stopped=%s", url, discovery.strategy_used, len(selected_urls),
            discovery.nameless_count, discovery.stopped_reason)
        if progress_callback:
            progress_callback("discovery_matched", {
                "strategy_used": discovery.strategy_used,
                "names_count": len(selected_urls),
                "nameless_count": discovery.nameless_count,
                "stopped_reason": discovery.stopped_reason,
                "pages_fetched": discovery.pages_fetched,
            })
```

Check the module already imports `asyncio` (it almost certainly does for the
async flows; add `import asyncio` at the top if not). `logger` already exists
in the module.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_crawler_discovery.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Regression — the existing crawler tests still pass**

Run: `uv run pytest tests/ -q -k "crawler or ingestion" 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 6: pylint**

Run: `uv run pylint src/services/crawler.py tests/test_crawler_discovery.py`
Expected: 10.00/10 exit 0. (If `crawl_url` trips `too-many-arguments`-style
counts it will already have a pragma or config allowance — it had 24 params
before this change; if a NEW message appears, report DONE_WITH_CONCERNS.)

- [ ] **Step 7: Commit**

```bash
git add src/services/crawler.py tests/test_crawler_discovery.py
git commit -m "feat(crawler): strategy-first discovery gate in crawl_url with scout fallback"
```

---

### Task 3: Surface params — REST `/crawl` schema + CLI `crawl --limit/--all`

**Files:**
- Modify: `src/api/schemas.py` (CrawlRequest), `src/api/server.py` (the `/crawl` endpoint's `crawl_url` call), `src/cmd/cli.py` (the `crawl` command)
- Test: `tests/test_crawl_request_range.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_crawl_request_range.py`:

```python
import pytest

from src.api.schemas import CrawlRequest


def _base(**kw):
    return dict(url="https://x.edu/p", univ_slug="xuni", year=2026, **kw)


def test_crawl_request_accepts_limit():
    assert CrawlRequest(**_base(limit=50)).limit == 50


def test_crawl_request_accepts_crawl_all():
    assert CrawlRequest(**_base(crawl_all=True)).crawl_all is True


def test_crawl_request_defaults():
    req = CrawlRequest(**_base())
    assert req.limit is None and req.crawl_all is False


def test_crawl_request_rejects_both():
    with pytest.raises(ValueError):
        CrawlRequest(**_base(limit=5, crawl_all=True))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_crawl_request_range.py -v`
Expected: FAIL (no `limit` field).

- [ ] **Step 3: Implement**

(a) `src/api/schemas.py` — add to `CrawlRequest` (after `page_type_hint`),
plus a model validator:

```python
    limit: Optional[int] = Field(
        default=None, ge=1,
        description="Crawl only the first N programmes discovered on the index page",
    )
    crawl_all: bool = Field(
        default=False,
        description="Crawl every discovered programme (safety-capped)",
    )

    @model_validator(mode="after")
    def _limit_xor_all(self) -> "CrawlRequest":
        if self.crawl_all and self.limit is not None:
            raise ValueError("limit and crawl_all are mutually exclusive")
        return self
```

(`model_validator` — extend the existing `from pydantic import ...` line if
not already imported.)

(b) `src/api/server.py` — in the `/crawl` endpoint handler, where `crawl_url`
is called with `selected_urls=body.selected_urls, ...`, add:

```python
                    limit=body.limit,
                    crawl_all=body.crawl_all,
```

(c) `src/cmd/cli.py` — the `crawl` command gains two options (place among its
existing `typer.Option` params):

```python
    limit: Optional[int] = typer.Option(
        None, "--limit", help="只爬取 index 页发现的前 N 门课程（含详情入库）。"),
    crawl_all: bool = typer.Option(
        False, "--all", help="爬取发现的全部课程（有安全上限）。"),
```

and pass them through in its `crawl_url(...)` invocation:

```python
        limit=limit,
        crawl_all=crawl_all,
```

(The CLI invokes `crawl_url` via `asyncio.run`/wrapper — read the function body
and add the kwargs to that exact call. Validation happens inside
`resolve_crawl_range`; let its `ValueError` surface.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_crawl_request_range.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: CLI smoke**

Run: `uv run python -m src.cmd.cli crawl --help | grep -E "limit|all"`
Expected: both flags listed.

- [ ] **Step 6: pylint + regression**

Run: `uv run pylint src/api/schemas.py src/api/server.py src/cmd/cli.py tests/test_crawl_request_range.py`
Expected: 10.00/10 exit 0.
Run: `uv run pytest tests/ -q -k "schema or api or cli" 2>&1 | tail -2`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/api/schemas.py src/api/server.py src/cmd/cli.py tests/test_crawl_request_range.py
git commit -m "feat(surface): limit/crawl_all range params on /crawl and CLI crawl"
```

---

### Task 4: `/agent/run` short-circuit — strategy-direct before the LLM loop

**Files:**
- Modify: `src/api/schemas.py` (AgentRunRequest), `src/api/server.py` (`api_agent_run`'s `_run_agent_job`)
- Test: `tests/test_agent_run_strategy_direct.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_run_strategy_direct.py`:

```python
"""/agent/run short-circuit: discovery matched → crawl_url direct, no LLM loop."""
from unittest.mock import AsyncMock

import pytest

import src.api.server as server_mod
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

    crawl_spy = AsyncMock(return_value=server_mod.CrawlResult(
        imported_count=1, university="x", year=2026))
    agent_spy = AsyncMock()
    monkeypatch.setattr(server_mod.crawler_service, "crawl_url", crawl_spy)
    monkeypatch.setattr(server_mod, "run_agent_crawl", agent_spy)

    body = AgentRunRequest(url="https://x.edu/p", univ_slug="x", year=2026,
                           page_type_hint="index", limit=10)
    result = await server_mod._execute_agent_job(body, lambda e: None)

    agent_spy.assert_not_awaited()
    assert crawl_spy.await_args.kwargs["discovery"] is matched
    assert result["mode"] == "strategy_direct"
    assert result["strategy_used"] == "server×heading_link"


@pytest.mark.asyncio
async def test_unmatched_runs_agent_loop_unchanged(monkeypatch):
    monkeypatch.setattr(
        server_mod, "_probe_strategy_discovery",
        lambda body: DiscoveryResult(matched=False))
    crawl_spy = AsyncMock()
    agent_spy = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(server_mod.crawler_service, "crawl_url", crawl_spy)
    monkeypatch.setattr(server_mod, "run_agent_crawl", agent_spy)

    body = AgentRunRequest(url="https://x.edu/p", univ_slug="x", year=2026,
                           page_type_hint="index")
    result = await server_mod._execute_agent_job(body, lambda e: None)

    crawl_spy.assert_not_awaited()
    agent_spy.assert_awaited_once()
    assert result == {"status": "ok"}
```

(`CrawlResult` import path: it's whatever `crawler_service.crawl_url` returns —
check `src/services/crawler.py`'s return type and import the same model in the
test, adjusting the constructor kwargs to its actual required fields. If
constructing it is awkward, return a `MagicMock` with the attributes
`_execute_agent_job` reads instead.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent_run_strategy_direct.py -v`
Expected: FAIL (no `limit` on AgentRunRequest; no `_execute_agent_job`/`_probe_strategy_discovery`).

- [ ] **Step 3: Implement**

(a) `src/api/schemas.py` — add the same `limit`/`crawl_all` fields + validator
to `AgentRunRequest` (copy the exact block from Task 3(a), with the validator
method renamed `_agent_limit_xor_all` and return type `"AgentRunRequest"`).

(b) `src/api/server.py` — two additions:

A module-level probe helper (near the agent-run handler):

```python
def _probe_strategy_discovery(body: AgentRunRequest):
    """Probe strategy-first discovery for an /agent/run index request.

    Returns a DiscoveryResult, or one with matched=False on any failure —
    the agent loop is the universal fallback.
    """
    from src.services.crawl_strategy.discovery import (  # pylint: disable=import-outside-toplevel
        DiscoveryResult, discover_with_default_adapters, resolve_crawl_range,
    )
    if body.page_type_hint != "index":
        return DiscoveryResult(matched=False)
    try:
        rng = resolve_crawl_range(body.limit, body.crawl_all)
        return discover_with_default_adapters(body.url, rng)
    except Exception:  # pylint: disable=broad-except
        logger.exception("agent-run discovery probe failed; using agent loop")
        return DiscoveryResult(matched=False)
```

Extract the body of `_run_agent_job`'s `try:` block into a testable coroutine
`_execute_agent_job(body, event_sink) -> dict` and add the branch:

```python
async def _execute_agent_job(body: AgentRunRequest, event_sink) -> dict:
    """Run one agent job: strategy-direct when discovery matches, else LLM loop."""
    discovery = await asyncio.to_thread(_probe_strategy_discovery, body)
    if discovery.matched:
        event_sink({"type": "strategy_direct_started",
                    "strategy_used": discovery.strategy_used,
                    "names_count": len(discovery.link_texts),
                    "stopped_reason": discovery.stopped_reason})
        crawl_result = await crawler_service.crawl_url(
            url=body.url, univ_slug=body.univ_slug, year=body.year,
            page_type_hint=body.page_type_hint,
            discovery=discovery,
            progress_callback=lambda ev, payload: event_sink(
                {"type": ev, **payload}),
        )
        return {
            "mode": "strategy_direct",
            "status": "ok",
            "strategy_used": discovery.strategy_used,
            "stopped_reason": discovery.stopped_reason,
            "names_discovered": discovery.names_total,
            "nameless_count": discovery.nameless_count,
            "imported_count": getattr(crawl_result, "imported_count", 0),
        }
    return await run_agent_crawl(
        url=body.url, univ_slug=body.univ_slug, year=body.year,
        page_type_hint=body.page_type_hint, runtime_mode=body.runtime,
        autonomous=body.autonomous, dry_run=body.dry_run,
        event_sink=event_sink,
        policy_profile=(body.policy_profile.model_dump(exclude_none=True)
                        if body.policy_profile else None),
        auto_paginate=body.auto_paginate, max_pages=body.max_pages,
    )
```

Then `_run_agent_job`'s try block becomes
`result = await _execute_agent_job(body, _event_sink)` (keeping the existing
`program_count`/`tokens_used` post-shaping and error handling exactly as-is).
Check what name the module imports the crawler under (it may already import
`crawl_url` directly or the module as a service alias) — match the existing
import style; the test refers to `server_mod.crawler_service.crawl_url`, so
expose it as `from src.services import crawler as crawler_service` if the
module doesn't already have an equivalent alias (and update existing call
sites only if trivially consistent — otherwise adapt the test's patch target
to the real import name and keep the module untouched).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_agent_run_strategy_direct.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: pylint + API regression**

Run: `uv run pylint src/api/server.py src/api/schemas.py tests/test_agent_run_strategy_direct.py`
Expected: 10.00/10 exit 0.
Run: `uv run pytest tests/ -q -k "agent or server or api" 2>&1 | tail -2`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/api/server.py src/api/schemas.py tests/test_agent_run_strategy_direct.py
git commit -m "feat(agent-run): strategy-direct short-circuit before the LLM loop"
```

---

### Task 5: Skill rewrite — user-level flow first

**Files:**
- Modify: `skills/uni-admission-crawl/SKILL.md`
- Test: `tests/test_crawl_strategy/test_skill_decision_table.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crawl_strategy/test_skill_decision_table.py`:

```python
def test_skill_documents_range_on_full_crawl():
    text = SKILL.read_text(encoding="utf-8")
    assert "crawl_all" in text          # /agent/run range params
    assert '"limit"' in text
    assert "strategy_direct" in text    # the short-circuit mode is explained
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_skill_decision_table.py -v -k full_crawl`
Expected: FAIL.

- [ ] **Step 3: Rewrite the crawl skill's entry flow**

In `skills/uni-admission-crawl/SKILL.md`, replace the "Step 1 — Determine
crawl mode" table's `paginate` row and the §3.2 paginated example so the
primary index flow carries the range, and add a short mode note. Concretely:

(a) In the Step 1 table, replace the `paginate` row with:

```markdown
| `index` (full) | URL is a program-list page; user wants programmes **in the DB / web UI** | REST `POST /agent/run` with `"limit": N` or `"crawl_all": true` |
```

(b) Replace the §3.2 request body example with:

```markdown
```bash
curl -sS -X POST http://127.0.0.1:8910/agent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "<INDEX_URL>",
    "univ_slug": "<SLUG>",
    "year": <YEAR>,
    "page_type_hint": "index",
    "limit": <N>            # or "crawl_all": true for everything
  }'
```

Range semantics: `limit` = first N programmes; `crawl_all` = every programme
(safety-capped); omit both = first batch (≤30). Each discovered programme is
one detail-page crawl + LLM extraction — **quote the cost in programme count
before launching** (`crawl_all` on a large catalogue can be hundreds of pages):

> 预计爬取 ~N 门课程的详情页（≈N 次 LLM 抽取）。开始吗？

The response carries `mode`. `mode: "strategy_direct"` means a known/
classified university was crawled deterministically (accurate names, no LLM
index analysis — cheaper and more reliable). Any other mode means the agent
LLM loop handled it (unknown layout). Either way results land in the DB and
the web UI — relay the same completion message.
```

(c) Keep the legacy `auto_paginate`/`max_pages` documentation as a fallback
note ("for sites the strategy system doesn't recognize, the agent loop's
pagination still honours `auto_paginate`/`max_pages`").

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_crawl_strategy/test_skill_decision_table.py -v`
Expected: PASS (all, including the existing `--limit`/`stopped_reason` token
assertions — do NOT remove the crawl-index section tokens).

- [ ] **Step 5: Commit**

```bash
git add skills/uni-admission-crawl/SKILL.md tests/test_crawl_strategy/test_skill_decision_table.py
git commit -m "docs(skill): user-level full-crawl flow with limit/crawl_all range"
```

---

### Task 6: Full gate + live acceptance

**Files:** none (verification; fixes only if found)

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 2: CI lint gate**

Run: `uv run pylint $(git ls-files '*.py')`
Expected: 10.00/10, exit 0.

- [ ] **Step 3: Live — Leeds end-to-end (needs network + LLM keys + server)**

```bash
uv run python -m src.cmd.cli crawl --name leeds --year 2026 \
  --url 'https://courses.leeds.ac.uk/course-search/masters-courses' \
  --page-type index --limit 5
```

Expected: log shows `strategy discovery matched ... strategy=server×heading_link
names=5`; then the pipeline crawls 5 detail pages and reports
`5 programs imported` (or fewer with quarantine reasons). Verify in DB:
`uv run python -m src.cmd.cli export --name leeds --year 2026 --output /tmp/leeds.xlsx`
→ file contains the 5 accurately-named programmes with detail fields.

- [ ] **Step 4: Live — NUS via API discovery**

```bash
uv run python -m src.cmd.cli crawl --name nus --year 2026 \
  --url 'https://study.nus.edu.sg/programme' --page-type index --limit 5
```

Expected: discovery log shows `strategy=api×json_api names=5`; 5 NUS detail
pages crawled and imported.

- [ ] **Step 5: Live — unknown university falls back**

Pick any university index URL not in the registry whose layout the classifier
won't match (or temporarily use a nav-only page). Expected: discovery logs the
fallback, the scout path runs exactly as before this feature (no crash, no
behaviour change).

- [ ] **Step 6: Commit any acceptance fixes**

```bash
git add -A && git commit -m "fix(discovery): corrections from live acceptance"
```

(Skip if acceptance passed clean.)

---

## Notes for the implementer

- **TDD discipline**: every code task is test-first; run the failing test
  before implementing.
- **pylint gate**: `uv run pylint $(git ls-files '*.py')` fails on ANY message.
  Heavy imports go inside functions (the codebase pattern); use `del` for
  interface-only args.
- **The fallback is sacred**: when discovery doesn't match, every call into
  `run_new_job` / `run_agent_crawl` must carry exactly the arguments it
  carries today. The regression tests in Tasks 2/4 pin this — do not weaken
  them to make an implementation pass.
- **Async**: `discover_with_default_adapters` is blocking (network) — always
  call via `asyncio.to_thread` from async contexts.
- **Read before editing**: Tasks 3/4 touch large existing files (`server.py`,
  `cli.py`); read the surrounding function fully and match its style. Where
  this plan says "check the existing import name / call site", do that check
  first and adapt the patch target accordingly.
