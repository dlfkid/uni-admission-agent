#!/usr/bin/env python3
"""CLI for user-side `adm-agent-client` runtime."""

from __future__ import annotations

import asyncio
import platform
from pathlib import Path

import typer

from src.client.bootstrap_prompt import build_bootstrap_prompt
from src.client.config import (
    ClientConfig,
    ensure_client_id,
    load_client_config,
    save_client_config,
)
from src.client.runtime import ClientRuntime


app = typer.Typer(
    name="adm-agent-client",
    help="adm-agent-client — connect local browser automation client to serve",
    add_completion=False,
)


def _default_client_name() -> str:
    name = platform.node().strip()
    return name or "adm-agent-client"


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
    try:
        asyncio.run(runtime.run_forever())
    except KeyboardInterrupt:
        typer.echo("Stopped.")


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


if __name__ == "__main__":
    app()
