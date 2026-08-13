"""Native browser automation fetcher (no Playwright dependency)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import signal
import shutil
import threading
import tempfile
import time
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import websockets


@dataclass(frozen=True)
class AnchorLink:
    """Single anchor candidate captured from a page."""

    url: str
    text: str
    class_name: str


def _same_page(url: str, base: str) -> bool:
    """True when *url* is the same page as *base*, ignoring a trailing
    slash and any ``#fragment``.

    Fragments are client-side scroll anchors ("skip to main content",
    in-page nav) — never a different page by HTML spec, so two URLs that
    differ only by fragment must count as the same page. A bare
    ``rstrip("/")`` comparison misses this: a "skip to main content" link
    on an index page whose OWN url happens to contain a keyword like
    "/programme" (e.g. Lingnan's ``.../sgs/programmes-on-offer``) resolves
    to ``base_url + "#main"``, which is not string-equal to ``base`` even
    though it is the same page — so it slipped through the old check,
    matched the keyword filter below purely because of the page's own
    url, and got selected as the ONLY "detail candidate", pushing out the
    real programme links entirely (confirmed live: a full crawl imported
    one garbage record named after the index page itself instead of the
    requested programmes).
    """
    def _strip(value: str) -> str:
        return value.split("#", 1)[0].rstrip("/")
    return _strip(url) == _strip(base)


def _same_section(base: str, candidate: str) -> bool:
    """True when *candidate* is nested under *base*'s own path.

    A minimal, local counterpart to ``_same_section`` in
    ``src/services/thin_page_supplement.py`` (deliberately not imported —
    this module ships standalone in the client binary and stays
    dependency-free from the server's service layer). Same rationale: real
    detail links for an index page live NESTED under that page's own path
    (Lingnan's index is ``.../sgs/programmes-on-offer``, its real
    programme links are ``.../sgs/programmes-on-offer/<slug>``), while
    sitewide chrome that happens to contain a matching keyword — a
    language switcher (``/cht/sgs/programmes-on-offer``), a news item
    (``/sgs/news-events/.../postgraduate-conference2026``), a "for current
    students" nav link — lives elsewhere on the same host. A bare
    keyword-in-url filter can't tell these apart; nesting can.
    """
    try:
        base_parts, cand_parts = urlparse(base), urlparse(candidate)
    except ValueError:
        return False
    if base_parts.netloc.lower() != cand_parts.netloc.lower():
        return False
    base_path = base_parts.path.rstrip("/")
    if not base_path:
        return True
    return cand_parts.path == base_path or cand_parts.path.startswith(base_path + "/")


def select_detail_links(
    *,
    index_url: str,
    anchors: list[AnchorLink],
    limit: int = 8,
) -> list[AnchorLink]:
    """Select likely detail links from index anchors."""
    if limit <= 0:
        return []
    base = str(index_url or "").strip()
    parsed = urlparse(base)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if "polyu.edu.hk" in host and "/study/pg/" in path:
        rows = [item for item in anchors if "/study/pg/tpg/" in item.url]
        return rows[:limit]

    nested_rows = []
    other_rows = []
    for item in anchors:
        parsed_url = urlparse(item.url)
        if parsed_url.netloc != parsed.netloc:
            continue
        if _same_page(item.url, base):
            continue
        text = item.url.lower()
        if not any(token in text for token in (
            "/programme", "/program", "/course", "/master", "/msc", "/ma-",
            "/degree", "/undergraduate", "/postgraduate",
        )):
            continue
        # Prefer links nested under the index page's own path — sitewide
        # chrome that merely shares a keyword (see _same_section docstring)
        # goes to the fallback bucket instead.
        if _same_section(base, item.url):
            nested_rows.append(item)
        else:
            other_rows.append(item)
    # Once ANY nested match exists, trust it exclusively — mixing in
    # sitewide noise to pad out to `limit` is worse than returning fewer,
    # all-correct candidates (missing data beats wrong data, every time).
    # Only fall back to the keyword-only bucket when nesting found nothing
    # at all (e.g. a layout where detail links genuinely aren't nested
    # under the index page's own path).
    if nested_rows:
        return nested_rows[:limit]
    return other_rows[:limit]


class _BrowserProcess:
    """Manages a persistent Chrome/Edge process for CDP automation."""

    _instance: _BrowserProcess | None = None
    _lock = threading.Lock()

    def __init__(self, *, browser_path: str, debug_port: int, launch_timeout: float) -> None:
        self.browser_path = browser_path
        self.debug_port = int(debug_port)
        self.launch_timeout = max(3.0, float(launch_timeout))
        self.process_pid: int | None = None
        self.temp_profile_dir: str | None = None
        self.started_by_me = False

    @classmethod
    def get_or_create(cls, *, browser_path: str, debug_port: int = 9222, launch_timeout: float = 20.0) -> _BrowserProcess:
        """Get the singleton browser instance, starting Chrome if needed."""
        with cls._lock:
            if cls._instance is not None and _is_debugger_ready(cls._instance.debug_port):
                return cls._instance
            instance = cls(browser_path=browser_path, debug_port=debug_port, launch_timeout=launch_timeout)
            instance.start()
            cls._instance = instance
            return instance

    def start(self) -> None:
        if _is_debugger_ready(self.debug_port):
            return

        self.temp_profile_dir = tempfile.mkdtemp(prefix="adm-agent-client-profile-")
        args = [
            self.browser_path,
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self.temp_profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "about:blank",
        ]
        self.process_pid = int(os.spawnv(os.P_NOWAIT, self.browser_path, args))
        self.started_by_me = True

        deadline = time.time() + self.launch_timeout
        while time.time() < deadline:
            if _is_debugger_ready(self.debug_port):
                return
            time.sleep(0.25)
        raise RuntimeError("Failed to start browser with remote debugging enabled")

    def close(self) -> None:
        if self.started_by_me and self.process_pid is not None:
            try:
                os.kill(self.process_pid, signal.SIGTERM)
            except Exception:
                try:
                    if hasattr(signal, "SIGKILL"):
                        os.kill(self.process_pid, signal.SIGKILL)
                except Exception:
                    pass
        if self.temp_profile_dir:
            shutil.rmtree(self.temp_profile_dir, ignore_errors=True)
        with self._lock:
            if _BrowserProcess._instance is self:
                _BrowserProcess._instance = None


def fetch_browser_payload(
    *,
    url: str,
    page_type_hint: str = "auto",
    detail_limit: int = 8,
    browser_path: str | None = None,
    debug_port: int = 9222,
    launch_timeout: float = 20.0,
) -> dict[str, Any]:
    """Fetch browser payload by driving local Chrome/Edge via CDP.

    Reuses a persistent Chrome instance (singleton). Each call opens a new
    tab via /json/new and closes it when done. Safe for concurrent calls.
    """
    target_url = str(url or "").strip()
    if not target_url:
        raise ValueError("url is required")

    resolved_browser = _resolve_browser_path(browser_path)
    _BrowserProcess.get_or_create(
        browser_path=resolved_browser,
        debug_port=debug_port,
        launch_timeout=launch_timeout,
    )
    # Chrome is running; each call just opens a new tab (via _open_debug_target)
    return asyncio.run(
        _fetch_payload_async(
            url=target_url,
            page_type_hint=page_type_hint,
            detail_limit=max(1, int(detail_limit)),
            debug_port=debug_port,
        )
    )


async def _fetch_payload_async(
    *,
    url: str,
    page_type_hint: str,
    detail_limit: int,
    debug_port: int,
) -> dict[str, Any]:
    normalized = str(page_type_hint or "auto").strip().lower()
    if normalized not in {"auto", "index", "detail"}:
        normalized = "auto"

    index_html, anchors = await _fetch_page_html_and_anchors(url=url, debug_port=debug_port)
    if normalized == "detail":
        return {"html_content": index_html}

    selected = select_detail_links(index_url=url, anchors=anchors, limit=detail_limit)
    selected_link_texts: dict[str, str] = {}
    selected_urls: list[str] = []

    for item in selected:
        selected_urls.append(item.url)
        if item.text:
            selected_link_texts[item.url] = item.text

    # NOTE: We intentionally do NOT pre-fetch detail pages here.
    # Pre-fetching 4-8 pages sequentially takes 2+ minutes and causes
    # RPC timeouts.  The agent fetches detail pages individually instead.

    payload: dict[str, Any] = {
        "html_content": index_html,
        "detail_pages_batch": [],
        "selected_urls": selected_urls,
    }
    if selected_link_texts:
        payload["selected_link_texts"] = selected_link_texts
    return payload


async def _fetch_page_html_and_anchors(
    *,
    url: str,
    debug_port: int,
) -> tuple[str, list[AnchorLink]]:
    target = _open_debug_target(debug_port)
    target_id = str(target.get("id") or "").strip()
    ws_url = str(target.get("webSocketDebuggerUrl") or "").strip()
    if not ws_url:
        raise RuntimeError("CDP websocket endpoint missing")

    try:
        async with websockets.connect(ws_url, max_size=25_000_000) as websocket:
            client = _CdpClient(websocket)
            await client.call("Page.enable")
            await client.call("Runtime.enable")
            await client.call("Network.enable")
            await client.call("Page.navigate", {"url": url})
            await _wait_page_ready(client)

            html_result = await client.call(
                "Runtime.evaluate",
                {
                    "expression": "document.documentElement.outerHTML",
                    "returnByValue": True,
                },
            )
            html = str(((html_result or {}).get("result") or {}).get("value") or "")

            anchor_result = await client.call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "(() => Array.from(document.querySelectorAll('a[href]')).map((a) => ({"
                        "href: a.getAttribute('href') || '', "
                        "text: (a.textContent || '').trim(), "
                        "className: a.className || ''"
                        "})))()"
                    ),
                    "returnByValue": True,
                },
            )
            anchors = _to_anchor_links(url=url, raw=((anchor_result or {}).get("result") or {}).get("value"))
            return html, anchors
    finally:
        if target_id:
            _close_debug_target(debug_port=debug_port, target_id=target_id)


class _CdpClient:
    def __init__(self, websocket: websockets.WebSocketClientProtocol) -> None:
        self.websocket = websocket
        self.counter = 0

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        self.counter += 1
        req_id = self.counter
        payload = {
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        await self.websocket.send(json.dumps(payload, ensure_ascii=False))
        return await self._wait_response(req_id=req_id, timeout=max(1.0, float(timeout)))

    async def _wait_response(self, *, req_id: int, timeout: float) -> dict[str, Any]:
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise TimeoutError(f"CDP response timeout for id={req_id}")
            raw = await asyncio.wait_for(self.websocket.recv(), timeout=remain)
            message = json.loads(raw)
            if int(message.get("id") or -1) != req_id:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP error: {message['error']}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}


async def _wait_page_ready(client: _CdpClient, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = await client.call(
            "Runtime.evaluate",
            {
                "expression": "document.readyState",
                "returnByValue": True,
            },
            timeout=8.0,
        )
        state = str(((result or {}).get("result") or {}).get("value") or "")
        if state == "complete":
            await asyncio.sleep(1.0)
            return
        await asyncio.sleep(0.35)
    await asyncio.sleep(1.0)


def _to_anchor_links(*, url: str, raw: Any) -> list[AnchorLink]:
    if not isinstance(raw, list):
        return []
    rows: list[AnchorLink] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        full = urljoin(url, href)
        parsed = urlparse(full)
        if parsed.scheme not in {"http", "https"}:
            continue
        if full in seen:
            continue
        seen.add(full)
        rows.append(
            AnchorLink(
                url=full,
                text=str(item.get("text") or "").strip(),
                class_name=str(item.get("className") or "").strip(),
            )
        )
    return rows


def _resolve_browser_path(explicit_path: str | None) -> str:
    if explicit_path:
        candidate = str(explicit_path).strip()
        if Path(candidate).exists():
            return candidate
        from_path = shutil.which(candidate)
        if from_path:
            return str(from_path)
        raise RuntimeError(f"Browser executable not found: {candidate}")

    for candidate in _default_browser_candidates():
        if Path(candidate).exists():
            return candidate
        from_path = shutil.which(candidate)
        if from_path:
            return str(from_path)
    raise RuntimeError(
        "No supported browser executable found. Please install Chrome or Edge, "
        "or pass --browser-path explicitly."
    )


def _default_browser_candidates() -> list[str]:
    system_name = platform.system().lower()
    if system_name == "darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "google-chrome",
            "chromium",
            "microsoft-edge",
        ]
    if system_name == "windows":
        return [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "chrome.exe",
            "msedge.exe",
        ]
    return [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
    ]


def _json_http(url: str, *, method: str = "GET") -> dict[str, Any]:
    request = Request(url=url, method=method)
    with urlopen(request, timeout=8) as response:
        payload = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(payload)
    return parsed if isinstance(parsed, dict) else {}


def _is_debugger_ready(port: int) -> bool:
    try:
        _json_http(f"http://127.0.0.1:{int(port)}/json/version")
        return True
    except Exception:
        return False


def _open_debug_target(debug_port: int) -> dict[str, Any]:
    return _json_http(f"http://127.0.0.1:{int(debug_port)}/json/new", method="PUT")


def _close_debug_target(*, debug_port: int, target_id: str) -> None:
    try:
        _json_http(f"http://127.0.0.1:{int(debug_port)}/json/close/{target_id}")
    except Exception:
        pass
