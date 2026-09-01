"""Install-root mutual exclusion for the upgrade transaction.

Concurrency here is not hypothetical. :func:`~src.services.upgrade.transaction.sweep_stale_staging`
deletes every scratch tree it finds under ``staging/``, so two upgrades
running at once would have the second delete the first's in-flight download;
a later interleaving lets one process prune the very version the other just
activated, so one reports success while the pointer has already moved back.

The lock is an **OS-level exclusive file lock** (``fcntl.flock`` on POSIX,
``msvcrt.locking`` on Windows), not a PID file the code reclaims by hand.
That distinction is the whole design:

* The kernel releases the lock when the holding process dies, so a crashed
  upgrade cannot pin the user — without any staleness heuristic to get wrong.
* An advisory-file scheme has to unlink and re-create to reclaim a stale
  lock, and those two steps are not atomic: two reclaimers can each unlink
  the other's freshly created lock and both end up inside the critical
  section, which is precisely the race the lock exists to prevent.

**The lock file is never deleted.** Deleting it would reintroduce that same
generation mix-up — one process removing a file whose lock another process
currently holds. It is a few bytes and it stays.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Iterator

from src.services.upgrade.types import UpgradeError

LOCK_NAME = ".upgrade.lock"


class UpgradeInProgressError(UpgradeError):
    """Raised when another process holds the install-root lock."""

    def __init__(self, pid: int | None) -> None:
        owner = f"pid {pid}" if pid is not None else "an unidentified process"
        super().__init__(
            f"Another upgrade is already running ({owner}). "
            "Wait for it to finish, then try again."
        )
        self.pid = pid


def _read_owner(path: Path) -> int | None:
    """The holder's PID, for the error message only. Never used for control flow."""
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _try_lock(fd: int) -> bool:
    """Take an exclusive, non-blocking OS lock on *fd*."""
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
        import msvcrt  # pylint: disable=import-outside-toplevel

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl  # pylint: disable=import-outside-toplevel

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    """Release the OS lock. Closing the fd would do it too; this is explicit."""
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
        import msvcrt  # pylint: disable=import-outside-toplevel

        with contextlib.suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return

    import fcntl  # pylint: disable=import-outside-toplevel

    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def install_lock(root: Path) -> Iterator[Path]:
    """Hold an exclusive lock on *root* for the duration of the block.

    Raises :class:`UpgradeInProgressError` when another process holds it.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / LOCK_NAME

    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if not _try_lock(fd):
            raise UpgradeInProgressError(_read_owner(path))
    except BaseException:
        os.close(fd)
        raise

    try:
        # Diagnostic only — the OS lock, not this content, is the mutex.
        with contextlib.suppress(OSError):
            os.truncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.fsync(fd)
        yield path
    finally:
        _unlock(fd)
        os.close(fd)
