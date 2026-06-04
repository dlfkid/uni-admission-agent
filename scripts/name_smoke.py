#!/usr/bin/env python3
"""Program-name extraction smoke test — fast, offline, iteration-friendly.

Getting the program *name* right is the hardest part of this crawler: the
LLM and heuristics regularly grab a navigation label, a faculty heading, or
an entry-requirement sentence instead of the actual degree title. This
harness lets you check, in seconds and with zero LLM/network cost, whether a
change to the name pipeline improved or regressed two things:

  ACCURACY  — do we resolve the correct name, and do we reject non-names?
  EFFICIENCY — what fraction resolves deterministically (no LLM call)?

It measures two layers:

  1. NOISE FILTER (instant, pure)
     golden_samples/name_labels.json holds labeled `noise` (must be rejected)
     and `valid` (must survive) strings. Reports recall on noise + precision
     guard on valid, and names every miss. This is the inner loop while
     tuning is_noise_program_name / the regexes.

  2. RESOLUTION (fast, offline — LLM disabled)
     For each golden_samples/cases/* snapshot, run resolve_program_name with
     llm_fallback_enabled=False over the real captured slug + html <title> +
     detail markdown, and compare to expected_name. Reports per-case
     pass/fail, which signal won, and — the efficiency metric — what share
     resolved via the rule path vs would have needed an LLM call.

Usage:
    uv run python scripts/name_smoke.py
    uv run python scripts/name_smoke.py --json            # machine-readable
    uv run python scripts/name_smoke.py --sim-threshold 0.85

Exit code is non-zero if any gate is breached, so it doubles as a CI check.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from src.scrapers.helpers import extract_program_name, is_noise_program_name
from src.services.ingestion_pipeline import _extract_html_title
from src.services.program_name_resolution import resolve_program_name
from src.services.quality_scoring import _name_similarity

REPO_ROOT = Path(__file__).resolve().parent.parent
LABELS_PATH = REPO_ROOT / "golden_samples" / "name_labels.json"
CASES_DIR = REPO_ROOT / "golden_samples" / "cases"

# Default gates — a change that drops below these fails the smoke test.
DEFAULT_NOISE_RECALL = 1.0       # every labeled noise string must be rejected
DEFAULT_VALID_PRECISION = 1.0    # no legitimate title may be killed
DEFAULT_RESOLUTION_ACCURACY = 0.80
DEFAULT_SIM_THRESHOLD = 0.80     # per-case: similarity >= this counts as correct


# ---------------------------------------------------------------------------
# Layer 1 — noise filter
# ---------------------------------------------------------------------------

def score_noise_filter() -> Dict[str, Any]:
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    noise: List[str] = labels.get("noise", [])
    valid: List[str] = labels.get("valid", [])

    noise_missed = [n for n in noise if not is_noise_program_name(n)]
    valid_killed = [v for v in valid if is_noise_program_name(v)]

    recall = (len(noise) - len(noise_missed)) / len(noise) if noise else 1.0
    precision = (len(valid) - len(valid_killed)) / len(valid) if valid else 1.0

    return {
        "noise_total": len(noise),
        "valid_total": len(valid),
        "noise_recall": recall,
        "valid_precision": precision,
        "noise_missed": noise_missed,
        "valid_killed": valid_killed,
    }


# ---------------------------------------------------------------------------
# Layer 2 — resolution accuracy + efficiency
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def score_resolution(sim_threshold: float) -> Dict[str, Any]:
    cases: List[Dict[str, Any]] = []
    for case_dir in sorted(p for p in CASES_DIR.iterdir() if p.is_dir()):
        expected_path = case_dir / "expected.json"
        meta_path = case_dir / "metadata.json"
        detail_md = case_dir / "detail.md"
        detail_html = case_dir / "detail.html"
        if not expected_path.is_file() or not detail_md.is_file():
            continue

        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        meta = json.loads(_read(meta_path) or "{}")
        expected_name = str(expected.get("expected_name") or "").strip()
        if not expected_name:
            continue

        markdown = _read(detail_md)
        html = _read(detail_html)
        detail_url = str(meta.get("detail_url") or "")

        # Realistic single-index extraction inputs: NO anchor text (the hard
        # path that produced the Leeds requirement-sentence bug). Resolution
        # must lean on slug + title + markdown heading.
        result = resolve_program_name(
            markdown_name=extract_program_name(markdown),
            selected_anchor_text="",
            detail_url=detail_url,
            html_title=_extract_html_title(html),
            is_index_mode=True,
            llm_fallback_enabled=False,
        )

        similarity = _name_similarity(expected_name, result.name)
        passed = result.status == "resolved" and similarity >= sim_threshold
        cases.append(
            {
                "case": case_dir.name,
                "expected": expected_name,
                "resolved": result.name,
                "status": result.status,
                "source": result.source,
                "reason": result.reason,
                "similarity": round(similarity, 3),
                "passed": passed,
                # Efficiency: resolved by rule = no LLM call needed.
                "deterministic": result.reason == "rule_high_confidence",
            }
        )

    total = len(cases)
    correct = sum(1 for c in cases if c["passed"])
    deterministic = sum(1 for c in cases if c["deterministic"])
    return {
        "total": total,
        "accuracy": (correct / total) if total else 1.0,
        "deterministic_coverage": (deterministic / total) if total else 1.0,
        "cases": cases,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def run(sim_threshold: float) -> Dict[str, Any]:
    return {
        "noise": score_noise_filter(),
        "resolution": score_resolution(sim_threshold),
        "sim_threshold": sim_threshold,
    }


def _print_human(report: Dict[str, Any]) -> None:
    noise = report["noise"]
    res = report["resolution"]

    print("\n=== NAME EXTRACTION SMOKE ===\n")
    print("① Noise filter")
    print(f"   noise recall:    {noise['noise_recall']:.0%}  "
          f"({noise['noise_total'] - len(noise['noise_missed'])}/{noise['noise_total']} rejected)")
    print(f"   valid precision: {noise['valid_precision']:.0%}  "
          f"({noise['valid_total'] - len(noise['valid_killed'])}/{noise['valid_total']} kept)")
    for miss in noise["noise_missed"]:
        print(f"     ✗ NOT rejected (should be noise): {miss!r}")
    for kill in noise["valid_killed"]:
        print(f"     ✗ wrongly rejected (real name):   {kill!r}")

    print("\n② Resolution (LLM off)")
    print(f"   accuracy:               {res['accuracy']:.0%}  "
          f"({sum(1 for c in res['cases'] if c['passed'])}/{res['total']})")
    print(f"   deterministic coverage: {res['deterministic_coverage']:.0%}  "
          f"(resolved without an LLM call — efficiency)")
    print()
    for c in res["cases"]:
        mark = "✓" if c["passed"] else "✗"
        llm = "" if c["deterministic"] else "  [needs-LLM]"
        print(f"   {mark} {c['case']}")
        print(f"       expected: {c['expected']!r}")
        print(f"       resolved: {c['resolved']!r}  "
              f"(sim={c['similarity']}, via {c['source']}/{c['reason']}){llm}")


def _gates_pass(report: Dict[str, Any], args: argparse.Namespace) -> bool:
    noise = report["noise"]
    res = report["resolution"]
    ok = True
    if noise["noise_recall"] < args.noise_recall:
        print(f"\n❌ noise recall {noise['noise_recall']:.0%} < gate {args.noise_recall:.0%}")
        ok = False
    if noise["valid_precision"] < args.valid_precision:
        print(f"❌ valid precision {noise['valid_precision']:.0%} < gate {args.valid_precision:.0%}")
        ok = False
    if res["accuracy"] < args.resolution_accuracy:
        print(f"❌ resolution accuracy {res['accuracy']:.0%} < gate {args.resolution_accuracy:.0%}")
        ok = False
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Program-name extraction smoke test")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--sim-threshold", type=float, default=DEFAULT_SIM_THRESHOLD)
    parser.add_argument("--noise-recall", type=float, default=DEFAULT_NOISE_RECALL)
    parser.add_argument("--valid-precision", type=float, default=DEFAULT_VALID_PRECISION)
    parser.add_argument("--resolution-accuracy", type=float, default=DEFAULT_RESOLUTION_ACCURACY)
    args = parser.parse_args()

    report = run(args.sim_threshold)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    if _gates_pass(report, args):
        if not args.json:
            print("\n✅ name smoke passed\n")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
