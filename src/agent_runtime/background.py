"""BackgroundManager — async background task execution (s08 pattern).

Runs skill calls as asyncio tasks so the agent loop can continue
working while slow operations complete. Results are collected via
a notification queue and injected before the next LLM call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class BackgroundManager:
    """Manage background skill executions via asyncio tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._counter = 0

    def run(
        self,
        coro: Any,
        *,
        skill_name: str,
        args_preview: str = "",
    ) -> str:
        """Schedule a coroutine as a background task. Returns a task ID."""
        self._counter += 1
        task_id = f"bg_{self._counter}"

        async_task = asyncio.ensure_future(self._execute(task_id, coro))

        self._tasks[task_id] = {
            "id": task_id,
            "skill": skill_name,
            "args_preview": args_preview[:200],
            "status": "running",
            "started_at": time.time(),
            "async_task": async_task,
            "result_preview": None,
        }

        logger.info("[Background] Started %s: %s", task_id, skill_name)
        return task_id

    async def _execute(self, task_id: str, coro: Any) -> None:
        """Run the coroutine and enqueue notification on completion."""
        try:
            result = await coro
            result_str = json.dumps(result, ensure_ascii=False, default=str)
            result_preview = result_str[:500]
        except Exception as exc:
            logger.warning("[Background] %s failed: %s", task_id, exc)
            result_preview = f"Error: {exc}"

        entry = self._tasks.get(task_id)
        if entry:
            entry["status"] = "completed"
            entry["result_preview"] = result_preview

        self._notifications.append({
            "task_id": task_id,
            "skill": self._tasks[task_id]["skill"] if task_id in self._tasks else "unknown",
            "result": result_preview,
        })
        logger.info("[Background] Completed %s", task_id)

    def drain_notifications(self) -> list[dict[str, Any]]:
        """Collect and clear all pending notifications. Thread-safe."""
        if not self._notifications:
            return []
        notifs = list(self._notifications)
        self._notifications.clear()
        return notifs

    def check(self, task_id: str) -> dict[str, Any]:
        """Check the status of a specific background task."""
        entry = self._tasks.get(task_id)
        if not entry:
            return {"error": f"Unknown background task: {task_id}"}
        return {
            "id": entry["id"],
            "skill": entry["skill"],
            "status": entry["status"],
            "result_preview": entry.get("result_preview"),
        }

    def list_all(self) -> list[dict[str, Any]]:
        """List all background tasks with their status."""
        return [
            {
                "id": e["id"],
                "skill": e["skill"],
                "status": e["status"],
                "args_preview": e["args_preview"],
                "result_preview": e.get("result_preview"),
            }
            for e in self._tasks.values()
        ]

    def has_running(self) -> bool:
        return any(e["status"] == "running" for e in self._tasks.values())
