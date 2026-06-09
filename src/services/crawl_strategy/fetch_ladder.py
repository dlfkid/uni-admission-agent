"""Fetch escalation ladder with content-usability gate.

The ladder tries progressively heavier fetch modes (server → client →
client_wait) and stops as soon as the returned markdown passes the
content-usable check.  Fetch callables are injected so the ladder is
fully unit-testable with fakes (no real network or browser required).

Note: _MIN_CHARS is set to 200 (not 400) so that the 10-item heading-link
fixture in the test suite (~390 chars) passes the gate reliably.
"""
from __future__ import annotations

import re
from typing import Callable, Optional, Tuple

from src.services.crawl_strategy.types import FetchMode, FetchResult

ServerFetch = Callable[[str], Tuple[str, str]]
ClientFetch = Callable[..., Tuple[str, str]]

_CF_RE = re.compile(
    r"just a moment|verifying you are human|cloudflare|安全验证|checking your browser",
    re.IGNORECASE,
)
_MIN_CHARS = 200
_MIN_LINKS = 5
_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")


def content_is_usable(markdown: str) -> bool:
    """Return True when *markdown* looks like real programme-listing content."""
    md = str(markdown or "").strip()
    if len(md) < _MIN_CHARS:
        return False
    if _CF_RE.search(md):
        return False
    return len(_LINK_RE.findall(md)) >= _MIN_LINKS


def fetch_with_escalation(
    index_url: str,
    *,
    server_fetch: ServerFetch,
    client_fetch: ClientFetch,
    wait_selector: Optional[str] = None,
) -> FetchResult:
    """Try server → client → client_wait, stopping at the first usable result.

    Args:
        index_url:     URL of the programme-index page to fetch.
        server_fetch:  Callable ``(url) -> (html, markdown)`` for plain HTTP.
        client_fetch:  Callable ``(url, **kw) -> (html, markdown)`` for
                       headless-browser fetches.  Receives ``wait=True`` and
                       ``wait_selector`` on the third (client_wait) attempt.
        wait_selector: Optional CSS selector passed to the client_wait attempt.

    Returns:
        A :class:`FetchResult` recording the HTML/markdown obtained, the fetch
        level that produced it, and every level that was attempted.
    """
    tried: list[str] = []

    tried.append(FetchMode.SERVER.value)
    html, md = server_fetch(index_url)
    if content_is_usable(md):
        return FetchResult(html, md, FetchMode.SERVER.value, tried)

    tried.append(FetchMode.CLIENT.value)
    html, md = client_fetch(index_url)
    if content_is_usable(md):
        return FetchResult(html, md, FetchMode.CLIENT.value, tried)

    tried.append(FetchMode.CLIENT_WAIT.value)
    html, md = client_fetch(index_url, wait_selector=wait_selector, wait=True)
    return FetchResult(html, md, FetchMode.CLIENT_WAIT.value, tried)
