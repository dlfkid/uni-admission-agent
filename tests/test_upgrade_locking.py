"""Tests for install-root mutual exclusion during an upgrade."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.upgrade.locking import (
    LOCK_NAME,
    UpgradeInProgressError,
    install_lock,
)


# ── acquire / release ─────────────────────────────────────────────────


def test_lock_is_created_and_released(tmp_path: Path) -> None:
    lock = tmp_path / LOCK_NAME
    with install_lock(tmp_path):
        assert lock.is_file()
        assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert not lock.exists()


def test_lock_is_released_when_the_body_raises(tmp_path: Path) -> None:
    """A failed upgrade must not leave the install permanently locked."""
    with pytest.raises(RuntimeError):
        with install_lock(tmp_path):
            raise RuntimeError("boom")
    assert not (tmp_path / LOCK_NAME).exists()


def test_lock_creates_the_root_if_absent(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    with install_lock(root):
        assert (root / LOCK_NAME).is_file()


# ── contention ────────────────────────────────────────────────────────


def test_a_live_holder_blocks(tmp_path: Path) -> None:
    """The scenario this exists for: a second upgrade must not proceed to
    sweep the first one's in-flight staging tree."""
    with install_lock(tmp_path):
        with pytest.raises(UpgradeInProgressError) as exc:
            with install_lock(tmp_path):
                pass
    assert exc.value.pid == os.getpid()
    assert "Another upgrade is already running" in str(exc.value)


def test_nesting_is_still_blocked_after_the_outer_lock_releases(tmp_path: Path) -> None:
    with install_lock(tmp_path):
        pass
    with install_lock(tmp_path):  # reacquirable once released
        pass


# ── stale locks ───────────────────────────────────────────────────────


def test_a_dead_holder_is_reclaimed(tmp_path: Path) -> None:
    """A process killed mid-upgrade leaves its lock behind; treating that as
    contention would pin the user permanently — the same failure class this
    whole feature exists to remove."""
    (tmp_path / LOCK_NAME).write_text("999999", encoding="utf-8")
    with patch("src.services.upgrade.locking.is_process_alive", return_value=False):
        with install_lock(tmp_path):
            assert (tmp_path / LOCK_NAME).read_text(encoding="utf-8") == str(os.getpid())
    assert not (tmp_path / LOCK_NAME).exists()


def test_an_unreadable_lock_is_reclaimed(tmp_path: Path) -> None:
    (tmp_path / LOCK_NAME).write_text("not-a-pid", encoding="utf-8")
    with install_lock(tmp_path):
        assert (tmp_path / LOCK_NAME).read_text(encoding="utf-8") == str(os.getpid())


def test_a_live_holder_is_not_reclaimed_even_if_unparseable_later(tmp_path: Path) -> None:
    (tmp_path / LOCK_NAME).write_text("4321", encoding="utf-8")
    with patch("src.services.upgrade.locking.is_process_alive", return_value=True):
        with pytest.raises(UpgradeInProgressError):
            with install_lock(tmp_path):
                pass
    # The live holder's lock file survives our refusal.
    assert (tmp_path / LOCK_NAME).read_text(encoding="utf-8") == "4321"
