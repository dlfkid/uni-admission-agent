"""Tests for the versioned install layout and atomic pointer switch — spec §3."""

from pathlib import Path

import pytest

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.types import UpgradeError


def _make_version(layout: InstallLayout, version: str) -> Path:
    """Create a plausible version payload (exe + _internal)."""
    vdir = layout.version_dir(version)
    (vdir / "_internal").mkdir(parents=True)
    exe = vdir / layout.executable_name
    exe.write_text("#!/bin/sh\necho fake\n")
    exe.chmod(0o755)
    return vdir


# ── pointer mechanics ─────────────────────────────────────────────────


@pytest.mark.parametrize("windows", [False, True])
def test_activate_then_read_back(tmp_path: Path, windows: bool) -> None:
    """Both pointer styles round-trip the active version name."""
    layout = InstallLayout(root=tmp_path, windows=windows)
    _make_version(layout, "v0.11.0")
    layout.activate("v0.11.0")
    assert layout.active_version() == "v0.11.0"


def test_active_version_is_none_when_never_activated(tmp_path: Path) -> None:
    assert InstallLayout(root=tmp_path, windows=False).active_version() is None


@pytest.mark.parametrize("windows", [False, True])
def test_activate_replaces_an_existing_pointer(tmp_path: Path, windows: bool) -> None:
    """Switching is a replace, not an error — this is the rollback primitive."""
    layout = InstallLayout(root=tmp_path, windows=windows)
    _make_version(layout, "v0.10.0")
    _make_version(layout, "v0.11.0")
    layout.activate("v0.10.0")
    layout.activate("v0.11.0")
    assert layout.active_version() == "v0.11.0"
    layout.activate("v0.10.0")
    assert layout.active_version() == "v0.10.0"


def test_activate_rejects_a_missing_version(tmp_path: Path) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    with pytest.raises(UpgradeError, match="not installed"):
        layout.activate("v9.9.9")


def test_activate_leaves_no_temp_files(tmp_path: Path) -> None:
    """A stray .current.*.tmp would accumulate on every upgrade."""
    layout = InstallLayout(root=tmp_path, windows=False)
    _make_version(layout, "v0.11.0")
    layout.activate("v0.11.0")
    assert not list(tmp_path.glob(".current.*"))


# ── entrypoint ────────────────────────────────────────────────────────


def test_ensure_entrypoint_posix_symlinks_through_pointer(tmp_path: Path) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    _make_version(layout, "v0.11.0")
    layout.activate("v0.11.0")
    layout.ensure_entrypoint()
    entry = layout.bin_dir / "adm-agent"
    assert entry.is_symlink()
    # Resolve both sides: tmp_path may itself sit behind a symlink (/var on macOS).
    assert entry.resolve() == (layout.version_dir("v0.11.0") / "adm-agent").resolve()


def test_ensure_entrypoint_windows_writes_cmd_shim(tmp_path: Path) -> None:
    layout = InstallLayout(root=tmp_path, windows=True)
    _make_version(layout, "v0.11.0")
    layout.activate("v0.11.0")
    layout.ensure_entrypoint()
    shim = layout.bin_dir / "adm-agent.cmd"
    body = shim.read_text()
    assert "current.txt" in body
    assert "%~dp0" in body


# ── inventory and retention ───────────────────────────────────────────


def test_installed_versions_sorted_newest_first(tmp_path: Path) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    for v in ("v0.9.0", "v0.11.0", "v0.10.0"):
        _make_version(layout, v)
    assert layout.installed_versions() == ["v0.11.0", "v0.10.0", "v0.9.0"]


def test_prune_removes_everything_not_kept(tmp_path: Path) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    for v in ("v0.9.0", "v0.10.0", "v0.11.0"):
        _make_version(layout, v)
    layout.activate("v0.11.0")
    removed = layout.prune(keep=["v0.11.0", "v0.10.0"])
    assert removed == ["v0.9.0"]
    assert not layout.version_dir("v0.9.0").exists()
    assert layout.version_dir("v0.10.0").exists()


def test_prune_refuses_to_remove_the_active_version(tmp_path: Path) -> None:
    """Pruning the live version would delete the running install."""
    layout = InstallLayout(root=tmp_path, windows=False)
    _make_version(layout, "v0.11.0")
    layout.activate("v0.11.0")
    with pytest.raises(UpgradeError, match="active"):
        layout.prune(keep=[])


# ── legacy detection (spec §3.5) ──────────────────────────────────────


def test_is_legacy_true_for_flat_bin_layout(tmp_path: Path) -> None:
    (tmp_path / "bin" / "_internal").mkdir(parents=True)
    (tmp_path / "bin" / "adm-agent").write_text("old")
    assert InstallLayout(root=tmp_path, windows=False).is_legacy() is True


def test_is_legacy_false_for_versioned_layout(tmp_path: Path) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    _make_version(layout, "v0.11.0")
    layout.activate("v0.11.0")
    layout.ensure_entrypoint()
    assert layout.is_legacy() is False


def test_is_legacy_false_for_empty_root(tmp_path: Path) -> None:
    assert InstallLayout(root=tmp_path, windows=False).is_legacy() is False


def test_is_legacy_true_for_flat_client_install(tmp_path: Path) -> None:
    """The client's legacy shape has _internal at the root, not under bin/."""
    (tmp_path / "_internal").mkdir()
    (tmp_path / "adm-agent-client").write_text("old")
    layout = InstallLayout(root=tmp_path, artifact_name="adm-agent-client", windows=False)
    assert layout.is_legacy() is True


# ── artifact parameterisation (spec §3.6) ─────────────────────────────


@pytest.mark.parametrize("windows", [False, True])
def test_client_layout_uses_the_client_executable(tmp_path: Path, windows: bool) -> None:
    layout = InstallLayout(
        root=tmp_path, artifact_name="adm-agent-client", windows=windows
    )
    expected = "adm-agent-client.exe" if windows else "adm-agent-client"
    assert layout.executable_name == expected

    _make_version(layout, "v0.11.0")
    layout.activate("v0.11.0")
    layout.ensure_entrypoint()

    entry = layout.entrypoint_path
    assert entry.name == ("adm-agent-client.cmd" if windows else "adm-agent-client")
    assert entry.exists()
    if windows:
        assert "adm-agent-client.exe" in entry.read_text()
    else:
        assert entry.resolve() == (layout.version_dir("v0.11.0") / expected).resolve()


# ── data-safety invariant (Global Constraints) ────────────────────────


def test_layout_operations_never_touch_user_data(tmp_path: Path) -> None:
    """.env and admission.db must survive activation, pruning, entrypoint work."""
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=secret\n")
    db = tmp_path / "admission.db"
    db.write_bytes(b"SQLite format 3\x00")
    layout = InstallLayout(root=tmp_path, windows=False)
    for v in ("v0.10.0", "v0.11.0"):
        _make_version(layout, v)
    layout.activate("v0.10.0")
    layout.activate("v0.11.0")
    layout.ensure_entrypoint()
    layout.prune(keep=["v0.11.0"])
    assert env.read_text() == "DEEPSEEK_API_KEY=secret\n"
    assert db.read_bytes() == b"SQLite format 3\x00"
