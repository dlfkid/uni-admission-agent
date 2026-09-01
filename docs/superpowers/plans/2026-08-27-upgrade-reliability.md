# Upgrade Reliability (Atomic Self-Update) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `adm-agent upgrade` atomic, verified, reversible and
agent-drivable, so a non-developer user can take a maintainer's fix with one
command and keep working.

**Architecture:** Replace in-place file overwriting with versioned install
directories plus an atomic pointer switch. The upgrading process runs from
`versions/<old>/` and never touches its own files; a new version is
downloaded to `staging/`, checksum-verified, and smoke-tested *before* the
pointer moves, so any pre-activation failure leaves the install byte-identical.
Rollback is repointing, not restoring. The CLI grows a stable JSON + exit-code
contract because the primary caller is an agent, not a human.

**Tech Stack:** Python 3.12, `packaging.version` (semantic comparison),
`typer` (CLI), `pytest` + `tmp_path` + `unittest.mock` (hermetic tests),
PyInstaller onedir (packaging), GitHub Actions (release gate).

**Spec:** [`docs/superpowers/specs/2026-08-27-upgrade-reliability-design.md`](../specs/2026-08-27-upgrade-reliability-design.md)

## Global Constraints

- **Data-safety invariant (spec §3.2):** upgrade code may write only to
  `versions/`, `staging/`, `current` / `current.txt`, and `bin/` under the
  install root. Never `.env`, `admission.db` (or its `-wal` / `-shm`
  siblings), `schemas/`, the strategy cache, or logs. Every failure-path test
  asserts this.
- **Install root:** `~/.uni-agent/` in frozen mode
  ([`src/core/paths.py:70`](../../../src/core/paths.py)). It is *also* the
  data root — that is why the invariant above is not optional.
- **Every layout/transaction function takes its root as a parameter.** No
  module-level `Path.home()` constants; tests drive everything through
  `tmp_path`.
- **Exit codes are API (spec §7):** `0` ok · `10` server_running ·
  `11` no_asset_for_platform · `12` checksum_mismatch /
  staged_binary_failed / unparseable_version · `13` post_check_failed
  (rolled back) · `14` not_frozen · `15` legacy_layout · `1` unexpected.
- **Release endpoint override (spec §7.1):** `ADM_AGENT_RELEASE_API_BASE`,
  defaulting to `https://api.github.com/repos`. Never hardcode the base URL
  at call sites.
- **Retention (spec §3.2):** keep active + one previous. Automatic rollback
  deletes the bad version; manual `--rollback` keeps the version rolled back
  from.
- **Post-check asymmetry (spec §6.3):** `check` failure warns only;
  `db-migrate` failure that `repair --auto` cannot resolve rolls back.
- **Test style:** plain pytest functions with type-hinted signatures,
  docstring first line, `# ── section ──` dividers, matching
  `tests/test_server_config.py`.
- **Lint gate:** `uv run pylint src/ scripts/` must stay at 10.00/10 (enforced
  by `.githooks/pre-push`).
- **Expect a red full suite between Task 1 and Task 9.** Task 1 deletes
  `src/services/upgrade.py` so the package can occupy that import path; a
  module and a package cannot coexist there. Until the CLIs are rewired
  (Tasks 9–10), `uv run pytest -q` fails at import on anything that loads
  `src/cmd/cli.py` or `src/cmd/client_cli.py`. This is expected. Run only the
  task's own test file until Task 10 says otherwise. Do **not** "fix" it by
  restoring the old module.

## File Structure

`src/services/upgrade.py` (448 lines) becomes a package. Justification: the
spec deletes its three central functions (`sync_installation_payload`,
`backup_current_executable`, `replace_executable`) and adds layout
management, platform-specific pointer mechanics, staging verification and
transaction orchestration — one file would exceed 700 lines. The repo
already uses this shape for `src/services/crawl_strategy/`.

| File | Responsibility |
|---|---|
| `src/services/upgrade/__init__.py` | Public API; re-exports so existing `from src.services.upgrade import ...` keeps working |
| `src/services/upgrade/types.py` | `UpgradeResult`, `BlockedReason`, `ExitCode`, `UpgradeError` |
| `src/services/upgrade/versions.py` | Tag parsing and semantic comparison (spec §4) |
| `src/services/upgrade/layout.py` | `InstallLayout`: paths, pointer read/switch, retention, legacy detection (spec §3.2–3.5) |
| `src/services/upgrade/release.py` | Release API client, asset matching, `SHA256SUMS` (spec §6.1, §7.1) |
| `src/services/upgrade/staging.py` | Download, safe extract, checksum verify, staged self-check (spec §5.3–5.5, §6.1–6.2) |
| `src/services/upgrade/preflight.py` | Frozen / legacy-layout / server-running gates (spec §9) |
| `src/services/upgrade/transaction.py` | The 8-step transaction, rollback, post-check (spec §5, §6.3) |
| `src/cmd/cli.py` | `upgrade --json/--rollback`, `version --json`, exit codes (spec §7) |
| `src/cmd/client_cli.py` | Same contract for `adm-agent-client`, rooted at its own install dir (spec §3.6) |
| `adm-agent.spec` | Add `packaging` to `_COLLECT_PKGS` (spec §12) |
| `skills/uni-admission-install/SKILL.md` | New layout in §1, §3 calls `upgrade`, legacy branch (spec §3.5, §8) |
| `.github/workflows/release.yml` | `SHA256SUMS` upload + `upgrade-verify` gate (spec §6.1, §11) |
| `README.md`, `RELEASING.md` | Windows invocation name, layout note (spec §3.4) |

Tests mirror the package: `tests/test_upgrade_versions.py`,
`_layout.py`, `_release.py`, `_staging.py`, `_preflight.py`,
`_transaction.py`, `_cli.py`.

---

### Task 1: Version comparison

Closes the shipped defect in spec §1.1. Pure functions, no I/O.

**Files:**
- Create: `src/services/upgrade/__init__.py` (empty for now)
- Create: `src/services/upgrade/types.py`
- Create: `src/services/upgrade/versions.py`
- Test: `tests/test_upgrade_versions.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `UnparseableVersionError(UpgradeError)` with `.current: str`, `.latest: str`
  - `parse_tag(tag: str) -> Version | None`
  - `is_newer(current: str, latest: str) -> bool` — raises `UnparseableVersionError`

- [ ] **Step 1: Delete the old module and create the package skeleton**

The old module must go before the package can exist at the same import path.

```bash
git rm src/services/upgrade.py
mkdir -p src/services/upgrade
touch src/services/upgrade/__init__.py
```

This breaks `src/cmd/cli.py` imports until Task 8. That is expected and
acceptable: Tasks 1–7 build the package bottom-up and are verified by their
own unit tests, which import submodules directly. Do not attempt to keep
`cli.py` importable in the interim.

- [ ] **Step 2: Write the failing test**

`tests/test_upgrade_versions.py`:

```python
"""Tests for semantic version comparison — spec §4."""

import pytest

from src.services.upgrade.types import UnparseableVersionError
from src.services.upgrade.versions import is_newer, parse_tag


# ── parse_tag ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v0.10.0", "0.10.0"),
        ("0.10.0", "0.10.0"),
        ("v0.7.5-alpha", "0.7.5a0"),
        ("v0.0.0-dev", "0.0.0.dev0"),
    ],
)
def test_parse_tag_accepts_every_historical_shape(tag: str, expected: str) -> None:
    """Every tag shape this repo has ever published must parse."""
    parsed = parse_tag(tag)
    assert parsed is not None
    assert str(parsed) == expected


@pytest.mark.parametrize("tag", ["", "latest", "v", "not-a-version"])
def test_parse_tag_returns_none_for_garbage(tag: str) -> None:
    assert parse_tag(tag) is None


# ── is_newer ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current,latest,expected",
    [
        # THE regression guard: string comparison said False here (spec §1.1).
        ("v0.9.0", "v0.10.0", True),
        ("v0.8.0", "v0.10.0", True),
        ("v0.10.0", "v0.9.1", False),
        ("v0.10.0", "v0.10.0", False),
        ("v0.10.0", "v0.11.0", True),
        ("v0.7.5-alpha", "v0.8.0", True),
        ("v1.0.0-alpha", "v1.0.0", True),
        ("v1.0.0", "v1.0.0-rc1", False),
        ("v0.0.0-dev", "v0.10.0", True),
    ],
)
def test_is_newer_orders_semantically(current: str, latest: str, expected: bool) -> None:
    assert is_newer(current, latest) is expected


@pytest.mark.parametrize(
    "current,latest",
    [("v0.10.0", "garbage"), ("garbage", "v0.10.0"), ("", "")],
)
def test_is_newer_raises_on_unparseable(current: str, latest: str) -> None:
    """Never crash, never guess — raise a typed error carrying both raws."""
    with pytest.raises(UnparseableVersionError) as exc:
        is_newer(current, latest)
    assert exc.value.current == current
    assert exc.value.latest == latest
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_upgrade_versions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.upgrade.types'`

- [ ] **Step 4: Write `types.py`**

```python
"""Shared types for the upgrade subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class UpgradeError(Exception):
    """Raised when an upgrade operation fails."""


class UnparseableVersionError(UpgradeError):
    """Raised when a version tag cannot be interpreted (spec §4)."""

    def __init__(self, current: str, latest: str) -> None:
        super().__init__(
            f"Cannot compare versions: current={current!r} latest={latest!r}"
        )
        self.current = current
        self.latest = latest


class ExitCode(IntEnum):
    """Stable CLI exit codes — the agent routes on these (spec §7)."""

    OK = 0
    UNEXPECTED = 1
    SERVER_RUNNING = 10
    NO_ASSET_FOR_PLATFORM = 11
    VERIFICATION_FAILED = 12
    POST_CHECK_FAILED = 13
    NOT_FROZEN = 14
    LEGACY_LAYOUT = 15


class BlockedReason(str):
    """Stable `blocked_reason` values (spec §7)."""

    SERVER_RUNNING = "server_running"
    NO_ASSET_FOR_PLATFORM = "no_asset_for_platform"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    STAGED_BINARY_FAILED = "staged_binary_failed"
    UNPARSEABLE_VERSION = "unparseable_version"
    POST_CHECK_FAILED = "post_check_failed"
    NOT_FROZEN = "not_frozen"
    LEGACY_LAYOUT = "legacy_layout"
    UNEXPECTED = "unexpected"


@dataclass
class UpgradeResult:
    """The `--json` payload (spec §7). Field names are API."""

    current_version: str = ""
    latest_version: str = ""
    is_newer: bool = False
    asset_available: bool = False
    checksum_verified: bool = False
    action_taken: str = "none"  # none | upgraded | rolled_back | blocked
    active_version: str = ""
    previous_version: str = ""
    blocked_reason: str | None = None
    next_action: str | None = None
    warnings: list[str] = field(default_factory=list)
    exit_code: int = int(ExitCode.OK)

    def to_json_dict(self) -> dict:
        """Serialise, omitting the internal-only exit code."""
        payload = self.__dict__.copy()
        payload.pop("exit_code")
        return payload
```

- [ ] **Step 5: Write `versions.py`**

```python
"""Semantic version comparison for upgrade decisions (spec §4).

Replaces a string comparison that reported ``v0.10.0 < v0.9.0`` and
permanently pinned every 0.8.x/0.9.x install (spec §1.1).
"""
from __future__ import annotations

from packaging.version import InvalidVersion, Version

from src.services.upgrade.types import UnparseableVersionError


def parse_tag(tag: str) -> Version | None:
    """Parse a ``vX.Y.Z``-style git tag. Return ``None`` if uninterpretable."""
    if not tag:
        return None
    candidate = tag[1:] if tag[0] in ("v", "V") else tag
    try:
        return Version(candidate)
    except InvalidVersion:
        return None


def is_newer(current: str, latest: str) -> bool:
    """Return ``True`` when *latest* supersedes *current*.

    Raises :class:`UnparseableVersionError` when either side is
    uninterpretable — never guesses, never crashes with a bare exception.
    """
    parsed_current = parse_tag(current)
    parsed_latest = parse_tag(latest)
    if parsed_current is None or parsed_latest is None:
        raise UnparseableVersionError(current=current, latest=latest)
    return parsed_latest > parsed_current
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_upgrade_versions.py -v`
Expected: PASS — 20 passed.

- [ ] **Step 7: Commit**

```bash
git add src/services/upgrade/ tests/test_upgrade_versions.py
git commit -m "fix: compare versions semantically instead of as strings

String comparison reported v0.10.0 < v0.9.0, so every install on 0.8.x or
0.9.x was told it was already current and could never receive a fix. The
regression is pinned by an explicit v0.9.0 -> v0.10.0 case."
```

---

### Task 2: Install layout and atomic pointer switching

Implements spec §3.2, §3.4, §3.5. This is the task that makes rollback
possible, so its tests are the backbone of the whole feature.

**Files:**
- Create: `src/services/upgrade/layout.py`
- Test: `tests/test_upgrade_layout.py`

**Interfaces:**
- Consumes: `UpgradeError` from Task 1.
- Produces: `InstallLayout` with:
  - fields `root: Path`, `windows: bool`
  - `versions_dir`, `staging_dir`, `bin_dir`, `pointer_path` (properties)
  - `version_dir(version: str) -> Path`
  - `active_version() -> str | None`
  - `installed_versions() -> list[str]`
  - `activate(version: str) -> None`
  - `prune(keep: Sequence[str]) -> list[str]` (returns removed names)
  - `is_legacy() -> bool`
  - `ensure_entrypoint() -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_upgrade_layout.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_upgrade_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.upgrade.layout'`

- [ ] **Step 3: Write `layout.py`**

```python
"""Versioned install layout with atomic pointer switching (spec §3).

The upgrading process runs from ``versions/<old>/``, so activation never
rewrites the files of the running executable — that is what makes the
operation atomic on every platform, Windows included.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from src.services.upgrade.types import UpgradeError
from src.services.upgrade.versions import parse_tag

_POSIX_POINTER = "current"
_WINDOWS_POINTER = "current.txt"

_CMD_SHIM = """@echo off
setlocal
set /p ADM_VERSION=<"%~dp0..\\{pointer}"
"%~dp0..\\versions\\%ADM_VERSION%\\{exe}" %*
"""


def _default_windows() -> bool:
    return sys.platform == "win32"


@dataclass(frozen=True)
class InstallLayout:
    """Filesystem layout of a packaged install.

    Parameterised by artifact so the backend (``~/.uni-agent``) and the
    client (``~/.adm-agent-client``) share one mechanism — spec §3.6.
    ``windows`` is injectable so both pointer styles are testable on one host.
    """

    root: Path
    artifact_name: str = "adm-agent"
    windows: bool = field(default_factory=_default_windows)

    # ── paths ────────────────────────────────────────────────────────

    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    @property
    def staging_dir(self) -> Path:
        return self.root / "staging"

    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    @property
    def pointer_path(self) -> Path:
        return self.root / (_WINDOWS_POINTER if self.windows else _POSIX_POINTER)

    @property
    def executable_name(self) -> str:
        return f"{self.artifact_name}.exe" if self.windows else self.artifact_name

    @property
    def entrypoint_path(self) -> Path:
        """The stable command users and post-checks invoke."""
        name = f"{self.artifact_name}.cmd" if self.windows else self.artifact_name
        return self.bin_dir / name

    def version_dir(self, version: str) -> Path:
        return self.versions_dir / version

    # ── pointer ──────────────────────────────────────────────────────

    def active_version(self) -> str | None:
        """Name of the currently active version directory, or ``None``."""
        pointer = self.pointer_path
        if self.windows:
            if not pointer.is_file():
                return None
            return pointer.read_text(encoding="utf-8").strip() or None
        if not pointer.is_symlink():
            return None
        return Path(os.readlink(pointer)).name

    def activate(self, version: str) -> None:
        """Point the install at *version*. Atomic: ``os.replace`` either
        completes or leaves the previous pointer untouched."""
        if not self.version_dir(version).is_dir():
            raise UpgradeError(f"Version {version} is not installed")

        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".current.{os.getpid()}.tmp"
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        try:
            if self.windows:
                tmp.write_text(version, encoding="utf-8")
            else:
                # Relative target keeps the tree relocatable.
                os.symlink(Path("versions") / version, tmp, target_is_directory=True)
            os.replace(tmp, self.pointer_path)
        finally:
            if tmp.exists() or tmp.is_symlink():
                tmp.unlink()

    # ── entrypoint ───────────────────────────────────────────────────

    def ensure_entrypoint(self) -> None:
        """Create the stable ``bin/`` entry point resolving through the pointer.

        POSIX uses a symlink (PyInstaller resolves the real executable path,
        which is how today's ``~/.local/bin/adm-agent`` symlink already works).
        Windows uses a ``.cmd`` shim so no privilege is needed and no
        executable ever sits inside ``bin/`` where it could be file-locked.
        """
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        entry = self.entrypoint_path
        if self.windows:
            entry.write_text(
                _CMD_SHIM.format(pointer=_WINDOWS_POINTER, exe=self.executable_name),
                encoding="utf-8",
            )
            return

        if entry.exists() or entry.is_symlink():
            entry.unlink()
        os.symlink(Path("..") / _POSIX_POINTER / self.executable_name, entry)

    # ── inventory and retention ──────────────────────────────────────

    def installed_versions(self) -> list[str]:
        """Installed version directory names, newest first.

        Unparseable directory names sort last by name rather than raising —
        a stray directory must never make the install unupgradable.
        """
        if not self.versions_dir.is_dir():
            return []
        names = [p.name for p in self.versions_dir.iterdir() if p.is_dir()]
        parseable = [n for n in names if parse_tag(n) is not None]
        other = sorted(n for n in names if parse_tag(n) is None)
        parseable.sort(key=parse_tag, reverse=True)
        return parseable + other

    def prune(self, keep: Sequence[str]) -> list[str]:
        """Delete every installed version not named in *keep*."""
        active = self.active_version()
        if active is not None and active not in keep:
            raise UpgradeError(f"Refusing to prune the active version {active}")
        removed = []
        for name in self.installed_versions():
            if name in keep:
                continue
            shutil.rmtree(self.version_dir(name))
            removed.append(name)
        return removed

    # ── legacy layout (spec §3.5) ────────────────────────────────────

    def is_legacy(self) -> bool:
        """True for a pre-versioning flat install (spec §3.6).

        Artifact-agnostic: the backend's legacy shape is ``bin/_internal``,
        the client's is ``_internal`` at the root, and neither has
        ``versions/``.
        """
        if self.versions_dir.is_dir():
            return False
        return (self.bin_dir / "_internal").is_dir() or (self.root / "_internal").is_dir()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_upgrade_layout.py -v`
Expected: PASS — 15 passed.

- [ ] **Step 5: Commit**

```bash
git add src/services/upgrade/layout.py tests/test_upgrade_layout.py
git commit -m "feat: versioned install layout with atomic pointer switching

Activation is an os.replace of a pointer, so it either happened or it did
not; the previous version stays on disk byte-identical, which makes
rollback a repoint rather than a restore."
```

---

### Task 3: Release API client

Implements spec §6.1 and §7.1.

**Files:**
- Create: `src/services/upgrade/release.py`
- Test: `tests/test_upgrade_release.py`

**Interfaces:**
- Consumes: `UpgradeError`.
- Produces:
  - `release_api_base() -> str`
  - `latest_release_url() -> str`
  - `fetch_latest_release() -> dict`
  - `get_platform_info() -> tuple[str, str]`
  - `find_release_asset(release: dict, os_name: str, arch: str, artifact_name: str = "adm-agent") -> dict | None`
  - `find_checksums_asset(release: dict) -> dict | None`
  - `parse_checksums(text: str) -> dict[str, str]`

- [ ] **Step 1: Write the failing test**

`tests/test_upgrade_release.py`:

```python
"""Tests for release resolution and checksum parsing — spec §6.1, §7.1."""

import json
from unittest.mock import patch

import pytest

from src.services.upgrade.release import (
    find_checksums_asset,
    find_release_asset,
    get_platform_info,
    latest_release_url,
    parse_checksums,
    release_api_base,
)


def _release(*names: str) -> dict:
    return {
        "tag_name": "v0.11.0",
        "html_url": "https://example.invalid/r",
        "assets": [
            {"name": n, "browser_download_url": f"https://example.invalid/{n}", "size": 10}
            for n in names
        ],
    }


# ── endpoint override (spec §7.1) ─────────────────────────────────────


def test_default_endpoint_is_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADM_AGENT_RELEASE_API_BASE", raising=False)
    assert release_api_base() == "https://api.github.com/repos"
    assert latest_release_url().endswith("/dlfkid/uni-admission-agent/releases/latest")


def test_endpoint_override_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The release gate verifies an unpublished version, so this must redirect."""
    monkeypatch.setenv("ADM_AGENT_RELEASE_API_BASE", "http://127.0.0.1:9/repos/")
    assert release_api_base() == "http://127.0.0.1:9/repos"
    assert latest_release_url().startswith("http://127.0.0.1:9/repos/")


# ── asset matching ────────────────────────────────────────────────────


def test_find_release_asset_matches_platform_artifact() -> None:
    release = _release(
        "adm-agent-v0.11.0-macos-arm64.tar.gz",
        "adm-agent-v0.11.0-linux-x86_64.tar.gz",
    )
    asset = find_release_asset(release, "macos", "arm64")
    assert asset is not None
    assert asset["name"] == "adm-agent-v0.11.0-macos-arm64.tar.gz"


def test_find_release_asset_uses_zip_on_windows() -> None:
    release = _release("adm-agent-v0.11.0-windows-x86_64.zip")
    assert find_release_asset(release, "windows", "x86_64") is not None


def test_find_release_asset_returns_none_when_absent() -> None:
    release = _release("adm-agent-v0.11.0-macos-arm64.tar.gz")
    assert find_release_asset(release, "linux", "arm64") is None


def test_find_release_asset_selects_the_client_artifact() -> None:
    release = _release(
        "adm-agent-v0.11.0-linux-x86_64.tar.gz",
        "adm-agent-client-v0.11.0-linux-x86_64.tar.gz",
    )
    asset = find_release_asset(release, "linux", "x86_64", artifact_name="adm-agent-client")
    assert asset["name"] == "adm-agent-client-v0.11.0-linux-x86_64.tar.gz"


def test_get_platform_info_normalises() -> None:
    with patch("platform.system", return_value="Darwin"), patch(
        "platform.machine", return_value="arm64"
    ):
        assert get_platform_info() == ("macos", "arm64")


# ── checksums ─────────────────────────────────────────────────────────


def test_find_checksums_asset() -> None:
    release = _release("adm-agent-v0.11.0-macos-arm64.tar.gz", "SHA256SUMS")
    assert find_checksums_asset(release)["name"] == "SHA256SUMS"


def test_find_checksums_asset_absent_on_old_releases() -> None:
    """Releases predating the gate have no SHA256SUMS (spec §6.1)."""
    assert find_checksums_asset(_release("adm-agent-v0.11.0-macos-arm64.tar.gz")) is None


def test_parse_checksums_reads_sha256sum_format() -> None:
    text = (
        "abc123  adm-agent-v0.11.0-macos-arm64.tar.gz\n"
        "def456 *adm-agent-v0.11.0-windows-x86_64.zip\n"
        "\n"
    )
    parsed = parse_checksums(text)
    assert parsed["adm-agent-v0.11.0-macos-arm64.tar.gz"] == "abc123"
    assert parsed["adm-agent-v0.11.0-windows-x86_64.zip"] == "def456"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_upgrade_release.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.upgrade.release'`

- [ ] **Step 3: Write `release.py`**

```python
"""GitHub release resolution (spec §6.1, §7.1)."""
from __future__ import annotations

import json
import os
import platform
import ssl
from urllib.request import Request, urlopen

from src.services.upgrade.types import UpgradeError

try:
    import certifi

    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover - depends on the frozen bundle
    _SSL_CONTEXT = ssl.create_default_context()

GITHUB_REPO = "dlfkid/uni-admission-agent"
DEFAULT_API_BASE = "https://api.github.com/repos"
CHECKSUMS_ASSET_NAME = "SHA256SUMS"


def release_api_base() -> str:
    """Release API root. Overridable for the release gate and unit tests."""
    return os.environ.get("ADM_AGENT_RELEASE_API_BASE", DEFAULT_API_BASE).rstrip("/")


def latest_release_url() -> str:
    return f"{release_api_base()}/{GITHUB_REPO}/releases/latest"


def fetch_latest_release() -> dict:
    """Fetch the release marked latest. Raises :class:`UpgradeError`."""
    try:
        with urlopen(latest_release_url(), timeout=30, context=_SSL_CONTEXT) as response:
            if response.status != 200:
                raise UpgradeError(f"Release API returned status {response.status}")
            return json.loads(response.read().decode())
    except UpgradeError:
        raise
    except Exception as exc:
        raise UpgradeError(f"Failed to fetch release information: {exc}") from exc


def fetch_text(url: str) -> str:
    """Fetch a small text asset (used for SHA256SUMS)."""
    try:
        with urlopen(Request(url), timeout=60, context=_SSL_CONTEXT) as response:
            return response.read().decode()
    except Exception as exc:
        raise UpgradeError(f"Failed to fetch {url}: {exc}") from exc


def get_platform_info() -> tuple[str, str]:
    """Return ``(os_name, arch_name)`` used in release artifact filenames."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_name = "macos"
    elif system == "windows":
        os_name = "windows"
    else:
        os_name = "linux"

    if machine in ("amd64", "x86_64"):
        arch_name = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch_name = "arm64"
    else:
        arch_name = machine

    return os_name, arch_name


def find_release_asset(
    release: dict,
    os_name: str,
    arch_name: str,
    artifact_name: str = "adm-agent",
) -> dict | None:
    """Find the asset matching ``<artifact>-<tag>-<os>-<arch>.<ext>``."""
    version = release.get("tag_name", "unknown")
    extension = ".zip" if os_name == "windows" else ".tar.gz"
    expected = f"{artifact_name}-{version}-{os_name}-{arch_name}{extension}"
    for asset in release.get("assets", []):
        if asset.get("name") == expected:
            return asset
    return None


def find_checksums_asset(release: dict) -> dict | None:
    """Find the ``SHA256SUMS`` asset. Absent on releases predating the gate."""
    for asset in release.get("assets", []):
        if asset.get("name") == CHECKSUMS_ASSET_NAME:
            return asset
    return None


def parse_checksums(text: str) -> dict[str, str]:
    """Parse ``sha256sum`` output into ``{filename: digest}``."""
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition(" ")
        name = name.strip().lstrip("*")
        if digest and name:
            parsed[name] = digest
    return parsed
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_upgrade_release.py -v`
Expected: PASS — 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/services/upgrade/release.py tests/test_upgrade_release.py
git commit -m "feat: release client with overridable endpoint and SHA256SUMS

The endpoint override is a requirement, not a test convenience: the release
gate must verify an upgrade into a version that is not published yet."
```

---

### Task 4: Staging — download, safe extract, verification

Implements spec §5 steps 3–5, §6.1, §6.2. The staged-binary self-check is
the gate that keeps a corrupt download from ever becoming the live install.

**Files:**
- Create: `src/services/upgrade/staging.py`
- Test: `tests/test_upgrade_staging.py`

**Interfaces:**
- Consumes: `UpgradeError`, `InstallLayout`.
- Produces:
  - `ChecksumOutcome` dataclass: `.verified: bool`, `.warnings: list[str]`
  - `verify_artifact(path: Path, expected_digest: str | None, expected_size: int | None) -> ChecksumOutcome`
  - `safe_extract(archive: Path, dest: Path) -> Path` (returns the single top-level dir)
  - `clear_quarantine(path: Path) -> None`
  - `verify_staged_binary(staged_dir: Path, expected_version: str, executable_name: str) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_upgrade_staging.py`:

```python
"""Tests for staging: checksum, safe extraction, staged self-check — spec §6."""

import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path

import pytest

from src.services.upgrade.staging import (
    ChecksumOutcome,
    safe_extract,
    verify_artifact,
    verify_staged_binary,
)
from src.services.upgrade.types import UpgradeError


def _write_archive(tmp_path: Path, top: str = "adm-agent") -> Path:
    payload = tmp_path / "payload" / top
    (payload / "_internal").mkdir(parents=True)
    (payload / "adm-agent").write_text("binary")
    archive = tmp_path / "artifact.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname=top)
    return archive


# ── checksum (spec §6.1) ──────────────────────────────────────────────


def test_matching_digest_verifies(tmp_path: Path) -> None:
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    outcome = verify_artifact(blob, expected_digest=digest, expected_size=5)
    assert outcome == ChecksumOutcome(verified=True, warnings=[])


def test_mismatched_digest_is_always_fatal(tmp_path: Path) -> None:
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"hello")
    with pytest.raises(UpgradeError, match="checksum"):
        verify_artifact(blob, expected_digest="0" * 64, expected_size=5)


def test_missing_digest_degrades_to_size_with_a_warning(tmp_path: Path) -> None:
    """Old releases have no SHA256SUMS; blocking them would re-pin users."""
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"hello")
    outcome = verify_artifact(blob, expected_digest=None, expected_size=5)
    assert outcome.verified is False
    assert any("SHA256SUMS" in w for w in outcome.warnings)


def test_missing_digest_with_wrong_size_is_fatal(tmp_path: Path) -> None:
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"hello")
    with pytest.raises(UpgradeError, match="size"):
        verify_artifact(blob, expected_digest=None, expected_size=999)


# ── extraction ────────────────────────────────────────────────────────


def test_safe_extract_returns_the_single_top_level_dir(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path)
    dest = tmp_path / "out"
    top = safe_extract(archive, dest)
    assert top.name == "adm-agent"
    assert (top / "_internal").is_dir()


def test_safe_extract_rejects_traversal_members(tmp_path: Path) -> None:
    """A member escaping the extraction root must abort the upgrade."""
    evil = tmp_path / "evil.tar.gz"
    victim = tmp_path / "outside.txt"
    victim.write_text("original")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(victim, arcname="../outside.txt")
    with pytest.raises(UpgradeError):
        safe_extract(evil, tmp_path / "out")
    assert victim.read_text() == "original"


def test_safe_extract_rejects_a_truncated_archive(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path)
    data = archive.read_bytes()
    archive.write_bytes(data[: len(data) // 2])
    with pytest.raises(UpgradeError):
        safe_extract(archive, tmp_path / "out")


# ── staged self-check (spec §6.2) ─────────────────────────────────────


def _fake_binary(staged: Path, name: str, stdout: str, exit_code: int = 0) -> None:
    staged.mkdir(parents=True, exist_ok=True)
    exe = staged / name
    exe.write_text(f"#!/bin/sh\ncat <<'EOF'\n{stdout}\nEOF\nexit {exit_code}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub")
def test_staged_binary_reporting_the_expected_version_passes(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    _fake_binary(staged, "adm-agent", json.dumps({"version": "v0.11.0"}))
    verify_staged_binary(staged, expected_version="v0.11.0", executable_name="adm-agent")


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub")
def test_staged_binary_with_a_wrong_version_aborts(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    _fake_binary(staged, "adm-agent", json.dumps({"version": "v0.9.0"}))
    with pytest.raises(UpgradeError, match="expected v0.11.0"):
        verify_staged_binary(staged, expected_version="v0.11.0", executable_name="adm-agent")


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub")
def test_staged_binary_that_exits_non_zero_aborts(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    _fake_binary(staged, "adm-agent", "boom", exit_code=3)
    with pytest.raises(UpgradeError):
        verify_staged_binary(staged, expected_version="v0.11.0", executable_name="adm-agent")


def test_staged_binary_missing_aborts(tmp_path: Path) -> None:
    with pytest.raises(UpgradeError, match="not found"):
        verify_staged_binary(tmp_path, expected_version="v0.11.0", executable_name="adm-agent")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_upgrade_staging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.upgrade.staging'`

- [ ] **Step 3: Write `staging.py`**

```python
"""Download, extract and verify a candidate version before activation.

Everything here happens in ``staging/``; the live install is untouched until
:mod:`src.services.upgrade.transaction` moves the verified tree into
``versions/`` and repoints (spec §5).
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from src.services.upgrade.types import UpgradeError

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024


@dataclass
class ChecksumOutcome:
    """Result of artifact verification (spec §6.1)."""

    verified: bool
    warnings: list[str] = field(default_factory=list)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(
    path: Path,
    expected_digest: str | None,
    expected_size: int | None,
) -> ChecksumOutcome:
    """Verify a downloaded artifact.

    A present-and-mismatched digest is always fatal. A *missing* digest
    degrades to an exact size check plus a warning, because hard-failing
    would make every pre-SHA256SUMS release un-upgradable — reintroducing
    the symptom this work exists to remove.
    """
    actual_size = path.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        raise UpgradeError(
            f"Downloaded artifact size {actual_size} does not match the "
            f"published size {expected_size} — download was truncated"
        )

    if expected_digest is None:
        return ChecksumOutcome(
            verified=False,
            warnings=[
                "This release publishes no SHA256SUMS; verified size only. "
                "Integrity is not cryptographically confirmed."
            ],
        )

    actual = sha256_of(path)
    if actual.lower() != expected_digest.lower():
        raise UpgradeError(
            f"Artifact checksum mismatch: expected {expected_digest}, got {actual}"
        )
    return ChecksumOutcome(verified=True)


def _assert_within(root: Path, target: Path) -> None:
    resolved_root = root.resolve()
    resolved_target = (root / target).resolve()
    if not str(resolved_target).startswith(str(resolved_root)):
        raise UpgradeError(f"Archive member escapes the extraction root: {target}")


def safe_extract(archive: Path, dest: Path) -> Path:
    """Extract *archive* into *dest*; return its single top-level directory."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    _assert_within(dest, Path(member))
                zf.extractall(dest)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                for member in tf.getmembers():
                    _assert_within(dest, Path(member.name))
                tf.extractall(dest, filter="data")
    except UpgradeError:
        raise
    except Exception as exc:
        raise UpgradeError(f"Failed to extract {archive.name}: {exc}") from exc

    entries = [p for p in dest.iterdir() if p.is_dir()]
    if len(entries) != 1:
        raise UpgradeError(
            f"Unexpected archive structure in {archive.name}: "
            f"expected one top-level directory, found {len(entries)}"
        )
    return entries[0]


def clear_quarantine(path: Path) -> None:
    """Strip the macOS quarantine xattr so Gatekeeper allows the self-check.

    Without this every staged-binary verification fails on macOS. Best effort:
    a missing ``xattr`` binary is not an upgrade failure.
    """
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["xattr", "-cr", str(path)], check=False, capture_output=True, timeout=60
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not clear quarantine attribute: %s", exc)


def verify_staged_binary(
    staged_dir: Path,
    expected_version: str,
    executable_name: str,
) -> None:
    """Run the staged binary and confirm it reports *expected_version*.

    This is the gate that matters most: it catches truncated downloads,
    wrong-architecture artifacts, a missing ``_internal`` and broken builds
    while the live install is still untouched.
    """
    executable = staged_dir / executable_name
    if not executable.is_file():
        raise UpgradeError(f"Staged executable {executable_name} not found")
    executable.chmod(0o755)
    clear_quarantine(staged_dir)

    try:
        proc = subprocess.run(
            [str(executable), "version", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:
        raise UpgradeError(f"Staged binary could not be executed: {exc}") from exc

    if proc.returncode != 0:
        raise UpgradeError(
            f"Staged binary self-check failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    try:
        reported = json.loads(proc.stdout)["version"]
    except Exception as exc:
        raise UpgradeError(
            f"Staged binary produced unparseable version output: {proc.stdout!r}"
        ) from exc

    if reported != expected_version:
        raise UpgradeError(
            f"Staged binary reports {reported}, expected {expected_version}"
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_upgrade_staging.py -v`
Expected: PASS — 12 passed.

- [ ] **Step 5: Commit**

```bash
git add src/services/upgrade/staging.py tests/test_upgrade_staging.py
git commit -m "feat: verify artifacts and smoke-test the staged binary

Runs the candidate binary and matches its reported version before anything
is activated, so corrupt or wrong-arch downloads are rejected while the
live install is still untouched. Extraction now rejects traversal members."
```

---

### Task 5: Preflight gates

Implements spec §9 and exit codes 10/14/15.

**Files:**
- Create: `src/services/upgrade/preflight.py`
- Test: `tests/test_upgrade_preflight.py`

**Interfaces:**
- Consumes: `InstallLayout`, `BlockedReason`, `ExitCode`.
- Produces:
  - `is_process_alive(pid: int) -> bool`
  - `is_server_running(pid_file: Path, health_url: str) -> bool`
  - `PreflightBlock` dataclass: `.reason: str`, `.exit_code: int`, `.message: str`, `.next_action: str`
  - `run_preflight(layout: InstallLayout, *, frozen: bool, pid_file: Path, health_url: str) -> PreflightBlock | None`

- [ ] **Step 1: Write the failing test**

`tests/test_upgrade_preflight.py`:

```python
"""Tests for upgrade preflight gates — spec §9."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.preflight import is_server_running, run_preflight
from src.services.upgrade.types import BlockedReason, ExitCode

_HEALTH = "http://127.0.0.1:8910/health"


def _versioned(tmp_path: Path) -> InstallLayout:
    layout = InstallLayout(root=tmp_path, windows=False)
    vdir = layout.version_dir("v0.10.0")
    (vdir / "_internal").mkdir(parents=True)
    (vdir / "adm-agent").write_text("x")
    layout.activate("v0.10.0")
    return layout


# ── server detection ──────────────────────────────────────────────────


def test_live_pid_file_means_running(tmp_path: Path) -> None:
    pid_file = tmp_path / "server.pid"
    pid_file.write_text(str(os.getpid()))
    with patch("src.services.upgrade.preflight._probe_health", return_value=False):
        assert is_server_running(pid_file, _HEALTH) is True


def test_stale_pid_file_does_not_block(tmp_path: Path) -> None:
    """A leftover PID file from a crashed server must not pin the user."""
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("999999")
    with patch("src.services.upgrade.preflight.is_process_alive", return_value=False), patch(
        "src.services.upgrade.preflight._probe_health", return_value=False
    ):
        assert is_server_running(pid_file, _HEALTH) is False


def test_health_probe_alone_means_running(tmp_path: Path) -> None:
    """A server started without a PID file still blocks."""
    with patch("src.services.upgrade.preflight._probe_health", return_value=True):
        assert is_server_running(tmp_path / "absent.pid", _HEALTH) is True


def test_unreadable_pid_file_is_ignored(tmp_path: Path) -> None:
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("not-a-pid")
    with patch("src.services.upgrade.preflight._probe_health", return_value=False):
        assert is_server_running(pid_file, _HEALTH) is False


# ── gate ordering ─────────────────────────────────────────────────────


def test_source_checkout_blocks_with_14(tmp_path: Path) -> None:
    block = run_preflight(
        _versioned(tmp_path), frozen=False, pid_file=tmp_path / "p", health_url=_HEALTH
    )
    assert block is not None
    assert block.exit_code == ExitCode.NOT_FROZEN
    assert block.reason == BlockedReason.NOT_FROZEN


def test_legacy_layout_blocks_with_15(tmp_path: Path) -> None:
    (tmp_path / "bin" / "_internal").mkdir(parents=True)
    layout = InstallLayout(root=tmp_path, windows=False)
    with patch("src.services.upgrade.preflight.is_server_running", return_value=False):
        block = run_preflight(
            layout, frozen=True, pid_file=tmp_path / "p", health_url=_HEALTH
        )
    assert block.exit_code == ExitCode.LEGACY_LAYOUT
    assert "reinstall" in block.next_action


def test_running_server_blocks_with_10(tmp_path: Path) -> None:
    with patch("src.services.upgrade.preflight.is_server_running", return_value=True):
        block = run_preflight(
            _versioned(tmp_path), frozen=True, pid_file=tmp_path / "p", health_url=_HEALTH
        )
    assert block.exit_code == ExitCode.SERVER_RUNNING
    assert block.next_action == "stop_server_then_retry"


def test_clean_install_passes(tmp_path: Path) -> None:
    with patch("src.services.upgrade.preflight.is_server_running", return_value=False):
        assert (
            run_preflight(
                _versioned(tmp_path), frozen=True, pid_file=tmp_path / "p", health_url=_HEALTH
            )
            is None
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_upgrade_preflight.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.upgrade.preflight'`

- [ ] **Step 3: Write `preflight.py`**

```python
"""Gates that must pass before any bytes are downloaded (spec §9)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.types import BlockedReason, ExitCode


@dataclass(frozen=True)
class PreflightBlock:
    """A refusal to proceed, carrying everything the agent needs to route."""

    reason: str
    exit_code: int
    message: str
    next_action: str


def is_process_alive(pid: int) -> bool:
    """True when *pid* names a live process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _probe_health(health_url: str) -> bool:
    if not health_url:
        return False  # artifacts without a health endpoint (e.g. the client)
    try:
        with urlopen(health_url, timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def is_server_running(pid_file: Path, health_url: str) -> bool:
    """True when a server is serving. A stale PID file must not block."""
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            pid = None
        if pid is not None and is_process_alive(pid):
            return True
    return _probe_health(health_url)


def run_preflight(
    layout: InstallLayout,
    *,
    frozen: bool,
    pid_file: Path,
    health_url: str,
) -> PreflightBlock | None:
    """Return a block, or ``None`` when the upgrade may proceed."""
    if not frozen:
        return PreflightBlock(
            reason=BlockedReason.NOT_FROZEN,
            exit_code=int(ExitCode.NOT_FROZEN),
            message=(
                "Self-upgrade only applies to packaged installs. "
                "This is a source checkout — update with git and uv sync."
            ),
            next_action="update_source_checkout_with_git",
        )

    if layout.is_legacy():
        return PreflightBlock(
            reason=BlockedReason.LEGACY_LAYOUT,
            exit_code=int(ExitCode.LEGACY_LAYOUT),
            message=(
                "This install predates versioned layouts. Re-run the installer "
                "once to migrate; your .env and database are preserved."
            ),
            next_action="reinstall_to_migrate_layout",
        )

    if is_server_running(pid_file, health_url):
        return PreflightBlock(
            reason=BlockedReason.SERVER_RUNNING,
            exit_code=int(ExitCode.SERVER_RUNNING),
            message="The server is running. Stop it, then run the upgrade again.",
            next_action="stop_server_then_retry",
        )

    return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_upgrade_preflight.py -v`
Expected: PASS — 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/services/upgrade/preflight.py tests/test_upgrade_preflight.py
git commit -m "feat: preflight gates for frozen mode, legacy layout, live server

Refuses rather than orchestrating the server lifecycle (the agent owns
stop/start), and a stale PID file from a crashed server never blocks."
```

---

### Task 6: The upgrade transaction

Implements spec §5 and §6.3 — the eight steps, the rollback semantics and
the deliberate post-check asymmetry.

**Files:**
- Create: `src/services/upgrade/transaction.py`
- Test: `tests/test_upgrade_transaction.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces:
  - `check_for_updates(artifact_name: str = "adm-agent") -> UpgradeResult`
  - `perform_upgrade(layout, *, artifact_name="adm-agent", force=False, migrate=True, frozen=True, pid_file, health_url, downloader=None, post_check=None) -> UpgradeResult`
  - `rollback(layout: InstallLayout) -> UpgradeResult`

`downloader` and `post_check` are injected callables (default to the real
implementations) so the transaction is testable without network or
subprocesses:
  - `downloader(asset: dict, dest_dir: Path) -> Path` returns the archive path
  - `post_check(layout: InstallLayout, migrate: bool) -> list[str]` returns
    warnings; raises `UpgradeError` when migration is unrecoverable

- [ ] **Step 1: Write the failing test**

`tests/test_upgrade_transaction.py`:

```python
"""Tests for the upgrade transaction — spec §5, §6.3."""

import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.transaction import perform_upgrade, rollback
from src.services.upgrade.types import BlockedReason, ExitCode, UpgradeError

_HEALTH = "http://127.0.0.1:8910/health"


def _install(tmp_path: Path, version: str = "v0.10.0") -> InstallLayout:
    layout = InstallLayout(root=tmp_path, windows=False)
    vdir = layout.version_dir(version)
    (vdir / "_internal").mkdir(parents=True)
    (vdir / "adm-agent").write_text("old")
    layout.activate(version)
    layout.ensure_entrypoint()
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=secret\n")
    (tmp_path / "admission.db").write_bytes(b"SQLite format 3\x00")
    return layout


def _release(tag: str = "v0.11.0") -> dict:
    return {
        "tag_name": tag,
        "html_url": "https://example.invalid/r",
        "assets": [
            {
                "name": f"adm-agent-{tag}-linux-x86_64.tar.gz",
                "browser_download_url": "https://example.invalid/a.tar.gz",
                "size": 10,
            },
            {
                "name": "SHA256SUMS",
                "browser_download_url": "https://example.invalid/SHA256SUMS",
                "size": 10,
            },
        ],
    }


def _fake_downloader(tmp_path: Path, tag: str = "v0.11.0"):
    """Produce an archive whose payload is a plausible new version."""

    def download(asset: dict, dest_dir: Path) -> Path:
        payload = tmp_path / f"payload-{tag}" / "adm-agent"
        (payload / "_internal").mkdir(parents=True, exist_ok=True)
        (payload / "adm-agent").write_text(tag)
        archive = dest_dir / "artifact.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(payload, arcname="adm-agent")
        return archive

    return download


def _run(
    layout: InstallLayout,
    tmp_path: Path,
    *,
    release: dict | None = None,
    current_version: str = "v0.10.0",
    platform_info: tuple[str, str] = ("linux", "x86_64"),
    **kwargs,
):
    """Drive perform_upgrade with every external dependency stubbed.

    Environment overrides go through the keyword arguments here — patching
    the same targets *around* this helper would be silently overridden by the
    patches below.
    """
    defaults = dict(
        artifact_name="adm-agent",
        frozen=True,
        pid_file=tmp_path / "server.pid",
        health_url=_HEALTH,
        downloader=_fake_downloader(tmp_path),
        post_check=lambda layout, migrate: [],
    )
    defaults.update(kwargs)
    with patch(
        "src.services.upgrade.transaction.fetch_latest_release",
        return_value=release if release is not None else _release(),
    ), patch(
        "src.services.upgrade.transaction.get_platform_info", return_value=platform_info
    ), patch(
        "src.services.upgrade.transaction.get_current_version", return_value=current_version
    ), patch(
        "src.services.upgrade.transaction.verify_staged_binary", return_value=None
    ), patch(
        "src.services.upgrade.transaction.resolve_expected_digest", return_value=None
    ), patch(
        "src.services.upgrade.preflight.is_server_running", return_value=False
    ):
        return perform_upgrade(layout, **defaults)


def _assert_user_data_intact(tmp_path: Path) -> None:
    assert (tmp_path / ".env").read_text() == "DEEPSEEK_API_KEY=secret\n"
    assert (tmp_path / "admission.db").read_bytes() == b"SQLite format 3\x00"


# ── happy path ────────────────────────────────────────────────────────


def test_successful_upgrade_activates_and_retains_previous(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    result = _run(layout, tmp_path)
    assert result.exit_code == ExitCode.OK
    assert result.action_taken == "upgraded"
    assert layout.active_version() == "v0.11.0"
    assert layout.version_dir("v0.10.0").exists()  # last-good retained
    _assert_user_data_intact(tmp_path)


def test_no_upgrade_when_already_current(tmp_path: Path) -> None:
    layout = _install(tmp_path, version="v0.11.0")
    result = _run(layout, tmp_path, current_version="v0.11.0")
    assert result.action_taken == "none"
    assert result.is_newer is False
    assert result.exit_code == ExitCode.OK


# ── pre-activation failures leave nothing changed ─────────────────────


def test_checksum_failure_leaves_the_pointer_untouched(tmp_path: Path) -> None:
    layout = _install(tmp_path)

    def boom(path, expected_digest, expected_size):
        raise UpgradeError("Artifact checksum mismatch: expected a, got b")

    with patch("src.services.upgrade.transaction.verify_artifact", side_effect=boom):
        result = _run(layout, tmp_path)
    assert result.exit_code == ExitCode.VERIFICATION_FAILED
    assert result.blocked_reason == BlockedReason.CHECKSUM_MISMATCH
    assert layout.active_version() == "v0.10.0"
    assert not layout.version_dir("v0.11.0").exists()
    _assert_user_data_intact(tmp_path)


def test_staged_binary_failure_leaves_the_pointer_untouched(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    with patch(
        "src.services.upgrade.transaction.verify_staged_binary",
        side_effect=UpgradeError("Staged binary reports v0.9.0, expected v0.11.0"),
    ):
        result = _run(layout, tmp_path)
    assert result.exit_code == ExitCode.VERIFICATION_FAILED
    assert result.blocked_reason == BlockedReason.STAGED_BINARY_FAILED
    assert layout.active_version() == "v0.10.0"
    _assert_user_data_intact(tmp_path)


def test_staging_is_cleaned_up_after_failure(tmp_path: Path) -> None:
    """A failed attempt must not leave half-extracted payloads behind."""
    layout = _install(tmp_path)
    with patch(
        "src.services.upgrade.transaction.verify_staged_binary",
        side_effect=UpgradeError("nope"),
    ):
        _run(layout, tmp_path)
    leftovers = list(layout.staging_dir.iterdir()) if layout.staging_dir.exists() else []
    assert leftovers == []


def test_missing_platform_asset_blocks_with_11(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    result = _run(layout, tmp_path, platform_info=("linux", "riscv64"))
    assert result.exit_code == ExitCode.NO_ASSET_FOR_PLATFORM
    assert layout.active_version() == "v0.10.0"


def test_unparseable_version_blocks_with_12(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    result = _run(layout, tmp_path, release=_release(tag="latest"))
    assert result.exit_code == ExitCode.VERIFICATION_FAILED
    assert result.blocked_reason == BlockedReason.UNPARSEABLE_VERSION
    assert layout.active_version() == "v0.10.0"


# ── post-activation asymmetry (spec §6.3) ─────────────────────────────


def test_migration_failure_rolls_back_and_deletes_the_bad_version(tmp_path: Path) -> None:
    layout = _install(tmp_path)

    def failing_post_check(layout, migrate):
        raise UpgradeError("migration failed and repair --auto could not fix it")

    result = _run(layout, tmp_path, post_check=failing_post_check)
    assert result.exit_code == ExitCode.POST_CHECK_FAILED
    assert result.action_taken == "rolled_back"
    assert layout.active_version() == "v0.10.0"
    # An automatically rolled-back version is proven bad; it is removed.
    assert not layout.version_dir("v0.11.0").exists()
    _assert_user_data_intact(tmp_path)


def test_check_warnings_do_not_roll_back(tmp_path: Path) -> None:
    """A missing Chromium is an environment problem; rolling back won't fix it."""
    layout = _install(tmp_path)
    result = _run(
        layout,
        tmp_path,
        post_check=lambda layout, migrate: ["Chromium is not installed"],
    )
    assert result.exit_code == ExitCode.OK
    assert result.action_taken == "upgraded"
    assert layout.active_version() == "v0.11.0"
    assert "Chromium is not installed" in result.warnings


# ── manual rollback (spec §3.2 retention) ─────────────────────────────


def test_manual_rollback_keeps_the_version_rolled_back_from(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    _run(layout, tmp_path)
    assert layout.active_version() == "v0.11.0"

    result = rollback(layout)
    assert result.action_taken == "rolled_back"
    assert layout.active_version() == "v0.10.0"
    # Retained so the user can move forward again without re-downloading.
    assert layout.version_dir("v0.11.0").exists()


def test_rollback_without_a_previous_version_errors(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    with pytest.raises(UpgradeError, match="no previous version"):
        rollback(layout)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_upgrade_transaction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.upgrade.transaction'`

- [ ] **Step 3: Write `transaction.py`**

```python
"""The upgrade transaction (spec §5).

Ordering is the whole design: everything that can fail is done in
``staging/`` first, so any pre-activation failure leaves the install
byte-identical. Only then does the pointer move.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.preflight import run_preflight
from src.services.upgrade.release import (
    fetch_latest_release,
    fetch_text,
    find_checksums_asset,
    find_release_asset,
    get_platform_info,
    parse_checksums,
)
from src.services.upgrade.staging import (
    safe_extract,
    verify_artifact,
    verify_staged_binary,
)
from src.services.upgrade.types import (
    BlockedReason,
    ExitCode,
    UnparseableVersionError,
    UpgradeError,
    UpgradeResult,
)
from src.services.upgrade.versions import is_newer

logger = logging.getLogger(__name__)

RETAIN_VERSIONS = 2


def get_current_version() -> str:
    """Current version, injected into ``src.__version__`` at build time."""
    try:
        from src import __version__

        return __version__ if __version__.startswith("v") else f"v{__version__}"
    except ImportError:
        return "v0.0.0-dev"


def resolve_expected_digest(release: dict, asset_name: str) -> str | None:
    """Digest for *asset_name*, or ``None`` on releases without SHA256SUMS."""
    checksums_asset = find_checksums_asset(release)
    if checksums_asset is None:
        return None
    text = fetch_text(checksums_asset["browser_download_url"])
    return parse_checksums(text).get(asset_name)


def default_downloader(asset: dict, dest_dir: Path) -> Path:
    """Stream a release asset into *dest_dir*."""
    from urllib.request import Request, urlopen

    from src.services.upgrade.release import _SSL_CONTEXT

    target = dest_dir / asset["name"]
    try:
        with urlopen(Request(asset["browser_download_url"]), timeout=300,
                     context=_SSL_CONTEXT) as response:
            with target.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except Exception as exc:
        raise UpgradeError(f"Failed to download {asset['name']}: {exc}") from exc
    return target


def default_post_check(layout: InstallLayout, migrate: bool) -> list[str]:
    """Run ``check`` (warn only) then ``db-migrate`` (fatal) — spec §6.3.

    Backend-only: ``adm-agent-client`` has neither command, and running the
    activated client binary with backend arguments would fail spuriously.
    """
    if layout.artifact_name != "adm-agent":
        return []

    warnings: list[str] = []
    entry = layout.entrypoint_path

    check = subprocess.run(
        [str(entry), "check"], capture_output=True, text=True, check=False, timeout=600
    )
    if check.returncode != 0:
        warnings.append(
            "Post-upgrade environment check reported problems (not caused by "
            f"the upgrade; not rolled back): {check.stdout.strip()[:400]}"
        )

    if not migrate:
        return warnings

    migration = subprocess.run(
        [str(entry), "db-migrate", "--yes"],
        capture_output=True, text=True, check=False, timeout=1800,
    )
    if migration.returncode == 0:
        return warnings

    repair = subprocess.run(
        [str(entry), "repair", "--auto"],
        capture_output=True, text=True, check=False, timeout=1800,
    )
    if repair.returncode == 0:
        warnings.append("Database migration failed but auto-repair recovered it.")
        return warnings

    raise UpgradeError(
        "Database migration failed and auto-repair could not recover: "
        f"{migration.stderr.strip()[:400]}"
    )


def check_for_updates(artifact_name: str = "adm-agent") -> UpgradeResult:
    """Resolve the latest release without changing anything."""
    result = UpgradeResult(current_version=get_current_version())
    try:
        release = fetch_latest_release()
    except UpgradeError as exc:
        result.blocked_reason = BlockedReason.UNEXPECTED
        result.action_taken = "blocked"
        result.exit_code = int(ExitCode.UNEXPECTED)
        result.warnings.append(str(exc))
        return result

    result.latest_version = release.get("tag_name", "")
    try:
        result.is_newer = is_newer(result.current_version, result.latest_version)
    except UnparseableVersionError as exc:
        result.blocked_reason = BlockedReason.UNPARSEABLE_VERSION
        result.action_taken = "blocked"
        result.exit_code = int(ExitCode.VERIFICATION_FAILED)
        result.warnings.append(str(exc))
        return result

    os_name, arch = get_platform_info()
    result.asset_available = (
        find_release_asset(release, os_name, arch, artifact_name) is not None
    )
    return result


# pylint: disable=too-many-locals,too-many-return-statements
def perform_upgrade(
    layout: InstallLayout,
    *,
    artifact_name: str = "adm-agent",
    force: bool = False,
    migrate: bool = True,
    frozen: bool = True,
    pid_file: Path,
    health_url: str,
    downloader: Callable[[dict, Path], Path] | None = None,
    post_check: Callable[[InstallLayout, bool], list[str]] | None = None,
) -> UpgradeResult:
    """Execute the eight-step transaction. Never raises for expected failures."""
    downloader = downloader or default_downloader
    post_check = post_check or default_post_check

    result = UpgradeResult(
        current_version=get_current_version(),
        active_version=layout.active_version() or "",
    )

    block = run_preflight(layout, frozen=frozen, pid_file=pid_file, health_url=health_url)
    if block is not None:
        result.action_taken = "blocked"
        result.blocked_reason = block.reason
        result.next_action = block.next_action
        result.exit_code = block.exit_code
        result.warnings.append(block.message)
        return result

    # 2. resolve
    try:
        release = fetch_latest_release()
    except UpgradeError as exc:
        return _blocked(result, BlockedReason.UNEXPECTED, ExitCode.UNEXPECTED, str(exc))

    result.latest_version = release.get("tag_name", "")
    try:
        result.is_newer = is_newer(result.current_version, result.latest_version)
    except UnparseableVersionError as exc:
        return _blocked(
            result, BlockedReason.UNPARSEABLE_VERSION, ExitCode.VERIFICATION_FAILED, str(exc)
        )

    if not result.is_newer and not force:
        result.action_taken = "none"
        return result

    os_name, arch = get_platform_info()
    asset = find_release_asset(release, os_name, arch, artifact_name)
    if asset is None:
        result.asset_available = False
        return _blocked(
            result,
            BlockedReason.NO_ASSET_FOR_PLATFORM,
            ExitCode.NO_ASSET_FOR_PLATFORM,
            f"No {artifact_name} build published for {os_name}-{arch}.",
        )
    result.asset_available = True

    previous = layout.active_version()
    new_version = result.latest_version
    layout.staging_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=layout.staging_dir))

    try:
        # 3. stage
        archive = downloader(asset, staging)
        # 4. verify artifact
        digest = resolve_expected_digest(release, asset["name"])
        outcome = verify_artifact(archive, digest, asset.get("size"))
        result.checksum_verified = outcome.verified
        result.warnings.extend(outcome.warnings)
        payload = safe_extract(archive, staging / "extracted")
        # 5. verify binary
        verify_staged_binary(payload, new_version, layout.executable_name)
    except UpgradeError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        reason = (
            BlockedReason.CHECKSUM_MISMATCH
            if "checksum" in str(exc).lower() or "size" in str(exc).lower()
            else BlockedReason.STAGED_BINARY_FAILED
        )
        return _blocked(result, reason, ExitCode.VERIFICATION_FAILED, str(exc))

    # 6. activate
    target = layout.version_dir(new_version)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(payload), str(target))
    shutil.rmtree(staging, ignore_errors=True)
    layout.activate(new_version)
    layout.ensure_entrypoint()

    # 7. post-check
    try:
        result.warnings.extend(post_check(layout, migrate))
    except UpgradeError as exc:
        if previous:
            layout.activate(previous)
            layout.ensure_entrypoint()
            shutil.rmtree(target, ignore_errors=True)
        result.action_taken = "rolled_back"
        result.blocked_reason = BlockedReason.POST_CHECK_FAILED
        result.next_action = "inspect_logs_then_retry"
        result.exit_code = int(ExitCode.POST_CHECK_FAILED)
        result.active_version = layout.active_version() or ""
        result.previous_version = new_version
        result.warnings.append(str(exc))
        return result

    # 8. settle
    keep = [v for v in (new_version, previous) if v][:RETAIN_VERSIONS]
    layout.prune(keep=keep)

    result.action_taken = "upgraded"
    result.active_version = new_version
    result.previous_version = previous or ""
    return result


def rollback(layout: InstallLayout) -> UpgradeResult:
    """Repoint to the retained previous version (spec §5)."""
    active = layout.active_version()
    candidates = [v for v in layout.installed_versions() if v != active]
    if not candidates:
        raise UpgradeError("Cannot roll back: no previous version is installed")

    target = candidates[0]
    layout.activate(target)
    layout.ensure_entrypoint()
    return UpgradeResult(
        current_version=active or "",
        action_taken="rolled_back",
        active_version=target,
        previous_version=active or "",
    )


def _blocked(
    result: UpgradeResult, reason: str, code: ExitCode, message: str
) -> UpgradeResult:
    result.action_taken = "blocked"
    result.blocked_reason = reason
    result.exit_code = int(code)
    result.warnings.append(message)
    return result
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_upgrade_transaction.py -v`
Expected: PASS — 12 passed.

- [ ] **Step 5: Run the whole upgrade suite together**

Run: `uv run pytest tests/test_upgrade_*.py -v`
Expected: PASS — all upgrade tests green.

- [ ] **Step 6: Commit**

```bash
git add src/services/upgrade/transaction.py tests/test_upgrade_transaction.py
git commit -m "feat: atomic upgrade transaction with verified activation

Everything fallible happens in staging/, so a pre-activation failure leaves
the install byte-identical. Migration failure rolls back and deletes the bad
version; environment-check failures only warn, because rolling back would
not fix a missing Chromium or an expired API key."
```

---

### Task 7: Package API and packaging metadata

Restores importability for `src/cmd/cli.py` and ensures `packaging` survives
PyInstaller (spec §12).

**Files:**
- Modify: `src/services/upgrade/__init__.py`
- Modify: `adm-agent.spec:26-64` (`_COLLECT_PKGS`)
- Test: `tests/test_upgrade_package_api.py`

**Interfaces:**
- Produces the public surface: `UpgradeError`, `UpgradeResult`, `ExitCode`,
  `BlockedReason`, `InstallLayout`, `check_for_updates`, `perform_upgrade`,
  `rollback`, `get_current_version`, `get_platform_info`,
  `default_install_layout()`, `default_pid_file()`, `default_health_url()`.

- [ ] **Step 1: Write the failing test**

`tests/test_upgrade_package_api.py`:

```python
"""The upgrade package's public surface must stay importable."""

from pathlib import Path

import pytest


def test_public_names_are_importable() -> None:
    import src.services.upgrade as upgrade

    for name in (
        "UpgradeError",
        "UpgradeResult",
        "ExitCode",
        "BlockedReason",
        "InstallLayout",
        "check_for_updates",
        "perform_upgrade",
        "rollback",
        "get_current_version",
        "get_platform_info",
    ):
        assert hasattr(upgrade, name), f"{name} missing from the package API"


def test_default_layout_uses_the_frozen_data_dir(tmp_path: Path, monkeypatch) -> None:
    """The install root is the frozen data dir — spec §3.1."""
    from src.services.upgrade import default_install_layout

    monkeypatch.setattr("src.services.upgrade.get_data_dir", lambda: tmp_path)
    assert default_install_layout().root == tmp_path


def test_packaging_is_declared_for_pyinstaller() -> None:
    """packaging drives version comparison; a missing bundle breaks upgrade."""
    spec = Path("adm-agent.spec").read_text()
    assert '"packaging"' in spec
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_upgrade_package_api.py -v`
Expected: FAIL — `AttributeError`/`ImportError` on the re-exports.

- [ ] **Step 3: Write `__init__.py`**

```python
"""Atomic, verified self-upgrade for the packaged backend.

See ``docs/superpowers/specs/2026-08-27-upgrade-reliability-design.md``.
"""
from __future__ import annotations

from pathlib import Path

from src.core.paths import get_data_dir, is_frozen
from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.preflight import PreflightBlock, run_preflight
from src.services.upgrade.release import get_platform_info
from src.services.upgrade.transaction import (
    check_for_updates,
    get_current_version,
    perform_upgrade,
    rollback,
)
from src.services.upgrade.types import (
    BlockedReason,
    ExitCode,
    UnparseableVersionError,
    UpgradeError,
    UpgradeResult,
)

DEFAULT_PORT = 8910


def default_install_layout() -> InstallLayout:
    """Backend layout, rooted at the frozen-mode data dir (spec §3.1)."""
    return InstallLayout(root=get_data_dir(), artifact_name="adm-agent")


def default_client_layout() -> InstallLayout:
    """Client layout, rooted at its existing config home (spec §3.6)."""
    return InstallLayout(
        root=Path.home() / ".adm-agent-client", artifact_name="adm-agent-client"
    )


def default_pid_file() -> Path:
    """PID file written by ``serve`` / ``serve-install``."""
    return Path.home() / ".adm-agent" / "server.pid"


def default_client_pid_file() -> Path:
    """PID file written by ``adm-agent-client start`` / ``start-install``.

    The client is a separate process from the server: a running server must
    not block a client upgrade, and vice versa.
    """
    return Path.home() / ".adm-agent-client" / "client.pid"


def default_health_url(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}/health"


__all__ = [
    "BlockedReason",
    "ExitCode",
    "InstallLayout",
    "PreflightBlock",
    "UnparseableVersionError",
    "UpgradeError",
    "UpgradeResult",
    "check_for_updates",
    "default_client_layout",
    "default_client_pid_file",
    "default_health_url",
    "default_install_layout",
    "default_pid_file",
    "get_current_version",
    "get_platform_info",
    "is_frozen",
    "perform_upgrade",
    "rollback",
    "run_preflight",
]
```

- [ ] **Step 4: Add `packaging` to the PyInstaller collect list**

In `adm-agent.spec`, inside `_COLLECT_PKGS`, add after the `# --- CLI ---`
group:

```python
    # --- Version comparison (upgrade) ---
    "packaging",
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_upgrade_package_api.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/services/upgrade/__init__.py adm-agent.spec tests/test_upgrade_package_api.py
git commit -m "feat: upgrade package public API and PyInstaller collection

packaging is force-collected because version comparison depends on it and a
frozen bundle that silently drops it would resurrect the pinning bug."
```

---

### Task 8: CLI wiring — backend and client

Implements spec §7 and §3.6. Both CLIs must be rewired in one task: Task 1
deleted the module they both import, and this task's final step is the first
point where the whole suite can be green again.

**Files:**
- Modify: `src/cmd/cli.py:56` (import), `:1458-1548` (upgrade command),
  `:1700-1720` (version command), `:1750-1760` (help text)
- Modify: `src/cmd/client_cli.py:29-34` (import), `:77-95`
  (`_print_upgrade_check`), `:295-320` (upgrade command), `:280` (version)
- Test: `tests/test_upgrade_cli.py`

**Interfaces:**
- Consumes: the Task 7 package API (`default_install_layout`,
  `default_client_layout`, `perform_upgrade`, `rollback`, `check_for_updates`).
- Produces: `adm-agent upgrade [--check] [--json] [--rollback] [--force]
  [--migrate/--no-migrate]`, `adm-agent version [--json]`, and the same
  `--json` / `--rollback` / exit-code contract on
  `adm-agent-client upgrade` / `version`.

- [ ] **Step 1: Write the failing test**

`tests/test_upgrade_cli.py`:

```python
"""Tests for the upgrade/version CLI contract — spec §7."""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from src.cmd.cli import app
from src.services.upgrade.types import BlockedReason, ExitCode, UpgradeResult

runner = CliRunner()


# ── version --json ────────────────────────────────────────────────────


def test_version_json_is_machine_readable() -> None:
    """The staged self-check parses this; its shape is API."""
    with patch("src.cmd.cli.get_current_version", return_value="v0.11.0"), patch(
        "src.cmd.cli.get_platform_info", return_value=("macos", "arm64")
    ):
        result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "v0.11.0"
    assert payload["platform"] == "macos-arm64"


def test_version_without_json_stays_human_readable() -> None:
    with patch("src.cmd.cli.get_current_version", return_value="v0.11.0"):
        result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "v0.11.0"


# ── upgrade exit codes ────────────────────────────────────────────────


def _result(**kwargs) -> UpgradeResult:
    base = UpgradeResult(current_version="v0.10.0", latest_version="v0.11.0")
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


def test_upgrade_success_exits_zero() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(action_taken="upgraded", active_version="v0.11.0"),
    ):
        result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 0


def test_upgrade_blocked_by_running_server_exits_10() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(
            action_taken="blocked",
            blocked_reason=BlockedReason.SERVER_RUNNING,
            next_action="stop_server_then_retry",
            exit_code=int(ExitCode.SERVER_RUNNING),
        ),
    ):
        result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 10


def test_upgrade_legacy_layout_exits_15() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(
            action_taken="blocked",
            blocked_reason=BlockedReason.LEGACY_LAYOUT,
            exit_code=int(ExitCode.LEGACY_LAYOUT),
        ),
    ):
        result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 15


def test_upgrade_rolled_back_exits_13() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(
            action_taken="rolled_back",
            blocked_reason=BlockedReason.POST_CHECK_FAILED,
            exit_code=int(ExitCode.POST_CHECK_FAILED),
        ),
    ):
        result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 13


def test_upgrade_json_emits_the_documented_fields() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(action_taken="upgraded", active_version="v0.11.0"),
    ):
        result = runner.invoke(app, ["upgrade", "--json"])
    payload = json.loads(result.stdout)
    for key in (
        "current_version",
        "latest_version",
        "is_newer",
        "asset_available",
        "checksum_verified",
        "action_taken",
        "active_version",
        "previous_version",
        "blocked_reason",
        "next_action",
        "warnings",
    ):
        assert key in payload
    assert "exit_code" not in payload  # internal only


def test_upgrade_check_json_does_not_upgrade() -> None:
    with patch(
        "src.cmd.cli.check_for_updates", return_value=_result(is_newer=True)
    ) as check, patch("src.cmd.cli.perform_upgrade") as perform:
        result = runner.invoke(app, ["upgrade", "--check", "--json"])
    assert result.exit_code == 0
    assert check.called
    assert not perform.called


def test_upgrade_rollback_invokes_rollback_only() -> None:
    with patch(
        "src.cmd.cli.rollback",
        return_value=_result(action_taken="rolled_back", active_version="v0.10.0"),
    ) as rb, patch("src.cmd.cli.perform_upgrade") as perform:
        result = runner.invoke(app, ["upgrade", "--rollback"])
    assert result.exit_code == 0
    assert rb.called
    assert not perform.called
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_upgrade_cli.py -v`
Expected: FAIL — `ImportError` (cli.py still imports the deleted names) and
unknown options `--json` / `--rollback`.

- [ ] **Step 3: Replace the import at `src/cmd/cli.py:56`**

```python
from src.services.upgrade import (
    ExitCode,
    check_for_updates,
    default_health_url,
    default_install_layout,
    default_pid_file,
    get_current_version,
    get_platform_info,
    is_frozen,
    perform_upgrade,
    rollback,
)
```

- [ ] **Step 4: Replace `_print_upgrade_check` / `_perform_upgrade` / `upgrade` (`cli.py:1458-1548`)**

Delete `_run_migration_after_upgrade` and `_run_cli_subcommand` — that logic
now lives in `default_post_check` and runs against the newly activated
version through the stable entry point.

```python
def _emit(result, as_json: bool) -> None:
    """Print a result either as JSON (agents) or prose (humans)."""
    if as_json:
        typer.echo(json.dumps(result.to_json_dict(), ensure_ascii=False, indent=2))
        return

    typer.echo(f"📋 Current version: {result.current_version}")
    if result.latest_version:
        typer.echo(f"📋 Latest version:  {result.latest_version}")

    if result.action_taken == "upgraded":
        typer.echo(f"🎉 Upgraded to {result.active_version}.")
        typer.echo("ℹ️  Restart the server to pick up the new version.")
    elif result.action_taken == "rolled_back":
        typer.echo(f"↩️  Rolled back to {result.active_version}.", err=True)
    elif result.action_taken == "blocked":
        typer.echo("⚠️  Upgrade did not run.", err=True)
    elif result.is_newer:
        typer.echo("🎯 Update available! Run 'upgrade' to install it.")
    else:
        typer.echo("✅ Already on latest version.")

    for warning in result.warnings:
        typer.echo(f"   • {warning}", err=result.exit_code != 0)


@app.command()
def upgrade(
    check_only: bool = typer.Option(False, "--check", help="Only check, don't install"),
    force: bool = typer.Option(False, "--force", help="Install even if not newer"),
    migrate: bool = typer.Option(True, "--migrate/--no-migrate", help="Run DB migration"),
    rollback_to_previous: bool = typer.Option(
        False, "--rollback", help="Return to the previously installed version"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Check for, install, or undo backend updates."""
    _setup_logging(verbose)

    try:
        if rollback_to_previous:
            result = rollback(default_install_layout())
        elif check_only:
            result = check_for_updates()
        else:
            result = perform_upgrade(
                default_install_layout(),
                force=force,
                migrate=migrate,
                frozen=is_frozen(),
                pid_file=default_pid_file(),
                health_url=default_health_url(),
            )
    except Exception as exc:  # noqa: BLE001 - surfaced as exit code 1
        if as_json:
            typer.echo(
                json.dumps({"action_taken": "blocked", "blocked_reason": "unexpected",
                            "warnings": [str(exc)]}, ensure_ascii=False)
            )
        else:
            typer.echo(f"❌ Upgrade failed: {exc}", err=True)
        raise typer.Exit(code=int(ExitCode.UNEXPECTED))

    _emit(result, as_json)
    if result.exit_code != int(ExitCode.OK):
        raise typer.Exit(code=result.exit_code)
```

- [ ] **Step 5: Replace the `version` command (`cli.py:1700-1720`)**

```python
@app.command()
def version(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Detailed info"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Display current version information."""
    try:
        current = get_current_version()
        os_name, arch_name = get_platform_info()

        if as_json:
            typer.echo(
                json.dumps(
                    {
                        "version": current,
                        "platform": f"{os_name}-{arch_name}",
                        "executable": sys.executable,
                    },
                    ensure_ascii=False,
                )
            )
            return

        if verbose:
            typer.echo(f"UniAdmission Agent {current}")
            typer.echo(f"Platform: {os_name}-{arch_name}")
            typer.echo(f"Python: {sys.version}")
            typer.echo(f"Executable: {sys.executable}")
        else:
            typer.echo(current)
    except Exception as e:
        typer.echo(f"❌ Failed to get version: {e}", err=True)
        raise typer.Exit(code=1)
```

Confirm `import json` is already present at the top of `cli.py`; add it if not.

- [ ] **Step 6: Update the `upgrade` help text (`cli.py:1756`)**

```
upgrade:
    --check      Only check for updates, don't install
    --force      Install even when not newer
    --rollback   Return to the previously installed version
    --json       Machine-readable output (for agents)
    --no-migrate Skip the post-upgrade database migration
```

- [ ] **Step 7: Rewire `src/cmd/client_cli.py`**

Replace the import block at `client_cli.py:29-34`:

```python
from src.services.upgrade import (
    ExitCode,
    check_for_updates,
    default_client_layout,
    default_client_pid_file,
    get_current_version,
    get_platform_info,
    is_frozen,
    perform_upgrade,
    rollback,
)
```

Replace the `upgrade` command (`client_cli.py:295-320`) with the same shape
as the backend's, differing only in layout and artifact. `_print_upgrade_check`
at `:77` is deleted — `_emit_client` replaces it.

```python
def _emit_client(result, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(result.to_json_dict(), ensure_ascii=False, indent=2))
        return
    typer.echo(f"📋 Current version: {result.current_version}")
    if result.latest_version:
        typer.echo(f"📋 Latest version:  {result.latest_version}")
    if result.action_taken == "upgraded":
        typer.echo(f"🎉 Upgraded to {result.active_version}.")
    elif result.action_taken == "rolled_back":
        typer.echo(f"↩️  Rolled back to {result.active_version}.", err=True)
    elif result.action_taken == "blocked":
        typer.echo("⚠️  Upgrade did not run.", err=True)
    else:
        typer.echo("✅ Already on latest version.")
    for warning in result.warnings:
        typer.echo(f"   • {warning}", err=result.exit_code != 0)


@app.command()
def upgrade(
    check_only: bool = typer.Option(False, "--check", help="Only check, don't install"),
    force: bool = typer.Option(False, "--force", help="Install even if not newer"),
    rollback_to_previous: bool = typer.Option(
        False, "--rollback", help="Return to the previously installed version"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Check for, install, or undo client updates."""
    try:
        if rollback_to_previous:
            result = rollback(default_client_layout())
        elif check_only:
            result = check_for_updates(artifact_name="adm-agent-client")
        else:
            result = perform_upgrade(
                default_client_layout(),
                artifact_name="adm-agent-client",
                force=force,
                migrate=False,  # the client has no database
                frozen=is_frozen(),
                # The client's own PID file — a running *server* is unrelated
                # to a client upgrade and must not block it.
                pid_file=default_client_pid_file(),
                health_url="",  # no health endpoint; PID liveness is the signal
            )
    except Exception as exc:  # noqa: BLE001 - surfaced as exit code 1
        typer.echo(f"❌ Upgrade failed: {exc}", err=True)
        raise typer.Exit(code=int(ExitCode.UNEXPECTED))

    _emit_client(result, as_json)
    if result.exit_code != int(ExitCode.OK):
        raise typer.Exit(code=result.exit_code)
```

Add `--json` to the client's `version` command the same way as Step 5, using
`get_current_version()` and `get_platform_info()`.

Note the deliberate difference: `migrate=False`. The client has no database,
and `default_post_check` short-circuits for non-backend artifacts anyway
(spec §3.6) — passing it explicitly documents the intent at the call site.

- [ ] **Step 8: Add client CLI tests**

Append to `tests/test_upgrade_cli.py`:

```python
# ── client CLI parity (spec §3.6) ─────────────────────────────────────


def test_client_upgrade_uses_the_client_layout_and_artifact() -> None:
    """The client must not upgrade itself using the backend's root."""
    from src.cmd.client_cli import app as client_app

    with patch(
        "src.cmd.client_cli.perform_upgrade",
        return_value=_result(action_taken="upgraded", active_version="v0.11.0"),
    ) as perform, patch("src.cmd.client_cli.default_client_layout") as layout:
        result = CliRunner().invoke(client_app, ["upgrade"])

    assert result.exit_code == 0
    assert layout.called
    assert perform.call_args.kwargs["artifact_name"] == "adm-agent-client"
    assert perform.call_args.kwargs["migrate"] is False


def test_client_upgrade_blocked_propagates_the_exit_code() -> None:
    from src.cmd.client_cli import app as client_app

    with patch(
        "src.cmd.client_cli.perform_upgrade",
        return_value=_result(
            action_taken="blocked",
            blocked_reason=BlockedReason.LEGACY_LAYOUT,
            exit_code=int(ExitCode.LEGACY_LAYOUT),
        ),
    ), patch("src.cmd.client_cli.default_client_layout"):
        result = CliRunner().invoke(client_app, ["upgrade"])
    assert result.exit_code == 15


def test_client_version_json() -> None:
    from src.cmd.client_cli import app as client_app

    with patch("src.cmd.client_cli.get_current_version", return_value="v0.11.0"), patch(
        "src.cmd.client_cli.get_platform_info", return_value=("linux", "x86_64")
    ):
        result = CliRunner().invoke(client_app, ["version", "--json"])
    assert json.loads(result.stdout)["version"] == "v0.11.0"
```

- [ ] **Step 9: Run the test to verify it passes**

Run: `uv run pytest tests/test_upgrade_cli.py -v`
Expected: PASS — 13 passed.

- [ ] **Step 10: Run the full suite and lint**

This is the first point since Task 1 where the whole suite can be green.

Run: `uv run pytest -q` — expected: all pass (1082 baseline + new tests).
Run: `uv run pylint src/ scripts/` — expected: 10.00/10.

- [ ] **Step 11: Commit**

```bash
git add src/cmd/cli.py src/cmd/client_cli.py tests/test_upgrade_cli.py
git commit -m "feat: stable JSON and exit-code contract for upgrade

The primary caller is an agent reading the shipped skills, so it routes on
exit codes and parsed fields instead of emoji prose. Adds --rollback as the
user-facing escape hatch and version --json for the staged self-check.

adm-agent-client gets the same contract and the same atomic mechanism rather
than a second, weaker upgrade path — it was pinned by the identical string
comparison bug."
```

---

### Task 9: Skill and documentation convergence

Implements spec §3.4, §3.5, §8. This is what stops the agent from executing
the unverified re-download path.

**Files:**
- Modify: `skills/uni-admission-install/SKILL.md` §1.4, §1.5, §3
- Modify: `README.md` (Windows notes, upgrade command table row)
- Modify: `RELEASING.md` (layout note)
- Test: `tests/test_upgrade_skill_docs.py`

**Interfaces:**
- Consumes: the CLI contract from Task 8.
- Produces: no code interfaces; asserted by documentation tests.

- [ ] **Step 1: Write the failing test**

`tests/test_upgrade_skill_docs.py`:

```python
"""The install skill must route through `upgrade`, not re-download — spec §8."""

from pathlib import Path

import pytest

SKILL = Path("skills/uni-admission-install/SKILL.md")


@pytest.fixture(name="skill_text")
def _skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_upgrade_section_calls_the_upgrade_command(skill_text: str) -> None:
    upgrade_section = skill_text.split("## §3")[1].split("## §4")[0]
    assert "adm-agent upgrade" in upgrade_section


def test_upgrade_section_no_longer_reruns_fresh_install(skill_text: str) -> None:
    """The re-download path had no backup, atomicity or verification."""
    upgrade_section = skill_text.split("## §3")[1].split("## §4")[0]
    assert "Run §1 (Fresh install)" not in upgrade_section


def test_skill_documents_the_exit_codes_the_agent_routes_on(skill_text: str) -> None:
    for code in ("10", "12", "13", "15"):
        assert f"| `{code}` |" in skill_text


def test_skill_documents_rollback(skill_text: str) -> None:
    assert "--rollback" in skill_text


def test_fresh_install_creates_the_versioned_layout(skill_text: str) -> None:
    assert "versions/" in skill_text
    assert "--strip-components=1 -C ~/.uni-agent/bin" not in skill_text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_upgrade_skill_docs.py -v`
Expected: FAIL — the skill still says "Run §1 (Fresh install)".

- [ ] **Step 3: Rewrite install skill §1.4 and §1.5**

Replace the extraction and symlink steps with the versioned layout:

````markdown
### 1.4 Download + extract into a versioned directory

```bash
mkdir -p ~/.uni-agent/versions/${VERSION} ~/.uni-agent/bin
cd /tmp
ARTIFACT="adm-agent-${VERSION}-${OS}-${ARCH}.${EXT}"
curl -fL -o "$ARTIFACT" \
  "https://github.com/dlfkid/uni-admission-agent/releases/download/${VERSION}/${ARTIFACT}"

case "$EXT" in
  tar.gz) tar -xzf "$ARTIFACT" -C ~/.uni-agent/versions/${VERSION} --strip-components=1 ;;
  zip)    unzip -o "$ARTIFACT" -d ~/.uni-agent/versions/${VERSION} ;;
esac

chmod +x ~/.uni-agent/versions/${VERSION}/adm-agent
xattr -dr com.apple.quarantine ~/.uni-agent/versions/${VERSION} 2>/dev/null || true
```

### 1.5 Point the install at it, then onto PATH

```bash
# The pointer the entry point resolves through.
ln -sfn versions/${VERSION} ~/.uni-agent/current
ln -sfn ../current/adm-agent ~/.uni-agent/bin/adm-agent

mkdir -p ~/.local/bin
ln -sf ~/.uni-agent/bin/adm-agent ~/.local/bin/adm-agent
```

On Windows there is no symlink; write the pointer file and the shim instead,
and the command users type is `adm-agent` (PATHEXT resolves `adm-agent.cmd`):

```cmd
echo %VERSION%> %USERPROFILE%\.uni-agent\current.txt
```

Data and configuration live in `~/.uni-agent/` alongside `versions/` and are
never touched by installs or upgrades: `.env`, `admission.db`, `schemas/`.
````

- [ ] **Step 4: Rewrite install skill §3**

````markdown
## §3 Upgrade in place

Do **not** re-run the fresh install to upgrade. `adm-agent upgrade` is
atomic, verified and reversible; re-downloading over a live install is none
of those things.

```bash
adm-agent upgrade --json
```

Route on the exit code — never parse the prose:

| Code | Meaning | What to do |
|---|---|---|
| `0` | Upgraded, or already current | Report the version; offer to restart the server. |
| `10` | Server is running | Stop it (Ctrl-C in the user's terminal, or `adm-agent serve-stop`), then re-run. |
| `11` | No build for this platform | Tell the user; offer the GitHub releases page. |
| `12` | Verification failed, nothing changed | Report it. The install is untouched — retrying is safe. |
| `13` | Upgraded then rolled back | The user is back on the working version. Show `warnings`; do not retry blindly. |
| `14` | Source checkout | Update with `git pull` + `uv sync` instead. |
| `15` | Legacy layout | One-time migration: run §1 once. `.env` and the database are preserved — say so. |

If anything looks wrong after an upgrade:

```bash
adm-agent upgrade --rollback
```

That returns the user to the previous version, which is still on disk. Data
and configuration are never modified by either direction.
````

- [ ] **Step 5: Update README.md and RELEASING.md**

In README's platform notes, replace the Windows `.exe` guidance:

```markdown
- **Windows:** first run may trigger SmartScreen; choose **More info → Run
  anyway**. The command is `adm-agent` (the installer writes a
  `adm-agent.cmd` launcher that resolves the active version).
```

In the CLI table, replace the `upgrade` row:

```markdown
| `uni-admission upgrade [--check \| --force \| --rollback \| --json]` | Update the backend, or return to the previous version. Atomic: a failed upgrade leaves the install unchanged. |
```

In RELEASING.md, add under **Notes**:

```markdown
- Publishing is gated on the upgrade verification job (`upgrade-verify` in
  `release.yml`). If it fails, no GitHub Release is created and no artifacts
  are uploaded — the tag exists but nothing is downloadable. Fix and cut the
  next patch tag.
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_upgrade_skill_docs.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 7: Commit**

```bash
git add skills/uni-admission-install/SKILL.md README.md RELEASING.md tests/test_upgrade_skill_docs.py
git commit -m "docs: route the agent through 'upgrade' instead of re-downloading

The install skill previously told the agent to re-run the fresh install for
upgrades — no backup, no atomicity, no verification, no migration. It now
calls adm-agent upgrade and routes on exit codes, with fresh install kept as
the reinstall / legacy-layout migration path."
```

---

### Task 10: Release gate in CI

Implements spec §6.1 and §11. Publication becomes conditional on a real
three-platform upgrade succeeding.

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `scripts/verify_upgrade.py`

**Interfaces:**
- Consumes: the CLI contract (Task 8) and `ADM_AGENT_RELEASE_API_BASE`
  (Task 3).
- Produces: an `upgrade-verify` job that `release` depends on.

- [ ] **Step 1: Write the verification script**

`scripts/verify_upgrade.py` — serves a fake release over HTTP, installs the
built artifact into the §3.2 layout, upgrades into it, and rolls back.

```python
#!/usr/bin/env python3
"""End-to-end upgrade verification for the release gate (spec §11).

Serves the just-built artifact as a fake GitHub release, installs it into a
throwaway root, then exercises upgrade and rollback against it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = "dlfkid/uni-admission-agent"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _serve(directory: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _build_fake_release(serve_dir: Path, artifact: Path, tag: str, base: str) -> None:
    """Lay out /repos/<repo>/releases/latest and the asset next to it."""
    shutil.copy2(artifact, serve_dir / artifact.name)
    checksums = serve_dir / "SHA256SUMS"
    checksums.write_text(f"{_sha256(artifact)}  {artifact.name}\n")

    release = {
        "tag_name": tag,
        "html_url": f"{base}/release",
        "assets": [
            {
                "name": artifact.name,
                "browser_download_url": f"{base}/{artifact.name}",
                "size": artifact.stat().st_size,
            },
            {
                "name": "SHA256SUMS",
                "browser_download_url": f"{base}/SHA256SUMS",
                "size": checksums.stat().st_size,
            },
        ],
    }
    target = serve_dir / "repos" / REPO / "releases"
    target.mkdir(parents=True, exist_ok=True)
    (target / "latest").write_text(json.dumps(release))


def _install(artifact: Path, root: Path, version: str, windows: bool) -> Path:
    vdir = root / "versions" / version
    vdir.mkdir(parents=True)
    if artifact.suffix == ".zip":
        with zipfile.ZipFile(artifact) as zf:
            zf.extractall(vdir.parent / "_x")
    else:
        with tarfile.open(artifact, "r:gz") as tf:
            tf.extractall(vdir.parent / "_x", filter="data")
    top = next((vdir.parent / "_x").iterdir())
    for item in top.iterdir():
        shutil.move(str(item), str(vdir / item.name))
    shutil.rmtree(vdir.parent / "_x")

    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    if windows:
        (root / "current.txt").write_text(version)
        (bin_dir / "adm-agent.cmd").write_text(
            "@echo off\r\nsetlocal\r\n"
            'set /p ADM_VERSION=<"%~dp0..\\current.txt"\r\n'
            '"%~dp0..\\versions\\%ADM_VERSION%\\adm-agent.exe" %*\r\n'
        )
        return bin_dir / "adm-agent.cmd"

    (root / "current").symlink_to(Path("versions") / version, target_is_directory=True)
    (bin_dir / "adm-agent").symlink_to(Path("..") / "current" / "adm-agent")
    (vdir / "adm-agent").chmod(0o755)
    return bin_dir / "adm-agent"


def _run(entry: Path, args: list[str], env_base: str, home: Path) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    env["ADM_AGENT_RELEASE_API_BASE"] = f"{env_base}/repos"
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        [str(entry), *args], capture_output=True, text=True, env=env, check=False
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--new-version", required=True)
    parser.add_argument("--old-version", default="v0.0.1")
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()

    windows = sys.platform == "win32"
    home = args.workdir / "home"
    root = home / ".uni-agent"
    root.mkdir(parents=True)
    serve_dir = args.workdir / "serve"
    serve_dir.mkdir(parents=True)

    # Seed user data; it must survive everything below.
    (root / ".env").write_text("DEEPSEEK_API_KEY=verify\n")
    (root / "admission.db").write_bytes(b"SQLite format 3\x00")

    server, base = _serve(serve_dir)
    try:
        _build_fake_release(serve_dir, args.artifact, args.new_version, base)
        entry = _install(args.artifact, root, args.old_version, windows)

        upgraded = _run(entry, ["upgrade", "--force", "--no-migrate", "--json"], base, home)
        print(upgraded.stdout, upgraded.stderr)
        if upgraded.returncode != 0:
            print(f"FAIL: upgrade exited {upgraded.returncode}")
            return 1
        payload = json.loads(upgraded.stdout)
        if payload["active_version"] != args.new_version:
            print(f"FAIL: active_version={payload['active_version']}")
            return 1

        reported = _run(entry, ["version", "--json"], base, home)
        if json.loads(reported.stdout)["version"] != args.new_version:
            print(f"FAIL: entry point reports {reported.stdout}")
            return 1

        back = _run(entry, ["upgrade", "--rollback", "--json"], base, home)
        if back.returncode != 0 or json.loads(back.stdout)["active_version"] != args.old_version:
            print(f"FAIL: rollback -> {back.stdout} {back.stderr}")
            return 1

        if (root / ".env").read_text() != "DEEPSEEK_API_KEY=verify\n":
            print("FAIL: .env was modified")
            return 1
        if (root / "admission.db").read_bytes() != b"SQLite format 3\x00":
            print("FAIL: admission.db was modified")
            return 1

        print("OK: upgrade, entry point, rollback and data safety all verified")
        return 0
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Add `SHA256SUMS` generation to the release job**

In `.github/workflows/release.yml`, in the `release` job after the download
steps and before "Create Release":

```yaml
      - name: Generate SHA256SUMS
        run: |
          cd artifacts
          sha256sum * > SHA256SUMS
          cat SHA256SUMS
```

- [ ] **Step 3: Add the `upgrade-verify` job**

Insert before the `release` job:

```yaml
  upgrade-verify:
    name: Verify upgrade on ${{ matrix.os }}
    needs: [build-backend]
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [windows-latest, macos-latest, ubuntu-latest]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Download the backend artifact built for this platform
        uses: actions/download-artifact@v4
        with:
          name: backend-${{ matrix.os }}
          path: artifacts/

      - name: Verify upgrade and rollback end to end
        shell: bash
        run: |
          ARTIFACT=$(ls artifacts/adm-agent-v* | head -1)
          # The version MUST come from the artifact filename, not GITHUB_REF_NAME:
          # find_release_asset builds the expected filename from the release tag,
          # so a mismatch makes the asset lookup fail rather than testing anything.
          BASENAME=$(basename "$ARTIFACT")
          NEW_VERSION=$(echo "$BASENAME" | sed -E 's/^adm-agent-(v[^-]+)-.*/\1/')
          echo "Verifying $BASENAME as $NEW_VERSION"
          python scripts/verify_upgrade.py \
            --artifact "$ARTIFACT" \
            --new-version "$NEW_VERSION" \
            --workdir "${RUNNER_TEMP}/upgrade-verify"
```

- [ ] **Step 4: Gate publication on it**

Change the `release` job's dependencies:

```yaml
  release:
    name: Create GitHub Release
    needs: [build-extension, build-backend, build-client, upgrade-verify]
```

A tag existing and a release being published are now separate events: if
`upgrade-verify` fails, no release is created and no artifacts upload.

- [ ] **Step 5: Validate the workflow locally**

Run: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml')); print('workflow parses')"`
Expected: `workflow parses`

Run: `uv run pylint scripts/verify_upgrade.py`
Expected: 10.00/10

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml scripts/verify_upgrade.py
git commit -m "ci: gate release publication on a real upgrade succeeding

Builds are verified by installing the artifact, upgrading into it from a
locally served fake release, rolling back, and asserting .env and the
database are untouched — on all three platforms. The release job now needs
upgrade-verify, so an unproven upgrade path is undownloadable rather than
merely flagged."
```

---

### Task 11: Integration test and final verification

Covers the one thing fixtures cannot catch (spec §10) and confirms the whole
change is green.

**Files:**
- Create: `tests/test_upgrade_integration.py`

**Interfaces:**
- Consumes: `release.py` from Task 3.

- [ ] **Step 1: Write the integration test**

`tests/test_upgrade_integration.py`:

```python
"""Integration checks against the real release API — excluded by default.

Run with: uv run pytest tests/test_upgrade_integration.py -m integration
"""

import pytest

from src.services.upgrade.release import (
    fetch_latest_release,
    find_release_asset,
    get_platform_info,
)
from src.services.upgrade.versions import parse_tag


@pytest.mark.integration
def test_published_assets_still_match_the_expected_naming() -> None:
    """A rename in release.yml would silently break every user's upgrade.

    No fixture can catch this — only the real release can.
    """
    release = fetch_latest_release()
    assert parse_tag(release["tag_name"]) is not None

    for os_name, arch in (("macos", "arm64"), ("linux", "x86_64"), ("windows", "x86_64")):
        assert find_release_asset(release, os_name, arch) is not None, (
            f"No adm-agent asset published for {os_name}-{arch} in "
            f"{release['tag_name']}"
        )


@pytest.mark.integration
def test_current_platform_has_a_downloadable_asset() -> None:
    os_name, arch = get_platform_info()
    release = fetch_latest_release()
    asset = find_release_asset(release, os_name, arch)
    assert asset is not None
    assert asset["size"] > 0
```

- [ ] **Step 2: Verify it is excluded from the default run**

Run: `uv run pytest tests/test_upgrade_integration.py -q`
Expected: `2 deselected` (the `-m 'not integration'` default in
`pyproject.toml` excludes it).

- [ ] **Step 3: Run it explicitly against the real API**

Run: `uv run pytest tests/test_upgrade_integration.py -m integration -v`
Expected: PASS (requires network). If it fails, the published asset names no
longer match `find_release_asset` — fix before releasing.

- [ ] **Step 4: Full verification**

Run: `uv run pytest -q`
Expected: all pass, no failures, integration deselected.

Run: `uv run pytest --cov=src/services/upgrade --cov-report=term-missing`
Expected: coverage for `src/services/upgrade/` well above the 17% the old
module had; every module in the package covered.

Run: `uv run pylint src/ scripts/`
Expected: 10.00/10

- [ ] **Step 5: Commit**

```bash
git add tests/test_upgrade_integration.py
git commit -m "test: assert published asset naming against the real release API

Marked integration so the default suite stays hermetic. This is the one
failure mode a fixture cannot reproduce: artifact names changing in
release.yml while every installed client still expects the old pattern."
```

---

## Post-implementation checklist

Before opening the MR:

- [ ] `uv run pytest -q` — all green
- [ ] `uv run pylint src/ scripts/` — 10.00/10
- [ ] `uv run pytest --cov=src/services/upgrade --cov-report=term` — no
      module in the package below 80%
- [ ] Manual smoke on the host platform: `uv run python -m src.cmd.cli upgrade --json`
      returns exit `14` (`not_frozen`) with a clear message
- [ ] Spec §3.5 acknowledged in the MR description: **existing users need one
      re-install to migrate onto the versioned layout**, and the install
      skill's §1 is what performs it
