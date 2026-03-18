"""WorktreeManager — git worktree isolation per task (s12 pattern).

Each task gets its own git worktree directory so teammates can work
in parallel without file conflicts. Worktrees are tracked in an
``index.json`` registry and lifecycle events are logged to ``events.jsonl``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from src.agent_runtime.task_manager import TaskManager

logger = logging.getLogger(__name__)

WORKTREES_DIR = Path(".worktrees")


# ---------------------------------------------------------------------------
# EventLog — append-only JSONL lifecycle log
# ---------------------------------------------------------------------------


class EventLog:
    """Append-only event log for worktree lifecycle tracking."""

    def __init__(self, log_path: Path | None = None) -> None:
        self.path = log_path or (WORKTREES_DIR / "events.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **data: Any) -> None:
        record = {"event": event, "ts": time.time(), **data}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        logger.info("[EventLog] %s", event)

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events

    def render(self, limit: int = 20) -> str:
        events = self.read_all()
        if not events:
            return "(no events)"
        recent = events[-limit:]
        lines = []
        for e in recent:
            lines.append(f"  [{e.get('event', '?')}] {json.dumps(e, ensure_ascii=False)[:120]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# WorktreeManager
# ---------------------------------------------------------------------------


class WorktreeManager:
    """Manage git worktrees bound to tasks."""

    def __init__(
        self,
        worktrees_dir: Path | None = None,
        tasks: TaskManager | None = None,
    ) -> None:
        self.dir = worktrees_dir or WORKTREES_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self.tasks = tasks or TaskManager()
        self.events = EventLog()
        self.index: dict[str, dict[str, Any]] = self._load_index()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self, name: str, task_id: int | None = None
    ) -> dict[str, Any]:
        """Create a new git worktree and optionally bind it to a task."""
        if name in self.index:
            return {"error": f"Worktree '{name}' already exists"}

        wt_path = (self.dir / name).resolve()
        branch = f"wt/{name}"

        self.events.emit("worktree.create.before", name=name, task_id=task_id)

        try:
            self._run_git(["worktree", "add", "-b", branch, str(wt_path), "HEAD"])
        except Exception as exc:
            self.events.emit("worktree.create.failed", name=name, error=str(exc))
            return {"error": f"git worktree add failed: {exc}"}

        entry: dict[str, Any] = {
            "name": name,
            "path": str(wt_path),
            "branch": branch,
            "task_id": task_id,
            "status": "active",
            "created_at": time.time(),
        }
        self.index[name] = entry
        self._save_index()

        # Bind to task if specified
        if task_id is not None:
            self.tasks.bind_worktree(task_id, name)

        self.events.emit("worktree.create.after", name=name, task_id=task_id)
        logger.info("[Worktree] Created '%s' at %s", name, wt_path)
        return entry

    def run_in(self, name: str, command: str, timeout: int = 300) -> dict[str, Any]:
        """Execute a shell command inside a worktree directory."""
        entry = self.index.get(name)
        if not entry:
            return {"error": f"Worktree '{name}' not found"}

        wt_path = entry["path"]
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=wt_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout + result.stderr).strip()[:50000]
            return {
                "exit_code": result.returncode,
                "output": output,
                "worktree": name,
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Command timed out ({timeout}s)", "worktree": name}
        except Exception as exc:
            return {"error": str(exc), "worktree": name}

    def keep(self, name: str) -> dict[str, Any]:
        """Mark a worktree as kept (preserved for future use)."""
        entry = self.index.get(name)
        if not entry:
            return {"error": f"Worktree '{name}' not found"}
        entry["status"] = "kept"
        self._save_index()
        self.events.emit("worktree.keep", name=name)
        return entry

    def remove(
        self, name: str, complete_task: bool = False, force: bool = False
    ) -> dict[str, Any]:
        """Remove a worktree. Optionally complete the bound task."""
        entry = self.index.get(name)
        if not entry:
            return {"error": f"Worktree '{name}' not found"}

        self.events.emit("worktree.remove.before", name=name)

        try:
            cmd = ["worktree", "remove", entry["path"]]
            if force:
                cmd.append("--force")
            self._run_git(cmd)
        except Exception as exc:
            self.events.emit("worktree.remove.failed", name=name, error=str(exc))
            return {"error": f"git worktree remove failed: {exc}"}

        # Complete bound task if requested
        task_id = entry.get("task_id")
        if complete_task and task_id is not None:
            self.tasks.update(task_id, status="completed")
            self.tasks.unbind_worktree(task_id)
            self.events.emit("task.completed", task_id=task_id, via_worktree=name)

        entry["status"] = "removed"
        self._save_index()
        self.events.emit("worktree.remove.after", name=name, task_id=task_id)
        logger.info("[Worktree] Removed '%s'", name)
        return entry

    def list_all(self) -> list[dict[str, Any]]:
        return list(self.index.values())

    def render(self) -> str:
        if not self.index:
            return "(no worktrees)"
        status_icon = {"active": "[>]", "kept": "[K]", "removed": "[x]"}
        lines: list[str] = []
        for e in self.index.values():
            icon = status_icon.get(e["status"], "[?]")
            task_info = f" (task #{e['task_id']})" if e.get("task_id") else ""
            lines.append(f"{icon} {e['name']}: {e['branch']}{task_info}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_git(args: list[str]) -> str:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"git {args[0]} failed")
        return result.stdout.strip()

    # ------------------------------------------------------------------
    # Index persistence
    # ------------------------------------------------------------------

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                return {e["name"]: e for e in data}
            except (json.JSONDecodeError, OSError, KeyError):
                pass
        return {}

    def _save_index(self) -> None:
        records = list(self.index.values())
        self.index_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
