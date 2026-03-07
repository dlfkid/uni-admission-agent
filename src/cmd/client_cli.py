#!/usr/bin/env python3
"""CLI for user-side `adm-agent-client` runtime."""

from __future__ import annotations

import asyncio
import json
import os
import platform
from pathlib import Path
import signal

import typer

from src.client.bootstrap_prompt import build_bootstrap_prompt
from src.client.config import (
    ClientConfig,
    ensure_client_id,
    get_client_home,
    load_client_config,
    save_client_config,
)
from src.client.native_browser import fetch_browser_payload
from src.client.runtime import ClientRuntime


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


@app.command()
def init() -> None:
    """Initialize client config interactively."""
    host = str(typer.prompt("Serve host", default="127.0.0.1")).strip()
    port = int(typer.prompt("Serve port", default="8910"))
    client_name = str(typer.prompt("Client name", default=_default_client_name())).strip()
    config = ClientConfig(
        server_host=host or "127.0.0.1",
        server_port=port,
        client_name=client_name or _default_client_name(),
        client_id=ensure_client_id(None),
        workdir=str(Path.cwd()),
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
    typer.echo(f"Serve: {config.server_host}:{config.server_port}")
    typer.echo(f"Workdir: {config.workdir}")

    connectivity = asyncio.run(ClientRuntime(config).start_once())
    state = "reachable" if connectivity.connected else "unreachable"
    typer.echo(f"Connectivity: {state} ({connectivity.endpoint})")


@app.command()
def start(
    once: bool = typer.Option(
        True,
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

    typer.echo("Starting websocket runtime. Press Ctrl+C to stop.")
    _write_client_pid_file()
    try:
        asyncio.run(runtime.run_forever())
    except KeyboardInterrupt:
        typer.echo("Stopped.")
    finally:
        _remove_client_pid_file()


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
    host: str = typer.Option(
        "",
        "--host",
        help="Override serve host in generated prompt",
    ),
    port: int = typer.Option(
        0,
        "--port",
        help="Override serve port in generated prompt",
    ),
) -> None:
    """Generate a setup prompt for external LLM tools."""
    config = load_client_config()
    resolved_host = str(host or (config.server_host if config else "127.0.0.1")).strip()
    resolved_port = int(port or (config.server_port if config else 8910))
    prompt = build_bootstrap_prompt(
        target=target,
        host=resolved_host,
        port=resolved_port,
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
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    typer.echo(f"Fetched payload keys: {', '.join(sorted(payload.keys()))}")


if __name__ == "__main__":
    app()
