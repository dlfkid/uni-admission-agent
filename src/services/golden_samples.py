"""Golden sample collection utilities for Phase 3 quality evaluation."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify_case_id(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return normalized or "case"


def load_manifest(manifest_path: str) -> Dict[str, Any]:
    path = Path(manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"Golden manifest not found: {manifest_path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Golden manifest must contain a non-empty 'cases' list")
    return payload


def _fetch_html(url: str, timeout_seconds: int = 40) -> Dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            )
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            charset = response.headers.get_content_charset() or "utf-8"
            html = response.read().decode(charset, errors="replace")
            return {
                "status_code": status_code,
                "html": html,
                "error": None,
            }
    except HTTPError as exc:
        return {
            "status_code": int(exc.code),
            "html": "",
            "error": f"HTTPError: {exc}",
        }
    except URLError as exc:
        return {
            "status_code": 0,
            "html": "",
            "error": f"URLError: {exc}",
        }


def _html_to_markdown(html: str, base_url: str) -> str:
    try:
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        generator = DefaultMarkdownGenerator()
        markdown_obj = generator.generate_markdown(input_html=html, base_url=base_url)
        if markdown_obj and hasattr(markdown_obj, "raw_markdown"):
            return str(markdown_obj.raw_markdown or "")
    except Exception as exc:  # pragma: no cover - fallback path
        logger.warning("Markdown conversion fallback for %s: %s", base_url, exc)

    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def collect_golden_samples(
    manifest_path: str,
    output_root: str = "golden_samples/cases",
    overwrite: bool = False,
    timeout_seconds: int = 40,
) -> Dict[str, Any]:
    manifest = load_manifest(manifest_path)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)

    collected = 0
    failures = 0
    case_reports: List[Dict[str, Any]] = []

    for raw_case in manifest.get("cases", []):
        case = dict(raw_case or {})
        case_id = slugify_case_id(case.get("case_id") or case.get("name"))
        case_dir = root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        files = {
            "index_html": case_dir / "index.html",
            "index_md": case_dir / "index.md",
            "detail_html": case_dir / "detail.html",
            "detail_md": case_dir / "detail.md",
            "metadata": case_dir / "metadata.json",
        }

        if not overwrite and files["index_html"].exists() and files["detail_html"].exists():
            case_reports.append(
                {
                    "case_id": case_id,
                    "status": "skipped",
                    "reason": "already exists",
                }
            )
            continue

        case_result = {
            "case_id": case_id,
            "name": case.get("name"),
            "index_url": case.get("index_url"),
            "detail_url": case.get("detail_url"),
            "fetched_at": _utc_now_iso(),
            "pages": {},
        }

        ok = True
        for page_kind, url_key in (("index", "index_url"), ("detail", "detail_url")):
            url = str(case.get(url_key) or "").strip()
            if not url:
                ok = False
                case_result["pages"][page_kind] = {
                    "status": "failed",
                    "error": f"missing {url_key}",
                }
                continue

            fetch_result = _fetch_html(url, timeout_seconds=timeout_seconds)
            status_code = int(fetch_result.get("status_code") or 0)
            html = str(fetch_result.get("html") or "")
            error = fetch_result.get("error")

            if status_code < 200 or status_code >= 300 or not html:
                ok = False
                case_result["pages"][page_kind] = {
                    "status": "failed",
                    "status_code": status_code,
                    "error": error or "empty response",
                    "url": url,
                }
                continue

            markdown = _html_to_markdown(html, url)
            files[f"{page_kind}_html"].write_text(html, encoding="utf-8")
            files[f"{page_kind}_md"].write_text(markdown, encoding="utf-8")
            case_result["pages"][page_kind] = {
                "status": "ok",
                "status_code": status_code,
                "url": url,
                "html_chars": len(html),
                "markdown_chars": len(markdown),
                "html_path": str(files[f"{page_kind}_html"]),
                "markdown_path": str(files[f"{page_kind}_md"]),
            }

        files["metadata"].write_text(
            json.dumps(case_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if ok:
            collected += 1
            case_reports.append({"case_id": case_id, "status": "ok"})
        else:
            failures += 1
            case_reports.append({"case_id": case_id, "status": "failed"})

    return {
        "manifest": str(Path(manifest_path)),
        "output_root": str(root),
        "collected": collected,
        "failures": failures,
        "cases": case_reports,
        "generated_at": _utc_now_iso(),
    }
