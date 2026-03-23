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
from collections.abc import Awaitable
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
        coro: Awaitable[Any],
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

    async def _execute(self, task_id: str, coro: Awaitable[Any]) -> None:
        """Run the coroutine and enqueue notification on completion."""
        try:
            result = await coro
            result_str = json.dumps(result, ensure_ascii=False, default=str)
            # Keep up to 50 000 chars so browser HTML / markdown results survive
            result_kept = result_str[:50_000]
        except Exception as exc:
            logger.warning("[Background] %s failed: %s", task_id, exc)
            result_kept = f"Error: {exc}"

        entry = self._tasks.get(task_id)
        if entry:
            entry["status"] = "completed"
            entry["result"] = result_kept

        self._notifications.append({
            "task_id": task_id,
            "skill": self._tasks[task_id]["skill"] if task_id in self._tasks else "unknown",
            "result": result_kept,
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
        """Check the status of a specific background task (non-blocking)."""
        entry = self._tasks.get(task_id)
        if not entry:
            return {"error": f"Unknown background task: {task_id}"}
        return {
            "id": entry["id"],
            "skill": entry["skill"],
            "status": entry["status"],
            "result": entry.get("result"),
        }

    async def wait(self, task_id: str, timeout: float = 300) -> dict[str, Any]:
        """Block until a background task completes or timeout (seconds)."""
        entry = self._tasks.get(task_id)
        if not entry:
            return {"error": f"Unknown background task: {task_id}"}
        if entry["status"] == "completed":
            return {
                "id": entry["id"],
                "skill": entry["skill"],
                "status": "completed",
                "result": entry.get("result"),
            }
        async_task = entry.get("async_task")
        if async_task is None:
            return {"error": f"No async task for {task_id}"}
        try:
            await asyncio.wait_for(asyncio.shield(async_task), timeout=timeout)
        except asyncio.TimeoutError:
            return {
                "id": entry["id"],
                "skill": entry["skill"],
                "status": "timeout",
                "result": None,
            }
        return {
            "id": entry["id"],
            "skill": entry["skill"],
            "status": entry.get("status", "completed"),
            "result": entry.get("result"),
        }

    def list_all(self) -> list[dict[str, Any]]:
        """List all background tasks with their status."""
        return [
            {
                "id": e["id"],
                "skill": e["skill"],
                "status": e["status"],
                "args_preview": e["args_preview"],
                "result": e.get("result"),
            }
            for e in self._tasks.values()
        ]

    def has_running(self) -> bool:
        return any(e["status"] == "running" for e in self._tasks.values())
