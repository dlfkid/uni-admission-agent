#!/usr/bin/env python3
"""CLI for user-side `adm-agent-client` runtime."""

from __future__ import annotations

import asyncio
import platform
import time
from pathlib import Path

import typer

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
        help="Run one connection probe or continuous probe loop",
    ),
    interval_seconds: int = typer.Option(10, "--interval", min=1, help="Continuous probe interval in seconds"),
) -> None:
    """Start client runtime (currently connectivity probe loop)."""
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

    typer.echo("Starting continuous probe. Press Ctrl+C to stop.")
    try:
        while True:
            result = asyncio.run(runtime.start_once())
            state = "connected" if result.connected else "disconnected"
            typer.echo(f"{state}: {result.endpoint}")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        typer.echo("Stopped.")


if __name__ == "__main__":
    app()

