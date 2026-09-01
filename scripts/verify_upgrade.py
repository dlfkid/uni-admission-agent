#!/usr/bin/env python3
"""End-to-end upgrade verification for the release gate (spec §11).

Two legs, both run against the exact binary that would ship:

* **step 2 — previous real release.** Download the currently published
  ``latest`` backend artifact, install it in its own native layout, and
  assert the upgrade a real user will actually perform. For the transition
  release the previous version has the flat legacy layout, so this leg
  asserts the spec §3.5 detection path: exit ``15``,
  ``blocked_reason="legacy_layout"``, install untouched. Skipped with an
  explicit log line when no previous release exists.
* **step 3 — local fake release.** Serve the just-built artifact as a fake
  GitHub release, install it into a throwaway root, then exercise the full
  happy path (stage → verify → activate → post-check) and rollback.
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


def _extract_flat(artifact: Path, dest: Path) -> None:
    """Extract *artifact* into *dest*, stripping its single top-level dir.

    ``build_dist.py`` archives with ``base_dir=<base_name>``, so every
    artifact wraps its payload in one directory. Both the versioned layout
    and the legacy flat layout expect that wrapper gone.
    """
    dest.mkdir(parents=True, exist_ok=True)
    scratch = dest.parent / f"_x-{dest.name}"
    if scratch.exists():
        shutil.rmtree(scratch)
    if artifact.name.endswith(".zip"):
        with zipfile.ZipFile(artifact) as zf:
            zf.extractall(scratch)
    else:
        with tarfile.open(artifact, "r:gz") as tf:
            tf.extractall(scratch, filter="data")
    tops = [p for p in scratch.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise SystemExit(
            f"Unexpected archive structure in {artifact.name}: expected one "
            f"top-level directory, found {len(tops)}"
        )
    top = tops[0]
    for item in top.iterdir():
        shutil.move(str(item), str(dest / item.name))
    shutil.rmtree(scratch)


def _install(
    artifact: Path, root: Path, version: str, windows: bool, artifact_name: str
) -> None:
    """Place *artifact* at ``versions/<version>`` and wire up the entry point.

    Mirrors what a real installer would produce (spec §3.2), so the gate
    then drives the CLI's own upgrade/rollback machinery against a layout
    it would actually recognise — not a shape invented just for the test.
    """
    vdir = root / "versions" / version
    _extract_flat(artifact, vdir)

    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    if windows:
        (root / _WINDOWS_POINTER).write_text(version)
        # Use the real shim template from layout.py rather than a copy of it,
        # so the gate exercises the exact layout ensure_entrypoint() produces.
        (bin_dir / f"{artifact_name}.cmd").write_text(
            _CMD_SHIM.format(pointer=_WINDOWS_POINTER, exe=f"{artifact_name}.exe")
        )
        return

    (root / "current").symlink_to(Path("versions") / version, target_is_directory=True)
    (bin_dir / artifact_name).symlink_to(Path("..") / "current" / artifact_name)
    (vdir / artifact_name).chmod(0o755)


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


def _migrate_flag(layout: InstallLayout) -> list[str]:
    """``--no-migrate`` where it exists, nothing where it does not.

    Only the backend has a database, so only its CLI declares the flag.
    Passing it to ``adm-agent-client upgrade`` makes Typer exit 2 with
    "No such option", which would fail the gate — and therefore block
    every release — before a single assertion ran.
    """
    return ["--no-migrate"] if layout.artifact_name == "adm-agent" else []


def _step_upgrade(
    layout: InstallLayout, args: argparse.Namespace, base: str, home: Path
) -> str | None:
    """Run ``upgrade --force`` and confirm it activated the new version."""
    upgraded = _run(layout, ["upgrade", "--force", *_migrate_flag(layout), "--json"], base, home)
    print(upgraded.stdout, upgraded.stderr)
    if upgraded.returncode != 0:
        return f"upgrade exited {upgraded.returncode}"
    payload, err = _load_json(upgraded.stdout)
    if err is not None:
        return err
    if payload.get("active_version") != args.new_version:
        return f"active_version={payload.get('active_version')!r} in {upgraded.stdout!r}"
    return None


def _step_entrypoint(
    layout: InstallLayout, args: argparse.Namespace, base: str, home: Path
) -> str | None:
    """Confirm the stable entry point itself now reports the new version."""
    reported = _run(layout, ["version", "--json"], base, home)
    payload, err = _load_json(reported.stdout)
    if err is not None:
        return err
    if payload.get("version") != args.new_version:
        return f"entry point reports {reported.stdout}"
    return None


def _step_rollback(
    layout: InstallLayout, args: argparse.Namespace, base: str, home: Path
) -> str | None:
    """Run ``upgrade --rollback`` and confirm it returned to the old version."""
    back = _run(layout, ["upgrade", "--rollback", *_migrate_flag(layout), "--json"], base, home)
    if back.returncode != 0:
        return f"rollback exited {back.returncode}: {back.stdout} {back.stderr}"
    payload, err = _load_json(back.stdout)
    if err is not None:
        return err
    if payload.get("active_version") != args.old_version:
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


# ── step 2: the previous real release (spec §11 step 2) ───────────────


def _fingerprint(tree: Path) -> dict[str, tuple[int, str]]:
    """Size + digest of every file under *tree*, for an untouched assertion."""
    return {
        str(p.relative_to(tree)): (p.stat().st_size, _sha256(p))
        for p in sorted(tree.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def _seed_user_data(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".env").write_text("DEEPSEEK_API_KEY=verify\n")
    (root / "admission.db").write_bytes(b"SQLite format 3\x00")


def _verify_legacy_refusal(
    new_exe: Path, root: Path, home: Path, base: str
) -> str | None:
    """The transition release: a real legacy install must be refused, intact.

    The *new* binary is run from outside the install root, so "install
    untouched" can be asserted byte-for-byte. This is the spec §3.5
    detection path — the shell-level branch the install skill performs
    before ever reaching a binary is covered by ``test_upgrade_skill_docs``.
    """
    before = _fingerprint(root / "bin")
    env = os.environ.copy()
    env["ADM_AGENT_RELEASE_API_BASE"] = f"{base}/repos"
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    proc = subprocess.run(
        [str(new_exe), "upgrade", "--json"],
        capture_output=True, text=True, env=env, check=False,
    )
    print(proc.stdout, proc.stderr)
    if proc.returncode != 15:
        return f"legacy install: expected exit 15, got {proc.returncode}"
    payload, err = _load_json(proc.stdout)
    if err is not None:
        return err
    if payload.get("blocked_reason") != "legacy_layout":
        return f"legacy install: blocked_reason={payload.get('blocked_reason')!r}"
    if _fingerprint(root / "bin") != before:
        return "legacy install: the install was modified despite the refusal"
    return _data_intact(root)


def _unpack_runnable(artifact: Path, dest: Path, artifact_name: str) -> Path:
    """Extract *artifact* to *dest* and return its executable, made runnable.

    The executable name follows the artifact: the client archive ships
    ``adm-agent-client``, and looking for ``adm-agent`` inside it fails with
    FileNotFoundError before any assertion runs.
    """
    _extract_flat(artifact, dest)
    suffix = ".exe" if sys.platform == "win32" else ""
    exe = dest / f"{artifact_name}{suffix}"
    if sys.platform != "win32":
        exe.chmod(0o755)
    if sys.platform == "darwin":
        subprocess.run(["xattr", "-cr", str(dest)], check=False)
    return exe


def _previous_ships_the_versioned_layout(prev_exe: Path) -> bool:
    """Does the published previous release predate the versioned layout?

    Decided by *capability*, not by tag: ``version --json`` (spec §6.2)
    exists only from the transition release onward, and it is exactly the
    mechanism the new upgrade's staged self-check depends on. Every artifact
    contains ``_internal`` (PyInstaller onedir), so archive contents cannot
    tell the two eras apart — only the binary's own behaviour can.
    """
    try:
        proc = subprocess.run(
            [str(prev_exe), "version", "--json"],
            capture_output=True, text=True, check=False, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    payload, err = _load_json(proc.stdout)
    return err is None and "version" in payload


def _step_previous_release(args: argparse.Namespace, base: str) -> str | None:
    """Upgrade from the currently published release into this build."""
    if args.previous_artifact is None:
        print(
            "SKIP: no previously published release to upgrade from — "
            "skipping the previous-real-release leg (spec §11 step 2)"
        )
        return None

    windows = sys.platform == "win32"
    home = args.workdir / "home-previous"
    root = home / (
        ".uni-agent" if args.artifact_name == "adm-agent" else ".adm-agent-client"
    )
    _seed_user_data(root)

    prev_exe = _unpack_runnable(
        args.previous_artifact, args.workdir / "prevbin", args.artifact_name
    )
    versioned = _previous_ships_the_versioned_layout(prev_exe)

    if versioned:
        # Post-transition: the published release already has the §3.2 layout,
        # so this leg is the real published → new happy path.
        print(f"Previous release {args.previous_version} has the versioned layout")
        layout = InstallLayout(
            root=root, artifact_name=args.artifact_name, windows=windows
        )
        _install(
            args.previous_artifact,
            root,
            args.previous_version,
            windows,
            args.artifact_name,
        )
        leg_args = argparse.Namespace(
            new_version=args.new_version, old_version=args.previous_version
        )
        return _verify(layout, root, leg_args, base, home)

    # Transition release: reproduce the flat layout the old installer built
    # (`bin/adm-agent` + `bin/_internal`, spec §3.1) and assert §3.5 detection.
    print(f"Previous release {args.previous_version} has the flat legacy layout")
    _extract_flat(args.previous_artifact, root / "bin")
    new_exe = _unpack_runnable(
        args.artifact, args.workdir / "newbin", args.artifact_name
    )
    return _verify_legacy_refusal(new_exe, root, home, base)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--new-version", required=True)
    parser.add_argument("--old-version", default="v0.0.1")
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument(
        "--previous-artifact",
        type=Path,
        default=None,
        help="The currently published latest artifact (spec §11 step 2). "
             "Omit when no previous release exists; the leg is then skipped "
             "with an explicit log line.",
    )
    parser.add_argument("--previous-version", default="")
    parser.add_argument(
        "--artifact-name",
        default="adm-agent",
        choices=["adm-agent", "adm-agent-client"],
        help="Which packaged artifact to verify. Spec §2 requires full "
             "parity for both, so the gate runs once per artifact.",
    )
    args = parser.parse_args()

    if args.previous_artifact is not None and not args.previous_artifact.is_file():
        print(f"FAIL: --previous-artifact {args.previous_artifact} does not exist")
        return 1

    windows = sys.platform == "win32"
    home = args.workdir / "home"
    root = home / (
        ".uni-agent" if args.artifact_name == "adm-agent" else ".adm-agent-client"
    )
    serve_dir = args.workdir / "serve"
    serve_dir.mkdir(parents=True)
    layout = InstallLayout(
        root=root, artifact_name=args.artifact_name, windows=windows
    )

    # Seed user data; it must survive everything below.
    _seed_user_data(root)

    server, base = _serve(serve_dir)
    try:
        _build_fake_release(serve_dir, args.artifact, args.new_version, base)

        error = _step_previous_release(args, base)
        if error is not None:
            print(f"FAIL: {error}")
            return 1

        _install(args.artifact, root, args.old_version, windows, args.artifact_name)
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
