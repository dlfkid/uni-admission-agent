"""ProtocolManager — generic request-response FSM for team coordination (s10).

Provides a reusable pattern for any protocol that follows:
    requester sends request (pending) → responder approves/rejects

Use cases: shutdown requests, plan approval, permission gates, etc.
Requests are persisted to ``.team/protocols.json`` and correlated via
unique ``request_id``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROTOCOLS_DIR = Path(".team")


class ProtocolManager:
    """Track request-response exchanges between agents."""

    VALID_STATUSES = {"pending", "approved", "rejected"}

    def __init__(self, protocols_dir: Path | None = None) -> None:
        self.dir = protocols_dir or PROTOCOLS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.dir / "protocols.json"
        self.requests: dict[str, dict[str, Any]] = self._load()

    def create_request(
        self,
        protocol: str,
        sender: str,
        target: str,
        description: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new protocol request. Returns the request record."""
        req_id = str(uuid.uuid4())[:8]
        record: dict[str, Any] = {
            "request_id": req_id,
            "protocol": protocol,
            "from": sender,
            "target": target,
            "description": description,
            "payload": payload or {},
            "status": "pending",
            "response_reason": "",
            "created_at": time.time(),
            "resolved_at": None,
        }
        self.requests[req_id] = record
        self._save()
        logger.info(
            "[Protocol] Created %s request %s: %s -> %s",
            protocol, req_id, sender, target,
        )
        return record

    def respond(
        self,
        request_id: str,
        approve: bool,
        reason: str = "",
    ) -> dict[str, Any] | None:
        """Approve or reject a pending request."""
        record = self.requests.get(request_id)
        if record is None:
            return None
        if record["status"] != "pending":
            return record  # already resolved

        record["status"] = "approved" if approve else "rejected"
        record["response_reason"] = reason
        record["resolved_at"] = time.time()
        self._save()
        logger.info(
            "[Protocol] Request %s %s: %s",
            request_id,
            record["status"],
            reason[:80],
        )
        return record

    def get(self, request_id: str) -> dict[str, Any] | None:
        return self.requests.get(request_id)

    def list_by_status(self, status: str | None = None) -> list[dict[str, Any]]:
        """List requests, optionally filtered by status."""
        if status:
            return [r for r in self.requests.values() if r["status"] == status]
        return list(self.requests.values())

    def list_for_target(self, target: str, status: str | None = None) -> list[dict[str, Any]]:
        """List requests targeting a specific agent."""
        results = [r for r in self.requests.values() if r["target"] == target]
        if status:
            results = [r for r in results if r["status"] == status]
        return results

    def render(self) -> str:
        """Render all requests as compact text."""
        if not self.requests:
            return "(no protocol requests)"
        lines: list[str] = []
        status_icon = {"pending": "[?]", "approved": "[v]", "rejected": "[x]"}
        for r in self.requests.values():
            icon = status_icon.get(r["status"], "[?]")
            lines.append(
                f"{icon} {r['request_id']} ({r['protocol']}): "
                f"{r['from']} -> {r['target']} | {r['description'][:60]}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                return {r["request_id"]: r for r in data}
            except (json.JSONDecodeError, OSError, KeyError):
                pass
        return {}

    def _save(self) -> None:
        records = list(self.requests.values())
        self.store_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
