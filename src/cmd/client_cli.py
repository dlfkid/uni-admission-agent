#!/usr/bin/env python3
"""CLI for user-side `adm-agent-client` runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
from pathlib import Path
import signal
import subprocess
import sys

import typer

from src.client.bootstrap_prompt import build_bootstrap_prompt
from src.client.config import (
    ClientConfig,
    ClientPolicyProfile,
    ensure_client_id,
    get_client_home,
    load_client_config,
    save_client_config,
)
from src.client.native_browser import fetch_browser_payload
from src.client.runtime import ClientRuntime
from src.services.upgrade import (
    check_for_updates_for_artifact,
    get_current_version,
    get_platform_info,
    upgrade_artifact,
)


app = typer.Typer(
    name="adm-agent-client",
    help="adm-agent-client — connect local browser automation client to serve",
    add_completion=False,
)


def _default_client_name() -> str:
    name = platform.node().strip()
    return name or "adm-agent-client"


def _client_pid_path() -> Path:
    return get_client_home() / "client.pid"


def _write_client_pid_file() -> None:
    pid_file = _client_pid_path()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def _read_client_pid_file() -> int | None:
    pid_file = _client_pid_path()
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _remove_client_pid_file() -> None:
    pid_file = _client_pid_path()
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def _print_upgrade_check(update_info: dict) -> None:
    if "error" in update_info:
        typer.echo(f"❌ Failed to check for updates: {update_info['error']}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"📋 Current version: {update_info['current_version']}")
    typer.echo(f"📋 Latest version:  {update_info['latest_version']}")

    if not update_info.get("is_newer"):
        typer.echo("✅ Already on latest version.")
        return
    if update_info.get("asset_available"):
        typer.echo("🎯 Update available! Run 'adm-agent-client upgrade' to install.")
        return

    typer.echo("⚠️  Update available but no compatible client asset found.")
    release_url = update_info.get("release_url")
    if release_url:
        typer.echo(f"   Manual download: {release_url}")


@app.command()
def init() -> None:
    """Initialize client config interactively."""
    url_input = str(typer.prompt("Serve URL", default="http://127.0.0.1:8910")).strip()
    if not url_input.startswith(("http://", "https://", "ws://", "wss://")):
        url_input = f"http://{url_input}"
        
    client_name = str(typer.prompt("Client name", default=_default_client_name())).strip()
    config = ClientConfig(
        server_url=url_input,
        client_name=client_name or _default_client_name(),
        client_id=ensure_client_id(None),
        workdir=str(Path.cwd()),
        policy_profile=ClientPolicyProfile(),
    )
    path = save_client_config(config)
    typer.echo(f"Config saved: {path}")
    typer.echo(f"Client ID: {config.client_id}")


@app.command()
def status() -> None:
    """Show current client config and connectivity probe."""
    config = load_client_config()
    if not config:
        typer.echo("Client is not initialized. Run: adm-agent-client init", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Client ID: {config.client_id}")
    typer.echo(f"Client Name: {config.client_name}")
    typer.echo(f"Serve URL: {config.server_url}")
    typer.echo(f"Workdir: {config.workdir}")

    connectivity = asyncio.run(ClientRuntime(config).start_once())
    state = "reachable" if connectivity.connected else "unreachable"
    typer.echo(f"Connectivity: {state} ({connectivity.endpoint})")


@app.command()
def start(
    once: bool = typer.Option(
        False,
        "--once/--continuous",
        help="Run one connectivity probe or continuous websocket runtime",
    ),
) -> None:
    """Start client runtime."""
    config = load_client_config()
    if not config:
        typer.echo("Client is not initialized. Run: adm-agent-client init", err=True)
        raise typer.Exit(code=1)

    runtime = ClientRuntime(config)
    if once:
        result = asyncio.run(runtime.start_once())
        state = "connected" if result.connected else "disconnected"
        typer.echo(f"{state}: {result.endpoint}")
        return

    _configure_client_logging()
    typer.echo("Starting websocket runtime. Press Ctrl+C to stop.")
    _write_client_pid_file()
    try:
        asyncio.run(runtime.run_forever())
    except KeyboardInterrupt:
        typer.echo("Stopped.")
    finally:
        _remove_client_pid_file()


def _build_client_base_cmd() -> list[str]:
    """Return the base argv to re-invoke this CLI (handles PyInstaller too)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, sys.argv[0]]


_CLIENT_LOG_FILE = get_client_home() / "client.log"


def _configure_client_logging() -> None:
    """Set up logging to both stderr and client.log file."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # file handler
    _CLIENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(_CLIENT_LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    # stderr handler (visible in foreground mode)
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)


@app.command(name="start-install")
def start_install() -> None:
    """Start the client as a background daemon.

    Launches ``start --continuous`` in a detached process so it persists
    after the terminal is closed.  Use ``stop`` to terminate it.
    """
    config = load_client_config()
    if not config:
        typer.echo("Client is not initialized. Run: adm-agent-client init", err=True)
        raise typer.Exit(code=1)

    existing_pid = _read_client_pid_file()
    if existing_pid is not None:
        try:
            os.kill(existing_pid, 0)
            typer.echo(
                f"⚠️  Client already running (PID {existing_pid}). "
                "Stop it first with: adm-agent-client stop"
            )
            raise typer.Exit(code=1)
        except (ProcessLookupError, OSError):
            _remove_client_pid_file()

    cmd = _build_client_base_cmd() + ["start", "--continuous"]

    log_dir = get_client_home()
    log_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    typer.echo(f"🚀 Client daemon started (PID {proc.pid})")
    typer.echo(f"   Log: {_CLIENT_LOG_FILE}")
    typer.echo("   Stop: adm-agent-client stop")


@app.command()
def stop(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force kill (SIGKILL) when graceful stop fails",
    ),
) -> None:
    """Stop a running client started by `adm-agent-client start --continuous`."""
    pid = _read_client_pid_file()
    pid_file = _client_pid_path()
    if pid is None:
        typer.echo("ℹ️  No running client found.")
        typer.echo(f"   Checked: {pid_file} (not present)")
        raise typer.Exit(code=0)

    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        typer.echo(f"ℹ️  Client process (PID {pid}) is not running. Removing stale PID file.")
        _remove_client_pid_file()
        raise typer.Exit(code=0)

    sig = signal.SIGKILL if force else signal.SIGTERM
    sig_name = "SIGKILL" if force else "SIGTERM"
    try:
        os.kill(pid, sig)
        typer.echo(f"✅ {sig_name} sent to client (PID {pid}, found via PID file)")
        _remove_client_pid_file()
    except (ProcessLookupError, PermissionError, OSError) as exc:
        typer.echo(f"❌ Failed to stop client (PID {pid}): {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def version(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed version info"),
) -> None:
    """Display current client version information."""
    try:
        current = get_current_version()
        if verbose:
            os_name, arch_name = get_platform_info()
            typer.echo(f"adm-agent-client {current}")
            typer.echo(f"Platform: {os_name}-{arch_name}")
            typer.echo(f"Python: {sys.version}")
            typer.echo(f"Executable: {sys.executable}")
            return
        typer.echo(current)
    except Exception as exc:
        typer.echo(f"❌ Failed to get version: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def upgrade(
    check_only: bool = typer.Option(False, "--check", help="Only check for updates, do not install"),
    force: bool = typer.Option(False, "--force", help="Force upgrade even if already latest version"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed upgrade logs"),
) -> None:
    """Check for and install client updates from GitHub releases."""
    try:
        update_info = check_for_updates_for_artifact(
            artifact_name="adm-agent-client",
            verbose=verbose,
        )
        if check_only:
            _print_upgrade_check(update_info)
            return

        upgraded = upgrade_artifact(
            artifact_name="adm-agent-client",
            force=force,
            verbose=verbose,
        )
        if not upgraded:
            typer.echo("ℹ️  No upgrade needed.")
            return
        typer.echo("🎉 Upgrade completed successfully!")
        typer.echo("ℹ️  Restart the client if it is currently running.")
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ Upgrade failed: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command("bootstrap")
def bootstrap(
    target: str = typer.Option(
        "generic",
        "--target",
        help="Prompt target: codex, claude, openclaw, generic",
    ),
    emit_prompt: bool = typer.Option(
        False,
        "--emit-prompt",
        help="Emit prompt text for copy/paste into your LLM tool",
    ),
    url: str = typer.Option(
        "",
        "--url",
        help="Override serve URL in generated prompt",
    ),
) -> None:
    """Generate a setup prompt for external LLM tools."""
    config = load_client_config()
    resolved_url = str(url or (config.server_url if config else "http://127.0.0.1:8910")).strip()
    prompt = build_bootstrap_prompt(
        target=target,
        server_url=resolved_url,
    )
    if emit_prompt:
        typer.echo(prompt)
        return
    typer.echo("Use --emit-prompt to print full bootstrap prompt text.")
    typer.echo(prompt)


@app.command("fetch")
def fetch(
    url: str = typer.Option(..., "--url", help="Target page URL"),
    page_type: str = typer.Option("auto", "--page-type", help="auto | index | detail"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON payload to stdout"),
    max_detail_links: int = typer.Option(4, "--max-detail-links", help="Max detail links to fetch for index page"),
    debug_port: int = typer.Option(9222, "--debug-port", help="Chrome/Edge remote debugging port"),
    browser_path: str = typer.Option("", "--browser-path", help="Optional browser executable path"),
) -> None:
    """Fetch page payload via local browser CDP automation."""
    normalized = str(page_type or "auto").strip().lower()
    if normalized not in {"auto", "index", "detail"}:
        typer.echo("Error: --page-type must be one of: auto, index, detail", err=True)
        raise typer.Exit(code=1)
    payload = fetch_browser_payload(
        url=str(url or "").strip(),
        page_type_hint=normalized,
        detail_limit=max(1, int(max_detail_links)),
        browser_path=str(browser_path or "").strip() or None,
        debug_port=int(debug_port),
    )
    config = load_client_config()
    if config and config.policy_profile is not None:
        payload["policy_profile"] = config.policy_profile.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    typer.echo(f"Fetched payload keys: {', '.join(sorted(payload.keys()))}")


if __name__ == "__main__":
    app()
