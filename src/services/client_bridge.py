"""Client bridge registry for server-side browser automation dispatch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Optional
import uuid


@dataclass
class ClientSession:
    """One connected client session metadata."""

    client_id: str
    client_name: str
    platform: str
    arch: str
    workdir: str
    capabilities: dict[str, Any]
    last_seen_epoch: float = field(default_factory=time.time)


class ClientRegistry:
    """In-memory connected-client registry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, ClientSession] = {}

    def register(self, session: ClientSession) -> None:
        """Insert or replace a client session."""
        with self._lock:
            self._sessions[session.client_id] = session

    def heartbeat(self, client_id: str) -> bool:
        """Update last-seen timestamp for the given client id."""
        with self._lock:
            session = self._sessions.get(client_id)
            if not session:
                return False
            session.last_seen_epoch = time.time()
            return True

    def remove(self, client_id: str) -> None:
        """Remove one client from registry."""
        with self._lock:
            self._sessions.pop(client_id, None)

    def get(self, client_id: str) -> Optional[ClientSession]:
        """Return one client session by id."""
        with self._lock:
            return self._sessions.get(client_id)

    def list_clients(self) -> list[dict[str, Any]]:
        """List all clients sorted by latest activity."""
        with self._lock:
            sessions = sorted(
                self._sessions.values(),
                key=lambda row: float(row.last_seen_epoch),
                reverse=True,
            )
            return [
                {
                    "client_id": row.client_id,
                    "client_name": row.client_name,
                    "platform": row.platform,
                    "arch": row.arch,
                    "workdir": row.workdir,
                    "capabilities": dict(row.capabilities or {}),
                    "last_seen_epoch": float(row.last_seen_epoch),
                }
                for row in sessions
            ]

    def select_client_id(self, preferred_client_id: Optional[str]) -> Optional[str]:
        """Select a browser-automation capable client id."""
        with self._lock:
            if preferred_client_id:
                preferred = self._sessions.get(preferred_client_id)
                if preferred and _supports_browser_automation(preferred):
                    return preferred.client_id

            candidates = [
                row for row in self._sessions.values() if _supports_browser_automation(row)
            ]
            if not candidates:
                return None
            latest = max(candidates, key=lambda row: float(row.last_seen_epoch))
            return latest.client_id

    def has_available_client(self) -> bool:
        """Whether there is at least one browser-capable client online."""
        return self.select_client_id(preferred_client_id=None) is not None


def _supports_browser_automation(session: ClientSession) -> bool:
    capabilities = session.capabilities or {}
    return bool(capabilities.get("browser_automation"))


class ClientUnavailableError(RuntimeError):
    """Raised when client RPC cannot complete."""


class ClientRpcBroker:
    """Tracks pending client RPC requests and correlates async responses."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._lock = threading.RLock()
        self._pending: dict[str, asyncio.Future] = {}
        self._pending_loop: dict[str, asyncio.AbstractEventLoop] = {}
        self._pending_client: dict[str, str] = {}

    def create_pending(self, client_id: str) -> tuple[str, asyncio.Future]:
        """Create a pending request future for one target client."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        request_id = uuid.uuid4().hex
        with self._lock:
            self._pending[request_id] = future
            self._pending_loop[request_id] = loop
            self._pending_client[request_id] = str(client_id or "").strip()
        return request_id, future

    async def wait_for_response(self, request_id: str) -> dict[str, Any]:
        """Wait for one request response with timeout."""
        with self._lock:
            future = self._pending.get(request_id)
        if not future:
            raise ClientUnavailableError(f"request not pending: {request_id}")

        try:
            payload = await asyncio.wait_for(future, timeout=self.timeout_seconds)
            return dict(payload or {})
        except asyncio.TimeoutError as exc:
            with self._lock:
                self._pending.pop(request_id, None)
                self._pending_loop.pop(request_id, None)
                self._pending_client.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise ClientUnavailableError(f"request timed out: {request_id}") from exc
        finally:
            if future.done():
                with self._lock:
                    self._pending.pop(request_id, None)
                    self._pending_loop.pop(request_id, None)
                    self._pending_client.pop(request_id, None)

    def resolve(self, request_id: str, payload: dict[str, Any]) -> bool:
        """Resolve one pending request (thread-safe across event loops)."""
        with self._lock:
            future = self._pending.get(request_id)
            loop = self._pending_loop.get(request_id)
        if not future or future.done():
            return False
        result = dict(payload or {})
        # Use call_soon_threadsafe to resolve futures that may live in a
        # different event loop (e.g. when the skill handler runs in a thread
        # with its own asyncio.run() loop).
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(future.set_result, result)
                return True
            except RuntimeError:
                pass
        future.set_result(result)
        return True

    def fail(self, request_id: str, message: str) -> bool:
        """Fail one pending request (thread-safe across event loops)."""
        with self._lock:
            future = self._pending.get(request_id)
            loop = self._pending_loop.get(request_id)
        if not future or future.done():
            return False
        exc = ClientUnavailableError(str(message or "client rpc failed"))
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(future.set_exception, exc)
                return True
            except RuntimeError:
                pass
        future.set_exception(exc)
        return True

    def fail_all_for_client(self, client_id: str, message: str) -> int:
        """Fail all pending requests for a disconnected client."""
        target = str(client_id or "").strip()
        if not target:
            return 0
        with self._lock:
            request_ids = [
                req_id
                for req_id, owner in self._pending_client.items()
                if owner == target
            ]
        failed = 0
        for req_id in request_ids:
            if self.fail(req_id, message):
                failed += 1
        return failed
