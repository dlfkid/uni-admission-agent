"""Phenomenon zip reporter.

Captures the raw page artefacts (HTML, Markdown, objective signal params, and
run log) into a single compressed zip file.  The zip contains no diagnosis or
conclusions — it is consumed offline by a developer or LLM to author a crawl
strategy and golden sample for a new university.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit


def export_report_zip(
    *,
    out_dir: "Path | str",
    index_url: str,
    html: str,
    markdown: str,
    params: Dict[str, Any],
    run_log: str,
    timestamp: str,
) -> str:
    """Write a phenomenon zip and return its absolute path as a string.

    Args:
        out_dir:    Directory where the zip file is written (created if absent).
        index_url:  URL of the programme-index page that was fetched.
        html:       Raw HTML returned by the final fetch attempt.
        markdown:   Markdown conversion of that HTML.
        params:     Objective signal dictionary (fetch levels, content signals,
                    strategy scores, outcome, etc.).  ``index_url`` and
                    ``timestamp`` are merged in automatically.
        run_log:    Free-text log of the fetch/extract run (one line per step).
        timestamp:  ISO-ish timestamp string used in the zip filename, e.g.
                    ``"20260609-120000"``.

    Returns:
        Absolute path of the written zip file.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    host = urlsplit(str(index_url or "").strip()).netloc.lower() or "unknown"
    zip_path = out / f"{host}-{timestamp}.zip"

    full_params: Dict[str, Any] = {
        "index_url": index_url,
        "timestamp": timestamp,
        **params,
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", html or "")
        zf.writestr("index.md", markdown or "")
        zf.writestr(
            "params.json",
            json.dumps(full_params, ensure_ascii=False, indent=2),
        )
        zf.writestr("run.log", run_log or "")

    return str(zip_path)
