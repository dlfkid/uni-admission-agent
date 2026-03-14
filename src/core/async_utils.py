"""Async helper utilities shared across sync wrappers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


def run_sync(awaitable_factory: Callable[[], Awaitable[T]], *, label: str) -> T:
    """Run an awaitable in sync context and guard against active loops."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable_factory())
    raise RuntimeError(
        f"{label} cannot run inside an active event loop; "
        "use async API instead."
    )
