"""TaskManager — file-persisted task DAG (s07 pattern).

Each task is a JSON file in ``.tasks/``. Tasks have:
- ``id``, ``subject``, ``description``
- ``status``: pending → in_progress → completed
- ``blockedBy``: list of task IDs that must complete first
- ``blocks``: list of task IDs this task blocks (reverse edge)
- ``owner``: optional, for multi-agent assignment (s09+)

Completing a task auto-clears its ID from all downstream ``blockedBy`` lists.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TASKS_DIR = Path(".tasks")


class TaskManager:
    """Persistent task graph backed by per-task JSON files."""

    VALID_STATUSES = {"pending", "in_progress", "completed"}

    def __init__(self, tasks_dir: Path | None = None) -> None:
        self.dir = tasks_dir or TASKS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._next_id = self._max_id() + 1

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
    ) -> dict[str, Any]:
        """Create a new task. Returns the task dict."""
        task_id = self._next_id
        self._next_id += 1

        task: dict[str, Any] = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blockedBy": blocked_by or [],
            "blocks": [],
            "owner": "",
        }

        # Update reverse edges: for each upstream, add this task to its blocks
        for upstream_id in task["blockedBy"]:
            upstream = self._load(upstream_id)
            if upstream and task_id not in upstream["blocks"]:
                upstream["blocks"].append(task_id)
                self._save(upstream)

        self._save(task)
        logger.info("[TaskManager] Created task %d: %s", task_id, subject)
        return task

    def get(self, task_id: int) -> dict[str, Any] | None:
        """Get a single task by ID."""
        return self._load(task_id)

    def update(
        self,
        task_id: int,
        *,
        status: str | None = None,
        add_blocked_by: list[int] | None = None,
        add_blocks: list[int] | None = None,
        owner: str | None = None,
    ) -> dict[str, Any] | None:
        """Update a task's status, dependencies, or owner."""
        task = self._load(task_id)
        if task is None:
            return None

        if status:
            if status not in self.VALID_STATUSES:
                raise ValueError(f"Invalid status: {status}")
            task["status"] = status
            if status == "completed":
                self._clear_dependency(task_id)

        if add_blocked_by:
            for bid in add_blocked_by:
                if bid not in task["blockedBy"]:
                    task["blockedBy"].append(bid)
                # Add reverse edge
                upstream = self._load(bid)
                if upstream and task_id not in upstream["blocks"]:
                    upstream["blocks"].append(task_id)
                    self._save(upstream)

        if add_blocks:
            for bid in add_blocks:
                if bid not in task["blocks"]:
                    task["blocks"].append(bid)
                # Add forward edge
                downstream = self._load(bid)
                if downstream and task_id not in downstream["blockedBy"]:
                    downstream["blockedBy"].append(task_id)
                    self._save(downstream)

        if owner is not None:
            task["owner"] = owner

        self._save(task)
        return task

    def list_all(self) -> list[dict[str, Any]]:
        """List all tasks sorted by ID."""
        tasks: list[dict[str, Any]] = []
        for f in sorted(self.dir.glob("task_*.json")):
            try:
                tasks.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return tasks

    def ready_tasks(self) -> list[dict[str, Any]]:
        """Return tasks that are pending with no unresolved blockers."""
        return [
            t for t in self.list_all()
            if t["status"] == "pending" and not t["blockedBy"]
        ]

    def unclaimed_tasks(self) -> list[dict[str, Any]]:
        """Return pending tasks with no owner and no unresolved blockers (s11)."""
        return [
            t for t in self.list_all()
            if t["status"] == "pending"
            and not t.get("owner")
            and not t["blockedBy"]
        ]

    def bind_worktree(self, task_id: int, worktree: str) -> dict[str, Any] | None:
        """Bind a worktree to a task and move to in_progress (s12)."""
        task = self._load(task_id)
        if task is None:
            return None
        task["worktree"] = worktree
        if task["status"] == "pending":
            task["status"] = "in_progress"
        self._save(task)
        logger.info("[TaskManager] Task %d bound to worktree '%s'", task_id, worktree)
        return task

    def unbind_worktree(self, task_id: int) -> dict[str, Any] | None:
        """Remove worktree binding from a task (s12)."""
        task = self._load(task_id)
        if task is None:
            return None
        task["worktree"] = ""
        self._save(task)
        return task

    def claim(self, task_id: int, owner: str) -> dict[str, Any] | None:
        """Claim a task: set owner and move to in_progress (s11)."""
        task = self._load(task_id)
        if task is None:
            return None
        if task.get("owner") and task["owner"] != owner:
            return None  # already claimed by someone else
        task["owner"] = owner
        task["status"] = "in_progress"
        self._save(task)
        logger.info("[TaskManager] Task %d claimed by '%s'", task_id, owner)
        return task

    def render(self) -> str:
        """Render the task graph as compact text."""
        tasks = self.list_all()
        if not tasks:
            return "(no tasks)"

        status_icon = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
        }
        lines: list[str] = []
        for t in tasks:
            icon = status_icon.get(t["status"], "[ ]")
            blocked = ""
            if t["blockedBy"]:
                blocked = f" (blocked by: {t['blockedBy']})"
            lines.append(f"{icon} #{t['id']}: {t['subject']}{blocked}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _path(self, task_id: int) -> Path:
        return self.dir / f"task_{task_id}.json"

    def _load(self, task_id: int) -> dict[str, Any] | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _save(self, task: dict[str, Any]) -> None:
        path = self._path(task["id"])
        path.write_text(
            json.dumps(task, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _max_id(self) -> int:
        max_id = 0
        for f in self.dir.glob("task_*.json"):
            try:
                task = json.loads(f.read_text(encoding="utf-8"))
                max_id = max(max_id, task.get("id", 0))
            except (json.JSONDecodeError, OSError):
                continue
        return max_id

    def _clear_dependency(self, completed_id: int) -> None:
        """Remove completed_id from all downstream blockedBy lists."""
        for f in self.dir.glob("task_*.json"):
            try:
                task = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if completed_id in task.get("blockedBy", []):
                task["blockedBy"].remove(completed_id)
                self._save(task)
                logger.info(
                    "[TaskManager] Unblocked task %d (was waiting on %d)",
                    task["id"],
                    completed_id,
                )
