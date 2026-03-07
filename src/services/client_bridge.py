"""Client bridge registry for server-side browser automation dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any, Optional


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

