"""CI gate for the program-name extraction smoke test.

The rich scorecard lives in scripts/name_smoke.py (run it during iteration:
`uv run python scripts/name_smoke.py`). These tests import the same scoring
functions so CI fails on any regression in name accuracy/efficiency without
needing a live LLM.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# Load the script module by path (scripts/ isn't a package).
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "name_smoke.py"
_spec = importlib.util.spec_from_file_location("name_smoke", _SCRIPT)
name_smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(name_smoke)


def test_noise_filter_rejects_all_labeled_noise():
    result = name_smoke.score_noise_filter()
    assert result["noise_missed"] == [], (
        f"these labeled-noise strings slipped through is_noise_program_name: "
        f"{result['noise_missed']}"
    )
    assert result["noise_recall"] == 1.0


def test_noise_filter_keeps_all_legitimate_names():
    result = name_smoke.score_noise_filter()
    assert result["valid_killed"] == [], (
        f"these real program names were wrongly flagged as noise: "
        f"{result['valid_killed']}"
    )
    assert result["valid_precision"] == 1.0


def test_resolution_accuracy_meets_gate():
    result = name_smoke.score_resolution(sim_threshold=name_smoke.DEFAULT_SIM_THRESHOLD)
    assert result["total"] >= 1, "no golden cases with expected_name were found"
    failed = [c for c in result["cases"] if not c["passed"]]
    assert result["accuracy"] >= name_smoke.DEFAULT_RESOLUTION_ACCURACY, (
        f"name resolution accuracy {result['accuracy']:.0%} below gate "
        f"{name_smoke.DEFAULT_RESOLUTION_ACCURACY:.0%}; failing cases: "
        f"{[(c['case'], c['resolved']) for c in failed]}"
    )


def test_resolution_runs_without_llm():
    """Efficiency guard: the golden cases should resolve deterministically
    (no LLM fallback) — if coverage drops, a heuristic regressed."""
    result = name_smoke.score_resolution(sim_threshold=name_smoke.DEFAULT_SIM_THRESHOLD)
    assert result["deterministic_coverage"] >= 0.75, (
        f"deterministic coverage {result['deterministic_coverage']:.0%} dropped — "
        "more cases now need an LLM call than before"
    )
