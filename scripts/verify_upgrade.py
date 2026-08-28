#!/usr/bin/env python3
"""End-to-end upgrade verification for the release gate (spec §11).

Serves the just-built artifact as a fake GitHub release, installs it into a
throwaway root, then exercises upgrade and rollback against it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.services.upgrade.layout import _CMD_SHIM, _WINDOWS_POINTER, InstallLayout

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


def _install(artifact: Path, root: Path, version: str, windows: bool) -> None:
    """Place *artifact* at ``versions/<version>`` and wire up the entry point.

    Mirrors what a real installer would produce (spec §3.2), so the gate
    then drives the CLI's own upgrade/rollback machinery against a layout
    it would actually recognise — not a shape invented just for the test.
    """
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
        (root / _WINDOWS_POINTER).write_text(version)
        # Use the real shim template from layout.py rather than a copy of it,
        # so the gate exercises the exact layout ensure_entrypoint() produces.
        (bin_dir / "adm-agent.cmd").write_text(
            _CMD_SHIM.format(pointer=_WINDOWS_POINTER, exe="adm-agent.exe")
        )
        return

    (root / "current").symlink_to(Path("versions") / version, target_is_directory=True)
    (bin_dir / "adm-agent").symlink_to(Path("..") / "current" / "adm-agent")
    (vdir / "adm-agent").chmod(0o755)


def _run(
    layout: InstallLayout, args: list[str], env_base: str, home: Path
) -> subprocess.CompletedProcess:
    """Invoke the entry point via :meth:`InstallLayout.spawn_argv`.

    Not ``[str(layout.entrypoint_path), *args]``: on Windows the entry point
    is a ``.cmd`` shim, and ``subprocess.run`` with ``shell=False`` hands the
    path straight to ``CreateProcess``, which can only load a PE image — a
    batch file is not one, and this raises ``OSError: [WinError 193] %1 is
    not a valid Win32 application`` before any assertion below even runs.
    ``spawn_argv`` is the one place (shared with the production post-check
    in ``transaction.py``) that knows to route a ``.cmd`` through
    ``cmd.exe /c`` instead.
    """
    env = os.environ.copy()
    env["ADM_AGENT_RELEASE_API_BASE"] = f"{env_base}/repos"
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        layout.spawn_argv(*args), capture_output=True, text=True, env=env, check=False
    )


def _load_json(stdout: str) -> tuple[dict | None, str | None]:
    """Parse *stdout* as JSON. Returns ``(value, None)`` or ``(None, message)``.

    A non-JSON stdout — the exact class of bug the pymupdf4llm stdout-
    pollution fix (previous commit) addressed — must fail with a readable
    ``FAIL:`` line, not an uncaught ``JSONDecodeError`` traceback.
    """
    try:
        return json.loads(stdout), None
    except json.JSONDecodeError:
        return None, f"unparseable output: {stdout!r}"


def _data_intact(root: Path) -> str | None:
    """Confirm the seeded ``.env`` and database survived byte-identically."""
    if (root / ".env").read_text() != "DEEPSEEK_API_KEY=verify\n":
        return ".env was modified"
    if (root / "admission.db").read_bytes() != b"SQLite format 3\x00":
        return "admission.db was modified"
    return None


def _step_upgrade(
    layout: InstallLayout, args: argparse.Namespace, base: str, home: Path
) -> str | None:
    """Run ``upgrade --force`` and confirm it activated the new version."""
    upgraded = _run(layout, ["upgrade", "--force", "--no-migrate", "--json"], base, home)
    print(upgraded.stdout, upgraded.stderr)
    if upgraded.returncode != 0:
        return f"upgrade exited {upgraded.returncode}"
    payload, err = _load_json(upgraded.stdout)
    if err is not None:
        return err
    if payload["active_version"] != args.new_version:
        return f"active_version={payload['active_version']}"
    return None


def _step_entrypoint(
    layout: InstallLayout, args: argparse.Namespace, base: str, home: Path
) -> str | None:
    """Confirm the stable entry point itself now reports the new version."""
    reported = _run(layout, ["version", "--json"], base, home)
    payload, err = _load_json(reported.stdout)
    if err is not None:
        return err
    if payload["version"] != args.new_version:
        return f"entry point reports {reported.stdout}"
    return None


def _step_rollback(
    layout: InstallLayout, args: argparse.Namespace, base: str, home: Path
) -> str | None:
    """Run ``upgrade --rollback`` and confirm it returned to the old version."""
    back = _run(layout, ["upgrade", "--rollback", "--json"], base, home)
    if back.returncode != 0:
        return f"rollback exited {back.returncode}: {back.stdout} {back.stderr}"
    payload, err = _load_json(back.stdout)
    if err is not None:
        return err
    if payload["active_version"] != args.old_version:
        return f"rollback -> {back.stdout} {back.stderr}"
    return None


def _verify(
    layout: InstallLayout, root: Path, args: argparse.Namespace, base: str, home: Path
) -> str | None:
    """Run upgrade, entry-point and rollback checks, then data safety.

    Returns a failure description, or ``None`` when everything checks out.
    Delegates each stage to its own function purely to keep every return
    count low enough for pylint's too-many-returns check while still
    failing loudly and distinctly on every kind of mismatch.
    """
    for step in (_step_upgrade, _step_entrypoint, _step_rollback):
        error = step(layout, args, base, home)
        if error is not None:
            return error
    return _data_intact(root)


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
    layout = InstallLayout(root=root, artifact_name="adm-agent", windows=windows)

    # Seed user data; it must survive everything below.
    (root / ".env").write_text("DEEPSEEK_API_KEY=verify\n")
    (root / "admission.db").write_bytes(b"SQLite format 3\x00")

    server, base = _serve(serve_dir)
    try:
        _build_fake_release(serve_dir, args.artifact, args.new_version, base)
        _install(args.artifact, root, args.old_version, windows)

        error = _verify(layout, root, args, base, home)
        if error is not None:
            print(f"FAIL: {error}")
            return 1

        print("OK: upgrade, entry point, rollback and data safety all verified")
        return 0
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
