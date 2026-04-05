"""Runtime helpers for ``adm-agent-client``."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import json
import logging
import os
import platform
from typing import Any

import websockets

from src.client.config import ClientConfig
from src.client.native_browser import fetch_browser_payload

logger = logging.getLogger(__name__)


import urllib.request
import urllib.error

@dataclass
class ClientConnectivity:
    """Connectivity probe result."""

    connected: bool
    message: str
    endpoint: str


def build_server_endpoint(config: ClientConfig) -> str:
    """Build human-readable server endpoint string."""
    return config.server_url


def build_ws_url(config: ClientConfig) -> str:
    """Build websocket URL for client bridge."""
    url = config.server_url.rstrip("/")
    if url.startswith("https://"):
        return url.replace("https://", "wss://", 1) + "/clients/ws"
    if url.startswith("http://"):
        return url.replace("http://", "ws://", 1) + "/clients/ws"
    # Fallback if no scheme
    return f"ws://{url}/clients/ws"


def render_fetch_command(template: str, *, url: str, page_type_hint: str) -> str:
    """Render fetch command template with crawl placeholders."""
    return str(template).format(
        url=str(url or "").strip(),
        page_type_hint=str(page_type_hint or "").strip(),
    )


async def probe_server(
    config: ClientConfig,
    timeout_seconds: float = 3.0,
) -> ClientConnectivity:
    """Probe HTTP reachability of configured serve endpoint."""
    endpoint = build_server_endpoint(config)
    url = endpoint.rstrip("/") + "/status"

    def _probe() -> None:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=max(0.1, float(timeout_seconds))) as _:
            pass

    try:
        await asyncio.to_thread(_probe)
        return ClientConnectivity(
            connected=True,
            message="reachable",
            endpoint=endpoint,
        )
    except Exception as exc:
        return ClientConnectivity(
            connected=False,
            message=str(exc),
            endpoint=endpoint,
        )


class ClientRuntime:
    """Lightweight runtime wrapper for client commands."""

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.fetch_command_template = os.environ.get("ADM_AGENT_CLIENT_FETCH_CMD", "").strip()
        self.native_detail_limit = int(os.environ.get("ADM_AGENT_CLIENT_DETAIL_LIMIT", "4") or "4")
        self.heartbeat_interval_seconds = 15

    async def start_once(self) -> ClientConnectivity:
        """One-shot start probe used by CLI start/status."""
        return await probe_server(self.config)

    async def run_forever(self) -> None:
        """Run websocket client loop and serve browser-automation RPC requests."""
        ws_url = build_ws_url(self.config)
        while True:
            try:
                async with websockets.connect(ws_url, max_size=25_000_000) as websocket:
                    providers = ["native_browser"]
                    if self.fetch_command_template:
                        providers.append("external_command")
                    await self._send_json(
                        websocket,
                        {
                            "type": "register",
                            "client_id": self.config.client_id,
                            "client_name": self.config.client_name,
                            "platform": platform.system().lower(),
                            "arch": platform.machine().lower(),
                            "workdir": self.config.workdir,
                            "capabilities": {
                                "browser_automation": True,
                                "providers": providers,
                            },
                        },
                    )
                    logger.info("Client websocket connected: %s", ws_url)
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket))
                    try:
                        await self._rpc_loop(websocket)
                    finally:
                        heartbeat_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat_task
            except Exception as exc:
                logger.warning("Client websocket loop error: %s", exc)
                await asyncio.sleep(3)

    async def _rpc_loop(self, websocket: websockets.WebSocketClientProtocol) -> None:
        rpc_semaphore = asyncio.Semaphore(5)  # max 5 concurrent RPC handlers
        rpc_tasks: set[asyncio.Task] = set()

        while True:
            raw = await websocket.recv()
            message = _loads_json(raw)
            msg_type = str(message.get("type") or "").strip().lower()
            if msg_type == "rpc_request":
                request_id = str(message.get("request_id") or "").strip()
                action = str(message.get("action") or "").strip()
                payload = message.get("payload") or {}
                url = payload.get("url", "") if isinstance(payload, dict) else ""
                logger.info(
                    "[Client RPC] Received request: id=%s action=%s url=%s",
                    request_id,
                    action,
                    url,
                )

                async def _bounded_handle(ws, msg, sem):
                    async with sem:
                        await self._handle_rpc_request(ws, msg)

                task = asyncio.create_task(_bounded_handle(websocket, message, rpc_semaphore))
                rpc_tasks.add(task)
                task.add_done_callback(rpc_tasks.discard)

    async def _heartbeat_loop(self, websocket: websockets.WebSocketClientProtocol) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            await self._send_json(
                websocket,
                {
                    "type": "heartbeat",
                    "client_id": self.config.client_id,
                },
            )

    async def _handle_rpc_request(
        self,
        websocket: websockets.WebSocketClientProtocol,
        message: dict[str, Any],
    ) -> None:
        request_id = str(message.get("request_id") or "").strip()
        action = str(message.get("action") or "").strip().lower()
        payload = message.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}

        if not request_id:
            await self._send_json(
                websocket,
                {
                    "type": "rpc_error",
                    "request_id": None,
                    "message": "missing request_id",
                },
            )
            return

        if action != "fetch_browser_payload":
            await self._send_json(
                websocket,
                {
                    "type": "rpc_error",
                    "request_id": request_id,
                    "message": f"unsupported action: {action or '<empty>'}",
                },
            )
            return

        try:
            fetch_url = str(payload_dict.get("url") or "").strip()
            fetch_hint = str(payload_dict.get("page_type_hint") or "auto")
            logger.info("[Client RPC] Starting browser fetch: url=%s hint=%s", fetch_url, fetch_hint)
            response_payload = await self._fetch_browser_payload(
                url=fetch_url,
                page_type_hint=fetch_hint,
            )
            response_payload = dict(response_payload or {})
            if self.config.policy_profile is not None:
                response_payload["policy_profile"] = self.config.policy_profile.model_dump(
                    mode="json"
                )
            html_len = len(str(response_payload.get("html_content") or ""))
            batch_count = len(response_payload.get("detail_pages_batch") or [])
            logger.info(
                "[Client RPC] Browser fetch completed: request_id=%s html=%d bytes, detail_batch=%d pages",
                request_id,
                html_len,
                batch_count,
            )
            await self._send_json(
                websocket,
                {
                    "type": "rpc_result",
                    "request_id": request_id,
                    "payload": response_payload,
                },
            )
        except Exception as exc:
            logger.error("[Client RPC] Browser fetch failed: request_id=%s error=%s", request_id, exc)
            await self._send_json(
                websocket,
                {
                    "type": "rpc_error",
                    "request_id": request_id,
                    "message": str(exc),
                },
            )

    async def _fetch_browser_payload(
        self,
        *,
        url: str,
        page_type_hint: str,
    ) -> dict[str, Any]:
        if not self.fetch_command_template:
            return await asyncio.to_thread(
                fetch_browser_payload,
                url=url,
                page_type_hint=page_type_hint,
                detail_limit=max(1, int(self.native_detail_limit)),
            )

        command = render_fetch_command(
            self.fetch_command_template,
            url=url,
            page_type_hint=page_type_hint,
        )
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self.config.workdir or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        if process.returncode != 0:
            err_text = (stderr or b"").decode("utf-8", errors="ignore").strip()
            raise RuntimeError(err_text or f"fetch command failed with exit={process.returncode}")

        output = (stdout or b"").decode("utf-8", errors="ignore").strip()
        if not output:
            raise RuntimeError("fetch command returned empty output")
        parsed = json.loads(output)
        if not isinstance(parsed, dict):
            raise RuntimeError("fetch command output must be a JSON object")
        return parsed

    _ws_send_lock: asyncio.Lock | None = None

    async def _send_json(
        self,
        websocket: websockets.WebSocketClientProtocol,
        payload: dict[str, Any],
    ) -> None:
        # Concurrent RPC handlers + heartbeat can interleave sends; serialize them.
        if self._ws_send_lock is None:
            self._ws_send_lock = asyncio.Lock()
        async with self._ws_send_lock:
            await websocket.send(json.dumps(payload, ensure_ascii=False))


def _loads_json(raw: Any) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("websocket payload must be json object")
    return parsed
