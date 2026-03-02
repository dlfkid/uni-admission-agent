#!/usr/bin/env python3
"""Collect golden sample snapshots from manifest URLs."""

from __future__ import annotations

import argparse
import json

from src.services.golden_samples import collect_golden_samples


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect golden sample snapshots")
    parser.add_argument(
        "--manifest",
        default="golden_samples/manifest.json",
        help="Path to golden samples manifest JSON",
    )
    parser.add_argument(
        "--output-root",
        default="golden_samples/cases",
        help="Directory to write per-case snapshots",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing case snapshots",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=40,
        help="Network timeout in seconds",
    )
    args = parser.parse_args()

    report = collect_golden_samples(
        manifest_path=args.manifest,
        output_root=args.output_root,
        overwrite=args.overwrite,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
