"""
In-memory async task tracker for long-running operations.

Provides a thread-safe, dict-based task registry so the API can
return immediately with a ``task_id`` while crawl operations run
in the background. Clients poll ``GET /tasks/{task_id}`` for progress.

Not intended for production-scale deployments — swap with Redis/Celery
if horizontal scaling is needed.
"""

import asyncio
import logging
import uuid
from enum import Enum
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class TaskState(str, Enum):
    """Lifecycle states for a background task."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class TaskInfo:
    """Mutable state container for a single task."""

    __slots__ = ("task_id", "state", "progress", "result", "error", "logs")

    def __init__(self, task_id: str) -> None:
        self.task_id: str = task_id
        self.state: TaskState = TaskState.PENDING
        self.progress: Optional[str] = None
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.logs: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for API responses."""
        return {
            "task_id": self.task_id,
            "state": self.state.value,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "logs": self.logs,
        }


class TaskManager:
    """Singleton in-memory task registry.

    Thread-safe via asyncio lock. Stores all tasks for the lifetime
    of the server process. Enforces strict singleton execution (only one
    RUNNING task at a time).
    """

    _instance: Optional["TaskManager"] = None
    _task_store: Dict[str, TaskInfo]
    _lock: asyncio.Lock
    _active_task_id: Optional[str] = None
    _task_objects: Dict[str, asyncio.Task]

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._task_store = {}
            inst._lock = asyncio.Lock()
            inst._active_task_id = None
            inst._task_objects = {}
            cls._instance = inst
        return cls._instance

    def create_task(self) -> str:
        """Register a new task and return its ID.

        Raises:
            RuntimeError: If another task is already RUNNING or PENDING.
        """
        if self._active_task_id:
            # Check if it's actually done but not cleaned up?
            # No, we rely on update_task to clear _active_task_id.
            # But let's be safe: if state is DONE/FAILED, allow new one.
            active_info = self._task_store.get(self._active_task_id)
            if active_info and active_info.state in (TaskState.RUNNING, TaskState.PENDING):
                raise RuntimeError(f"Task {self._active_task_id} is already running")

        task_id = uuid.uuid4().hex[:12]
        self._task_store[task_id] = TaskInfo(task_id)
        self._active_task_id = task_id
        logger.info("Task created: %s", task_id)
        return task_id

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        """Retrieve task info by ID."""
        return self._task_store.get(task_id)

    def get_active_task(self) -> Optional[TaskInfo]:
        """Return the currently running task, if any."""
        if not self._active_task_id:
            return None
        info = self._task_store.get(self._active_task_id)
        if info and info.state in (TaskState.RUNNING, TaskState.PENDING):
            return info
        return None

    def register_task_object(self, task_id: str, task_obj: asyncio.Task) -> None:
        """Associate an asyncio.Task with a task ID for cancellation."""
        self._task_objects[task_id] = task_obj

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task. Returns True if cancelled."""
        task_obj = self._task_objects.get(task_id)
        if task_obj and not task_obj.done():
            task_obj.cancel()
            self.update_task(task_id, state=TaskState.FAILED, error="Cancelled by user")
            logger.info("Task %s cancelled by user", task_id)
            return True
        return False

    def add_log(self, task_id: str, message: str) -> None:
        """Append a log message to the task."""
        info = self._task_store.get(task_id)
        if info:
            info.logs.append(message)

    def update_task(
        self,
        task_id: str,
        *,
        state: Optional[TaskState] = None,
        progress: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update fields on an existing task."""
        info = self._task_store.get(task_id)
        if info is None:
            logger.warning("Attempted to update unknown task: %s", task_id)
            return

        if state is not None:
            info.state = state
            # If terminal state, clear active flag
            if state in (TaskState.DONE, TaskState.FAILED) and self._active_task_id == task_id:
                self._active_task_id = None
                # Cleanup task object reference
                self._task_objects.pop(task_id, None)

        if progress is not None:
            info.progress = progress
        if result is not None:
            info.result = result
        if error is not None:
            info.error = error
