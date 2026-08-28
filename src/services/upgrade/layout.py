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
