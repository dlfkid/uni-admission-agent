"""Real fetch adapters — bridges the orchestrator's injected fetch signature
to AdmissionScraper (server) and native_browser (client).

Heavy dependencies (crawl4ai, playwright, native_browser) are imported
inside the helper functions so that importing this module is cheap and the
unit-test suite can mock the inner ``_run_*`` / ``_html_to_markdown``
functions without triggering those imports.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Optional, Tuple

_PROGRAM_RENDER_RE = re.compile(
    r"Doctor of |Master of |Bachelor of |Graduate (?:Diploma|Certificate)",
    re.IGNORECASE,
)
_CLIENT_WAIT_SCROLL_ROUNDS = 12
_CLIENT_WAIT_SCROLL_PIXELS = 3500
_CLIENT_WAIT_TICK_MS = 1500
_CLIENT_WAIT_MIN_MATCHES = 5


def _run_server_crawl(url: str):
    from src.scrapers.engine import AdmissionScraper  # pylint: disable=import-outside-toplevel
    scraper = AdmissionScraper()
    return asyncio.run(scraper.crawl_page(url))


def _clean_browser_path() -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright  # pylint: disable=import-outside-toplevel
        with sync_playwright() as p:
            path = p.chromium.executable_path
        return path if path and Path(path).exists() else None
    except Exception:  # pylint: disable=broad-except
        return None


def _run_client_fetch(url: str, *, wait: bool = False,
                      wait_selector: Optional[str] = None) -> dict:
    from src.client.native_browser import fetch_browser_payload  # pylint: disable=import-outside-toplevel
    # wait / wait_selector are accepted for interface compatibility but are NOT
    # forwarded to native_browser — real wait-for-render uses _run_client_wait_fetch.
    del wait, wait_selector
    # page_type_hint="detail" is intentional even for an index page: it makes
    # the browser return raw html_content without anchor pre-selection, which is
    # exactly what the orchestrator needs.  Do NOT change to "index".
    return fetch_browser_payload(
        url=url, page_type_hint="detail",
        browser_path=_clean_browser_path(), debug_port=9333, launch_timeout=45.0)


def _run_client_wait_fetch(url: str) -> str:  # noqa: C901
    """Fetch *url* via Playwright headless Chromium with scroll-and-wait.

    Launches a fresh Playwright browser, navigates to *url*, then scrolls
    incrementally and checks page content until enough programme headings are
    visible (or the scroll budget is exhausted).  Returns the final HTML.
    """
    from playwright.sync_api import sync_playwright  # pylint: disable=import-outside-toplevel
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            html = ""
            for _ in range(_CLIENT_WAIT_SCROLL_ROUNDS):
                page.mouse.wheel(0, _CLIENT_WAIT_SCROLL_PIXELS)
                page.wait_for_timeout(_CLIENT_WAIT_TICK_MS)
                html = page.content()
                if len(_PROGRAM_RENDER_RE.findall(html)) >= _CLIENT_WAIT_MIN_MATCHES:
                    break
            if not html:
                html = page.content()
            return html
    except Exception:  # pylint: disable=broad-except
        return ""
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # pylint: disable=broad-except
                pass


def _html_to_markdown(html: str, base_url: str) -> str:
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator  # pylint: disable=import-outside-toplevel
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
    """Fetch *url* via native Chrome CDP or Playwright render-and-scroll.

    Args:
        url:           Target URL.
        wait:          When True, use Playwright scroll-and-wait render
                       (``_run_client_wait_fetch``).  When False, use the
                       native Chrome CDP path (``_run_client_fetch``).
        wait_selector: Accepted for interface compatibility; not forwarded.

    Returns:
        ``(html, markdown)`` tuple; either field is an empty string on failure.
    """
    if wait:
        html = _run_client_wait_fetch(url)
        return (html, _html_to_markdown(html, url) if html else "")
    payload = _run_client_fetch(url, wait_selector=wait_selector)
    html = str(payload.get("html_content") or "")
    return (html, _html_to_markdown(html, url) if html else "")
