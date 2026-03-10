"""Validate auto page-type classifier with real LLM over golden samples."""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.agents.factory import create_router
from src.scrapers.link_parser import extract_links_with_text
from src.services.page_type_resolution import classify_page_type_auto


def _load_manifest() -> dict:
    return json.loads(Path("golden_samples/manifest.json").read_text(encoding="utf-8"))


def main() -> int:
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        print("Refusing to run real-LLM auto page-type validation in CI/GitHub Actions.")
        print("Run locally with .env loaded.")
        return 2

    router = create_router()
    manifest = _load_manifest()
    cases = list(manifest.get("cases") or [])
    if not cases:
        print("No golden cases found.")
        return 1

    total = 0
    passed = 0
    failures: list[str] = []

    for case in cases:
        case_id = str(case.get("case_id") or "")
        case_dir = Path("golden_samples/cases") / case_id

        index_md = (case_dir / "index.md").read_text(encoding="utf-8")
        index_links = len(extract_links_with_text(index_md, str(case.get("index_url") or "")))
        index_decision = classify_page_type_auto(
            url=str(case.get("index_url") or ""),
            markdown=index_md,
            html="",
            link_count=index_links,
            router=router,
        )
        total += 1
        if index_decision.page_type == "index":
            passed += 1
        else:
            failures.append(f"{case_id}: index -> {index_decision.page_type}")

        detail_md = (case_dir / "detail.md").read_text(encoding="utf-8")
        detail_links = len(extract_links_with_text(detail_md, str(case.get("detail_url") or "")))
        detail_decision = classify_page_type_auto(
            url=str(case.get("detail_url") or ""),
            markdown=detail_md,
            html="",
            link_count=detail_links,
            router=router,
        )
        total += 1
        if detail_decision.page_type == "detail":
            passed += 1
        else:
            failures.append(f"{case_id}: detail -> {detail_decision.page_type}")

    print(f"Real-LLM golden auto page-type: {passed}/{total} passed")
    if failures:
        print("Failures:")
        for row in failures:
            print(f"- {row}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
