#!/usr/bin/env python3
"""E2E Agent Smoke Test.

Exercises the full agent runtime (pydanticai mode) against a randomly
selected golden-sample university. Runs detail + index page crawls in
dry-run mode and outputs results for human quality judgment.

Usage:
    uv run python scripts/e2e_agent_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
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
TIMEOUT = 8 * 60  # 8 minutes per test


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


def pick_golden_case() -> dict[str, Any]:
    """Randomly select a golden sample case and return its metadata."""
    cases = sorted(GOLDEN_CASES_DIR.iterdir())
    case_dirs = [d for d in cases if (d / "metadata.json").exists()]
    if not case_dirs:
        print("ERROR: No golden sample cases found.")
        sys.exit(1)

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


async def run_single_test(
    client: Any,
    *,
    label: str,
    url: str,
    univ_slug: str,
    year: int,
    page_type_hint: str,
    policy_profile: dict[str, Any] | None = None,
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

    # Poll
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > TIMEOUT:
            print(f"  TIMEOUT after {elapsed:.0f}s")
            return {"status": "TIMEOUT", "duration_sec": elapsed, "programs": []}

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
            print(f"  Programs extracted: {len(programs)}")
            print(render_programs_table(programs))
            return {
                "status": "DONE",
                "duration_sec": round(duration, 1),
                "url": url,
                "programs": programs,
                "raw_result": result,
            }

        if state == "FAILED":
            duration = time.monotonic() - start
            error = data.get("error") or "unknown"
            print(f"\n  FAILED after {duration:.1f}s: {error}")
            return {
                "status": "FAILED",
                "duration_sec": round(duration, 1),
                "error": error,
                "programs": [],
            }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    check_env()
    metadata = pick_golden_case()

    case_id = metadata["case_id"]
    univ_name = metadata["name"]
    # Derive slug from case_id (e.g. "ucl_undergrad_anthropology" -> "ucl")
    # or fall back to lowercased name
    univ_slug = case_id.split("_")[0] if "_" in case_id else univ_name.lower().replace(" ", "-")
    index_url = metadata["index_url"]
    detail_url = metadata["detail_url"]
    year = datetime.now(tz=timezone.utc).year

    # Import app after env is loaded
    from httpx import ASGITransport, AsyncClient
    from src.api.server import app

    transport = ASGITransport(app=app)  # type: ignore[arg-type]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        total_start = time.monotonic()

        # Test 1: Detail page
        detail_result = await run_single_test(
            client,
            label="Detail",
            url=detail_url,
            univ_slug=univ_slug,
            year=year,
            page_type_hint="detail",
        )

        # Test 2: Index page (max 3 candidates)
        index_result = await run_single_test(
            client,
            label="Index",
            url=index_url,
            univ_slug=univ_slug,
            year=year,
            page_type_hint="index",
            policy_profile={"auto_run_max_candidates": 3},
        )

        total_duration = time.monotonic() - total_start

    # Save JSON results
    E2E_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    result_path = E2E_RESULTS_DIR / f"{timestamp}_{univ_slug}.json"
    archive = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "case_id": case_id,
        "univ_name": univ_name,
        "year": year,
        "detail_test": {
            k: v for k, v in detail_result.items() if k != "raw_result"
        },
        "index_test": {
            k: v for k, v in index_result.items() if k != "raw_result"
        },
    }
    # Add auto_run_max_candidates to index test
    archive["index_test"]["auto_run_max_candidates"] = 3

    result_path.write_text(
        json.dumps(archive, indent=2, ensure_ascii=False, default=str)
    )

    # Final summary
    detail_status = detail_result.get("status", "?")
    detail_progs = len(detail_result.get("programs") or [])
    detail_dur = detail_result.get("duration_sec", 0)

    index_status = index_result.get("status", "?")
    index_progs = len(index_result.get("programs") or [])
    index_dur = index_result.get("duration_sec", 0)

    print(f"\n{'=' * 50}")
    print(f"  University: {univ_name} ({case_id})")
    print(f"  Detail test:  {detail_status} -- {detail_progs} program(s), {detail_dur:.1f}s")
    print(f"  Index test:   {index_status} -- {index_progs} program(s), {index_dur:.1f}s")
    print(f"  Total time:   {total_duration:.1f}s")
    print(f"  Results saved: {result_path.relative_to(PROJECT_ROOT)}")
    print(f"{'=' * 50}")

    # Exit code: 0 if both tests succeeded, 1 otherwise
    if detail_status != "DONE" or index_status != "DONE":
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
