"""Install-root mutual exclusion for the upgrade transaction.

Concurrency here is not hypothetical. :func:`~src.services.upgrade.transaction.sweep_stale_staging`
deletes every scratch tree it finds under ``staging/``, so two upgrades
running at once would have the second delete the first's in-flight download;
a later interleaving lets one process prune the very version the other just
activated, so one reports success while the pointer has already moved back.

The lock is a file in the install root holding the owner's PID. A holder that
died without releasing leaves a stale lock, which is detected by probing that
PID rather than by any timeout — the same cross-platform liveness check the
server-running gate uses.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Iterator

from src.services.upgrade.preflight import is_process_alive
from src.services.upgrade.types import UpgradeError

LOCK_NAME = ".upgrade.lock"


class UpgradeInProgressError(UpgradeError):
    """Raised when another live process holds the install-root lock."""

    def __init__(self, pid: int | None) -> None:
        owner = f"pid {pid}" if pid is not None else "an unidentified process"
        super().__init__(
            f"Another upgrade is already running ({owner}). "
            "Wait for it to finish, then try again."
        )
        self.pid = pid


def _read_owner(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _try_acquire(path: Path) -> int | None:
    """Create the lock file exclusively. Returns the fd, or ``None`` if held."""
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return None


@contextlib.contextmanager
def install_lock(root: Path) -> Iterator[Path]:
    """Hold an exclusive lock on *root* for the duration of the block.

    Raises :class:`UpgradeInProgressError` when a live process holds it. A
    lock left behind by a dead process is reclaimed once, not waited on.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / LOCK_NAME

    fd = _try_acquire(path)
    if fd is None:
        owner = _read_owner(path)
        if owner is not None and is_process_alive(owner):
            raise UpgradeInProgressError(owner)
        # The holder is gone (or the file is unreadable): reclaim it once.
        with contextlib.suppress(OSError):
            path.unlink()
        fd = _try_acquire(path)
        if fd is None:
            # Someone won the race between our unlink and our re-create.
            raise UpgradeInProgressError(_read_owner(path))

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield path
    finally:
        with contextlib.suppress(OSError):
            path.unlink()
