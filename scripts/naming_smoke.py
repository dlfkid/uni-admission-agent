#!/usr/bin/env python3
"""Names-only crawl smoke — harvest program names from an index page.

Collects ONLY program names (no detail-page crawl, no per-page LLM), so it
costs a single index fetch. Two modes:

  # Offline (zero network/tokens) — iterate fast against a saved snapshot:
  uv run python scripts/naming_smoke.py --snapshot golden_samples/cases/leeds_masters_ai_business/index.md

  # Live (one index fetch, still no detail crawl / no LLM):
  uv run python scripts/naming_smoke.py --url 'https://courses.leeds.ac.uk/course-search/masters-courses'

Output is a markdown table of harvested course names for human review.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Dict, Any

from src.services.index_name_harvest import harvest_index_program_names


def _fetch_index_markdown(url: str) -> str:
    """Fetch a single index page to markdown (1 crawl, no LLM)."""
    from src.scrapers.engine import AdmissionScraper

    scraper = AdmissionScraper()
    page = asyncio.run(scraper.crawl_page(url))
    return page.markdown or ""


def _render_markdown(items: List[Dict[str, Any]], source: str) -> str:
    lines = [
        f"# Names-only harvest — {source}",
        "",
        f"**{len(items)} 门课程**（仅抓名字，0 详情页抓取，0 per-page LLM）",
        "",
        "| # | 课程名 | 你的标记 |",
        "|---|---|---|",
    ]
    for i, item in enumerate(items, 1):
        lines.append(f"| {i} | {item['name_en']} | |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Names-only index harvest smoke")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--snapshot", help="Path to a saved index markdown file")
    src.add_argument("--url", help="Index URL to fetch live (1 fetch, no LLM)")
    parser.add_argument("--out", help="Write the markdown report to this path")
    args = parser.parse_args()

    if args.snapshot:
        markdown = Path(args.snapshot).read_text(encoding="utf-8")
        base_url = "https://courses.leeds.ac.uk/course-search/masters-courses"
        source = f"snapshot {args.snapshot}"
    else:
        markdown = _fetch_index_markdown(args.url)
        base_url = args.url
        source = f"live {args.url}"

    items = harvest_index_program_names(markdown, base_url=base_url)
    report = _render_markdown(items, source)

    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"wrote {len(items)} names to {args.out}")
    else:
        print(report)

    if not items:
        print("\n⚠️  No names harvested — check the index markdown structure.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
