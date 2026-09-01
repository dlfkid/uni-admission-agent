"""Tests for install-root mutual exclusion during an upgrade."""

import os
from pathlib import Path

import pytest

from src.services.upgrade.locking import (
    LOCK_NAME,
    UpgradeInProgressError,
    install_lock,
)


# ── acquire / release ─────────────────────────────────────────────────


def test_lock_is_created_and_records_the_owner(tmp_path: Path) -> None:
    lock = tmp_path / LOCK_NAME
    with install_lock(tmp_path):
        assert lock.is_file()
        assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_the_lock_file_outlives_the_lock(tmp_path: Path) -> None:
    """It must NOT be deleted on release.

    Unlinking is what makes a lock file unsafe: a process that removes a file
    another process currently holds a lock on lets both end up inside the
    critical section. The file staying behind is harmless — the OS lock, not
    the file's existence, is the mutex.
    """
    with install_lock(tmp_path):
        pass
    assert (tmp_path / LOCK_NAME).exists()


def test_lock_is_reacquirable_after_release(tmp_path: Path) -> None:
    with install_lock(tmp_path):
        pass
    with install_lock(tmp_path):
        pass


def test_lock_is_released_when_the_body_raises(tmp_path: Path) -> None:
    """A failed upgrade must not leave the install permanently locked."""
    with pytest.raises(RuntimeError):
        with install_lock(tmp_path):
            raise RuntimeError("boom")
    with install_lock(tmp_path):
        pass


def test_lock_creates_the_root_if_absent(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    with install_lock(root):
        assert (root / LOCK_NAME).is_file()


# ── contention ────────────────────────────────────────────────────────


def test_a_second_holder_is_refused(tmp_path: Path) -> None:
    """The scenario this exists for: a second upgrade must not proceed to
    sweep the first one's in-flight staging tree.

    ``flock`` and ``msvcrt.locking`` are per-open-file-description, so a
    second acquisition genuinely contends even from the same process — this
    is a real mutual-exclusion check, not a simulated one.
    """
    with install_lock(tmp_path):
        with pytest.raises(UpgradeInProgressError) as exc:
            with install_lock(tmp_path):
                pass
    assert exc.value.pid == os.getpid()
    assert "Another upgrade is already running" in str(exc.value)


def test_a_refused_acquirer_leaves_the_holders_lock_alone(tmp_path: Path) -> None:
    """The loser must not unlink or truncate the winner's lock.

    Deterministic version of the two-reclaimers interleaving: if the refused
    process removed the file, the next acquirer would create a fresh one and
    both would be inside the critical section.
    """
    with install_lock(tmp_path):
        for _ in range(3):
            with pytest.raises(UpgradeInProgressError):
                with install_lock(tmp_path):
                    pass
        # Still present, still naming the real holder.
        assert (tmp_path / LOCK_NAME).read_text(encoding="utf-8").strip() == str(
            os.getpid()
        )
        # And still actually held: a fourth attempt is refused too.
        with pytest.raises(UpgradeInProgressError):
            with install_lock(tmp_path):
                pass


def test_a_refused_acquirer_does_not_leak_a_descriptor(tmp_path: Path) -> None:
    """Refusal happens after os.open, so the fd must be closed on that path."""
    with install_lock(tmp_path):
        for _ in range(200):
            with pytest.raises(UpgradeInProgressError):
                with install_lock(tmp_path):
                    pass


# ── a lock left by a dead process ─────────────────────────────────────


def test_a_lock_file_from_a_dead_process_does_not_block(tmp_path: Path) -> None:
    """No staleness heuristic is involved: the kernel released the lock when
    that process died, so the file alone cannot pin anyone."""
    (tmp_path / LOCK_NAME).write_text("999999", encoding="utf-8")
    with install_lock(tmp_path):
        assert (tmp_path / LOCK_NAME).read_text(encoding="utf-8").strip() == str(
            os.getpid()
        )


def test_an_unreadable_lock_file_does_not_block(tmp_path: Path) -> None:
    (tmp_path / LOCK_NAME).write_text("not-a-pid", encoding="utf-8")
    with install_lock(tmp_path):
        pass
