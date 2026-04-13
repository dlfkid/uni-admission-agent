#!/usr/bin/env python3
"""E2E Agent Smoke Test.

Exercises the full agent runtime (pydanticai mode) against golden-sample
universities. Starts a real uvicorn server + browser client, then runs
detail + index page crawls in dry-run mode and outputs results for human
quality judgment.

Usage:
    # Random university, detail + index tests
    uv run python scripts/e2e_agent_smoke.py

    # Specific university
    uv run python scripts/e2e_agent_smoke.py --case edinburgh

    # Pagination test (auto-selects a paginated university, crawls 2 pages)
    uv run python scripts/e2e_agent_smoke.py --paginate

    # Pagination-only test on a specific university
    uv run python scripts/e2e_agent_smoke.py --paginate --paginate-case edinburgh --skip-detail --skip-index

    # List available cases
    uv run python scripts/e2e_agent_smoke.py --case nonexistent
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_CASES_DIR = PROJECT_ROOT / "golden_samples" / "cases"
E2E_RESULTS_DIR = PROJECT_ROOT / "e2e_results"
POLL_INTERVAL = 3  # seconds
TIMEOUT = 900  # 15 minutes per test (matches PAGE_TIMEOUT)
CLIENT_READY_TIMEOUT = 30  # seconds to wait for client registration


# ---------------------------------------------------------------------------
# Precondition checks
# ---------------------------------------------------------------------------

def check_env() -> None:
    """Verify .env exists and load it."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("ERROR: .env file not found at project root.")
        print(f"Expected: {env_path}")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv(env_path)
    os.environ["AGENT_ENABLED"] = "true"
    print(f"Loaded .env from {env_path}")


def pick_golden_case(case_filter: str | None = None) -> dict[str, Any]:
    """Select a golden sample case and return its metadata.

    Args:
        case_filter: Optional substring to match against case_id or name.
            When None, a random case is selected.
    """
    cases = sorted(GOLDEN_CASES_DIR.iterdir())
    case_dirs = [d for d in cases if (d / "metadata.json").exists()]
    if not case_dirs:
        print("ERROR: No golden sample cases found.")
        sys.exit(1)

    if case_filter:
        needle = case_filter.lower()
        matched = [
            d for d in case_dirs
            if needle in d.name.lower()
        ]
        if not matched:
            available = [d.name for d in case_dirs]
            print(f"ERROR: No case matching '{case_filter}'.")
            print(f"Available: {', '.join(available)}")
            sys.exit(1)
        chosen = matched[0]
    else:
        chosen = random.choice(case_dirs)

    metadata = json.loads((chosen / "metadata.json").read_text())
    print(f"Selected case: {metadata['case_id']} ({metadata['name']})")
    return metadata


# ---------------------------------------------------------------------------
# Table rendering (stdlib only)
# ---------------------------------------------------------------------------

def truncate(text: str, width: int) -> str:
    """Truncate text to width, adding '...' if needed."""
    text = str(text or "")
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def render_programs_table(programs: list[dict[str, Any]]) -> str:
    """Render a simple ASCII table of programs."""
    if not programs:
        return "  (no programs extracted)"

    cols = [
        ("#", 4),
        ("Program Name", 30),
        ("Faculty", 18),
        ("Tuition", 12),
        ("Study Mode", 20),
    ]

    header_widths = [w for _, w in cols]
    sep = "+" + "+".join("-" * (w + 2) for w in header_widths) + "+"
    header = "|" + "|".join(
        f" {name:<{w}} " for name, w in cols
    ) + "|"

    lines = [sep, header, sep]
    for idx, prog in enumerate(programs, 1):
        study_opts = prog.get("study_options") or []
        study_mode = ""
        if study_opts and isinstance(study_opts, list):
            first = study_opts[0] if study_opts else {}
            if isinstance(first, dict):
                mode = first.get("study_mode") or ""
                dur = first.get("duration") or ""
                study_mode = f"{mode} / {dur}".strip(" /")

        row = "|" + "|".join([
            f" {truncate(str(idx), 4):<4} ",
            f" {truncate(prog.get('name_en') or prog.get('name', ''), 30):<30} ",
            f" {truncate(prog.get('faculty') or '', 18):<18} ",
            f" {truncate(prog.get('tuition_fee') or prog.get('tuition') or '', 12):<12} ",
            f" {truncate(study_mode, 20):<20} ",
        ]) + "|"
        lines.append(row)

    lines.append(sep)

    # Extra details per program
    for idx, prog in enumerate(programs, 1):
        deadlines = prog.get("deadlines") or []
        source = prog.get("source_url") or ""
        lines.append(
            f"  [{idx}] Deadlines: {len(deadlines)} item(s)"
            + (f"  |  Source: {truncate(source, 60)}" if source else "")
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def extract_programs(task_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract program list from agent task result."""
    output = task_result.get("output") or {}

    # Check parsed_programs from dry-run output
    parsed = output.get("parsed_programs") or []
    if parsed:
        return parsed

    # Fallback: check agent_response for structured data
    response_text = output.get("agent_response") or ""
    # Programs might be embedded in the response
    return parsed  # Return empty if nothing found


def evaluate_streaming_acceptance(*, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate whether streaming behavior met the smoke-test acceptance bar."""
    event_types = [str(event.get("type") or "") for event in list(events or [])]
    required_lifecycle = [
        "llm_call_started",
        "tool_call_finished",
        "agent_done",
    ]
    missing_lifecycle = [
        event_type for event_type in required_lifecycle if event_type not in event_types
    ]
    summary_delta_seen = "summary_delta" in event_types
    summary_fallback_seen = any(
        event.get("type") == "summary_finished" and event.get("streamed") is False
        for event in list(events or [])
    )
    return {
        "event_types": event_types,
        "missing_lifecycle": missing_lifecycle,
        "summary_delta_seen": summary_delta_seen,
        "summary_fallback_seen": summary_fallback_seen,
        "lifecycle_ok": not missing_lifecycle,
        "summary_ok": summary_delta_seen or summary_fallback_seen,
    }


def assert_streaming_acceptance(*, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Raise when the smoke run failed the streaming acceptance criteria."""
    verdict = evaluate_streaming_acceptance(events=events)
    if not verdict["lifecycle_ok"]:
        raise AssertionError(
            "Missing lifecycle streaming events: "
            + ", ".join(verdict["missing_lifecycle"])
        )
    if not verdict["summary_ok"]:
        raise AssertionError(
            "Missing streamed/fallback final summary event."
        )
    return verdict


async def collect_task_events(client: Any, task_id: str) -> list[dict[str, Any]]:
    """Collect task lifecycle events from the SSE endpoint until it closes."""
    events: list[dict[str, Any]] = []
    async with client.stream("GET", f"/tasks/{task_id}/events", timeout=None) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if not payload:
                continue
            events.append(json.loads(payload))
    return events


async def finalize_events_task(
    events_task: "asyncio.Task[list[dict[str, Any]]]",
) -> list[dict[str, Any]]:
    """Resolve the SSE collector task without hanging the smoke test."""
    try:
        return await asyncio.wait_for(events_task, timeout=5.0)
    except asyncio.TimeoutError:
        events_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await events_task
        return []


async def run_single_test(
    client: Any,
    *,
    label: str,
    url: str,
    univ_slug: str,
    year: int,
    page_type_hint: str,
    policy_profile: dict[str, Any] | None = None,
    auto_paginate: bool = False,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Run one agent test and poll until completion."""
    payload: dict[str, Any] = {
        "url": url,
        "univ_slug": univ_slug,
        "year": year,
        "page_type_hint": page_type_hint,
        "autonomous": True,
        "dry_run": True,
        "runtime": "pydanticai",
    }
    if policy_profile:
        payload["policy_profile"] = policy_profile
    if auto_paginate:
        payload["auto_paginate"] = True
    if max_pages is not None:
        payload["max_pages"] = max_pages

    print(f"\n{'=' * 50}")
    print(f"  [{label}] {univ_slug} -- {page_type_hint}")
    print(f"  URL: {truncate(url, 70)}")
    print(f"{'=' * 50}")

    # Submit
    resp = await client.post("/agent/run", json=payload)
    if resp.status_code != 200:
        print(f"  ERROR: POST /agent/run returned {resp.status_code}")
        print(f"  Body: {resp.text}")
        return {"status": "ERROR", "error": resp.text, "programs": []}

    task_id = resp.json()["task_id"]
    print(f"  Task ID: {task_id}")
    events_task = asyncio.create_task(collect_task_events(client, task_id))

    # Poll
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > TIMEOUT:
            print(f"  TIMEOUT after {elapsed:.0f}s — cancelling task")
            try:
                await client.post(f"/tasks/{task_id}/cancel")
                await asyncio.sleep(2)
            except Exception:
                pass  # Best-effort cancel
            events = await finalize_events_task(events_task)
            return {
                "status": "TIMEOUT",
                "duration_sec": elapsed,
                "programs": [],
                "events": events,
            }

        await asyncio.sleep(POLL_INTERVAL)

        status_resp = await client.get(f"/tasks/{task_id}")
        if status_resp.status_code != 200:
            continue

        data = status_resp.json()
        state = data.get("state", "")
        progress = data.get("progress") or ""

        mins = int(elapsed) // 60
        secs = int(elapsed) % 60
        print(f"  [{mins:02d}:{secs:02d}] {state} -- {truncate(progress, 50)}", end="\r")

        if state == "DONE":
            duration = time.monotonic() - start
            print(f"\n  Status: DONE | Duration: {duration:.1f}s")
            result = data.get("result") or {}
            programs = extract_programs(result)
            events = await finalize_events_task(events_task)
            acceptance = assert_streaming_acceptance(events=events)
            print(f"  Programs extracted: {len(programs)}")
            print(render_programs_table(programs))
            print(
                "  Streaming acceptance: PASS "
                f"(summary_delta={acceptance['summary_delta_seen']}, "
                f"summary_fallback={acceptance['summary_fallback_seen']})"
            )
            return {
                "status": "DONE",
                "duration_sec": round(duration, 1),
                "url": url,
                "programs": programs,
                "raw_result": result,
                "events": events,
                "streaming_acceptance": acceptance,
            }

        if state == "FAILED":
            duration = time.monotonic() - start
            error = data.get("error") or "unknown"
            events = await finalize_events_task(events_task)
            print(f"\n  FAILED after {duration:.1f}s: {error}")
            return {
                "status": "FAILED",
                "duration_sec": round(duration, 1),
                "error": error,
                "programs": [],
                "events": events,
            }


# ---------------------------------------------------------------------------
# Server + Client lifecycle
# ---------------------------------------------------------------------------

def find_free_port() -> int:
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_server(port: int) -> asyncio.Task[None]:
    """Start uvicorn server as a background asyncio task."""
    import uvicorn
    from src.api.server import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # Wait for server to start accepting connections
    for _ in range(50):
        await asyncio.sleep(0.2)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            continue
    else:
        raise RuntimeError(f"Server did not start on port {port}")
    print(f"  Server started on http://127.0.0.1:{port}")
    return task


async def start_client(server_url: str) -> asyncio.Task[None]:
    """Start client runtime as a background asyncio task."""
    from src.client.config import ClientConfig, ensure_client_id
    from src.client.runtime import ClientRuntime

    config = ClientConfig(
        server_url=server_url,
        client_name="e2e-smoke-client",
        client_id=ensure_client_id(None),
        workdir=str(PROJECT_ROOT),
    )
    runtime = ClientRuntime(config)
    task = asyncio.create_task(runtime.run_forever())
    print(f"  Client connecting to {server_url}")
    return task


async def wait_for_client(base_url: str) -> None:
    """Poll GET /clients until at least one client is registered."""
    import httpx

    deadline = time.monotonic() + CLIENT_READY_TIMEOUT
    async with httpx.AsyncClient(base_url=base_url) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get("/clients")
                if resp.status_code == 200:
                    clients = resp.json()
                    if clients:
                        print(f"  Client registered: {clients[0].get('client_name', '?')}")
                        return
            except Exception:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError("Client did not register within timeout")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> "argparse.Namespace":
    """Parse CLI arguments."""
    import argparse

    parser = argparse.ArgumentParser(
        description="E2E Agent Smoke Test — exercises the full agent runtime against golden samples.",
    )
    parser.add_argument(
        "--case",
        default=None,
        help=(
            "Substring to match a golden sample case_id (e.g. 'edinburgh', 'leeds'). "
            "When omitted, a random case is selected."
        ),
    )
    parser.add_argument(
        "--paginate",
        action="store_true",
        default=False,
        help="Run a pagination smoke test: auto-paginate 2 pages and verify quality.",
    )
    parser.add_argument(
        "--paginate-case",
        default=None,
        help=(
            "Case to use for the pagination test (default: auto-select a paginated case). "
            "Only used when --paginate is set."
        ),
    )
    parser.add_argument(
        "--skip-detail",
        action="store_true",
        default=False,
        help="Skip the detail page test.",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        default=False,
        help="Skip the normal (non-paginated) index page test.",
    )
    return parser.parse_args()


# Cases known to have URL-parameter pagination (for --paginate auto-selection)
_PAGINATED_CASES = ["edinburgh", "leeds"]


async def main() -> None:
    args = parse_args()
    check_env()
    metadata = pick_golden_case(args.case)

    case_id = metadata["case_id"]
    univ_name = metadata["name"]
    univ_slug = case_id.split("_")[0] if "_" in case_id else univ_name.lower().replace(" ", "-")
    index_url = metadata["index_url"]
    detail_url = metadata["detail_url"]
    year = datetime.now(tz=timezone.utc).year

    # Resolve pagination case (may differ from the main case)
    paginate_metadata = None
    if args.paginate:
        paginate_case_filter = args.paginate_case
        if not paginate_case_filter:
            # Auto-select: prefer a known paginated case
            paginate_case_filter = random.choice(_PAGINATED_CASES)
        paginate_metadata = pick_golden_case(paginate_case_filter)
        print(f"  Pagination test case: {paginate_metadata['case_id']}")

    # Start real server + client
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    print("\n--- Starting server + client ---")
    server_task = await start_server(port)
    client_task = await start_client(base_url)
    await wait_for_client(base_url)
    print("--- Ready ---\n")

    import httpx

    results: dict[str, dict[str, Any]] = {}

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            total_start = time.monotonic()

            # Test 1: Detail page
            if not args.skip_detail:
                results["detail"] = await run_single_test(
                    client,
                    label="Detail",
                    url=detail_url,
                    univ_slug=univ_slug,
                    year=year,
                    page_type_hint="detail",
                )

            # Test 2: Index page (normal, max 3 candidates)
            if not args.skip_index:
                results["index"] = await run_single_test(
                    client,
                    label="Index",
                    url=index_url,
                    univ_slug=univ_slug,
                    year=year,
                    page_type_hint="index",
                    policy_profile={"auto_run_max_candidates": 3},
                )

            # Test 3: Pagination test (auto-paginate 2 pages)
            if paginate_metadata:
                pg_case_id = paginate_metadata["case_id"]
                pg_slug = (
                    pg_case_id.split("_")[0]
                    if "_" in pg_case_id
                    else paginate_metadata["name"].lower().replace(" ", "-")
                )
                results["pagination"] = await run_single_test(
                    client,
                    label="Pagination",
                    url=paginate_metadata["index_url"],
                    univ_slug=pg_slug,
                    year=year,
                    page_type_hint="index",
                    auto_paginate=True,
                    max_pages=2,
                )

            total_duration = time.monotonic() - total_start
    finally:
        client_task.cancel()
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await client_task
        with contextlib.suppress(asyncio.CancelledError):
            await server_task

    # Save JSON results
    E2E_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    result_path = E2E_RESULTS_DIR / f"{timestamp}_{univ_slug}.json"
    archive: dict[str, Any] = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "case_id": case_id,
        "univ_name": univ_name,
        "year": year,
    }
    for test_name, result in results.items():
        archive[f"{test_name}_test"] = {
            k: v for k, v in result.items() if k != "raw_result"
        }
    if "index" in results:
        archive["index_test"]["auto_run_max_candidates"] = 3
    if "pagination" in results:
        archive["pagination_test"]["max_pages"] = 2
        archive["pagination_test"]["paginate_case"] = (
            paginate_metadata["case_id"] if paginate_metadata else None
        )

    result_path.write_text(
        json.dumps(archive, indent=2, ensure_ascii=False, default=str)
    )

    # Final summary
    print(f"\n{'=' * 60}")
    print(f"  University: {univ_name} ({case_id})")

    failed = False
    for test_name, result in results.items():
        status = result.get("status", "?")
        progs = len(result.get("programs") or [])
        dur = result.get("duration_sec", 0)
        label = test_name.capitalize().ljust(12)
        print(f"  {label} {status} -- {progs} program(s), {dur:.1f}s")

        if status not in ("DONE",):
            # quality_failed is acceptable for pagination — it means the breaker worked
            if test_name == "pagination" and status == "quality_failed":
                print(f"    (quality breaker triggered — this is informational, not a failure)")
            else:
                print(f"  FAIL: {test_name} test status={status}")
                failed = True

    # Minimum program count checks
    if "detail" in results:
        detail_progs = len(results["detail"].get("programs") or [])
        if detail_progs < 1 and results["detail"].get("status") == "DONE":
            print(f"  FAIL: detail test extracted 0 programs")
            failed = True

    if "index" in results:
        index_progs = len(results["index"].get("programs") or [])
        if index_progs < 2 and results["index"].get("status") == "DONE":
            print(f"  FAIL: index test extracted {index_progs} program(s), expected >= 2")
            failed = True

    if "pagination" in results:
        pg_result = results["pagination"]
        pg_status = pg_result.get("status", "?")
        pg_progs = len(pg_result.get("programs") or [])
        if pg_status == "DONE" and pg_progs < 2:
            print(f"  FAIL: pagination test extracted {pg_progs} program(s), expected >= 2 from 2 pages")
            failed = True
        if pg_status == "DONE":
            # Verify programs have quality data
            quality_ok = sum(
                1 for p in pg_result.get("programs", [])
                if p.get("name_en") and len(str(p.get("name_en", ""))) >= 5
            )
            print(f"  Pagination quality: {quality_ok}/{pg_progs} programs have valid names")

    print(f"  Total time:   {total_duration:.1f}s")
    print(f"  Results saved: {result_path.relative_to(PROJECT_ROOT)}")
    print(f"{'=' * 60}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
