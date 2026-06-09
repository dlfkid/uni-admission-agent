"""Real fetch adapters — bridges the orchestrator's injected fetch signature
to AdmissionScraper (server) and native_browser (client).

Heavy dependencies (crawl4ai, playwright, native_browser) are imported
inside the helper functions so that importing this module is cheap and the
unit-test suite can mock the inner ``_run_*`` / ``_html_to_markdown``
functions without triggering those imports.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional, Tuple


def _run_server_crawl(url: str):
    from src.scrapers.engine import AdmissionScraper
    scraper = AdmissionScraper()
    return asyncio.run(scraper.crawl_page(url))


def _clean_browser_path() -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
        return path if path and Path(path).exists() else None
    except Exception:
        return None


def _run_client_fetch(url: str, *, wait: bool = False,
                      wait_selector: Optional[str] = None) -> dict:
    from src.client.native_browser import fetch_browser_payload
    del wait, wait_selector
    return fetch_browser_payload(
        url=url, page_type_hint="detail",
        browser_path=_clean_browser_path(), debug_port=9333, launch_timeout=45.0)


def _html_to_markdown(html: str, base_url: str) -> str:
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    obj = DefaultMarkdownGenerator().generate_markdown(input_html=html or "", base_url=base_url)
    return getattr(obj, "raw_markdown", "") or ""


def server_fetch(url: str) -> Tuple[str, str]:
    """Fetch *url* via AdmissionScraper (plain HTTP / crawl4ai server mode).

    Returns:
        ``(html, markdown)`` tuple; either field is an empty string on failure.
    """
    page = _run_server_crawl(url)
    return (getattr(page, "html", "") or "", getattr(page, "markdown", "") or "")


def client_fetch(url: str, *, wait: bool = False,
                 wait_selector: Optional[str] = None, **_: Any) -> Tuple[str, str]:
    """Fetch *url* via native Chrome CDP (headless browser).

    Args:
        url:           Target URL.
        wait:          Passed to the browser payload helper (future use).
        wait_selector: CSS selector to wait for before returning (future use).

    Returns:
        ``(html, markdown)`` tuple; either field is an empty string on failure.
    """
    payload = _run_client_fetch(url, wait=wait, wait_selector=wait_selector)
    html = str(payload.get("html_content") or "")
    return (html, _html_to_markdown(html, url) if html else "")
