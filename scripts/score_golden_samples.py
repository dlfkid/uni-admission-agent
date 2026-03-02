#!/usr/bin/env python3
"""Run Phase 3 quality scoring against golden samples."""

from __future__ import annotations

import argparse
import json
import sys

from src.services.quality_scoring import score_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Score golden sample quality metrics")
    parser.add_argument(
        "--manifest",
        default="golden_samples/manifest.json",
        help="Path to golden samples manifest JSON",
    )
    parser.add_argument(
        "--base-dir",
        default="golden_samples/cases",
        help="Directory containing case snapshots",
    )
    parser.add_argument(
        "--report",
        default="golden_samples/reports/quality_report.json",
        help="Output report JSON path",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.60,
        help="Global mean-score threshold",
    )
    args = parser.parse_args()

    report = score_manifest(
        manifest_path=args.manifest,
        base_dir=args.base_dir,
        output_report_path=args.report,
        global_threshold=args.threshold,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("global_pass", False):
        sys.exit(1)


if __name__ == "__main__":
    main()
