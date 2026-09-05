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
    ExitCode,
    check_for_updates,
    default_client_layout,
    default_client_pid_file,
    get_current_version,
    get_platform_info,
    is_frozen,
    is_process_alive,
    perform_upgrade,
    rollback,
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
    elif result.is_newer:
        typer.echo("🎯 Update available! Run 'adm-agent-client upgrade' to install it.")
    else:
        typer.echo("✅ Already on latest version.")
    for warning in result.warnings:
        typer.echo(f"   • {warning}", err=result.exit_code != 0)


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
        # `is_process_alive`, never `os.kill(pid, 0)`: on Windows os.kill
        # terminates the target rather than probing it.
        if is_process_alive(existing_pid):
            typer.echo(
                f"⚠️  Client already running (PID {existing_pid}). "
                "Stop it first with: adm-agent-client stop"
            )
            raise typer.Exit(code=1)
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

    # Probing must never terminate the target, which `os.kill(pid, 0)` would
    # do on Windows.
    if not is_process_alive(pid):
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
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Display current client version information."""
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
    check_only: bool = typer.Option(False, "--check", help="Only check, don't install"),
    force: bool = typer.Option(False, "--force", help="Install even if not newer"),
    rollback_to_previous: bool = typer.Option(
        False, "--rollback", help="Return to the previously installed version"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Check for, install, or undo client updates."""
    if verbose:
        _configure_client_logging()

    try:
        if rollback_to_previous:
            # migrate=False: the client has no database (mirrors the
            # forward path below). default_post_check is a no-op for a
            # non-"adm-agent" artifact anyway.
            result = rollback(default_client_layout(), migrate=False)
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
        if as_json:
            typer.echo(
                json.dumps({"action_taken": "blocked", "blocked_reason": "unexpected",
                            "warnings": [str(exc)]}, ensure_ascii=False)
            )
        else:
            typer.echo(f"❌ Upgrade failed: {exc}", err=True)
        raise typer.Exit(code=int(ExitCode.UNEXPECTED))

    _emit_client(result, as_json)
    if result.exit_code != int(ExitCode.OK):
        raise typer.Exit(code=result.exit_code)


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
    page_type: str = typer.Option("index", "--page-type", help="index (default) | detail"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON payload to stdout"),
    max_detail_links: int = typer.Option(4, "--max-detail-links", help="Max detail links to fetch for index page"),
    debug_port: int = typer.Option(9222, "--debug-port", help="Chrome/Edge remote debugging port"),
    browser_path: str = typer.Option("", "--browser-path", help="Optional browser executable path"),
) -> None:
    """Fetch page payload via local browser CDP automation."""
    normalized = str(page_type or "index").strip().lower()
    if normalized not in {"index", "detail"}:
        typer.echo("Error: --page-type must be index or detail", err=True)
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


@app.command("chat")
def chat(
    server: str = typer.Option(
        "",
        "--server",
        "-s",
        help="Server base URL (default: from client config or http://127.0.0.1:8910)",
    ),
) -> None:
    """Start an interactive chat session with the agent via the server-side LLM.

    Streams live events (LLM thinking, tool calls, summary tokens) as they happen.
    Type 'exit' or 'quit' to leave, Ctrl-C to abort.
    """
    asyncio.run(_chat_loop(server))


async def _chat_loop(server_override: str) -> None:
    """Async chat REPL — sends messages to /agent/chat and streams SSE events."""
    import httpx  # transitive dependency via fastapi/mcp

    config = load_client_config()
    base_url = (
        str(server_override or "").strip()
        or (config.server_url if config else "")
        or "http://127.0.0.1:8910"
    )
    base_url = base_url.rstrip("/")

    # Verify connectivity + agent enabled
    try:
        async with httpx.AsyncClient(timeout=10.0) as probe:
            resp = await probe.get(f"{base_url}/status")
            resp.raise_for_status()
            status_data = resp.json()
    except Exception as exc:
        typer.echo(f"Cannot reach server at {base_url}: {exc}", err=True)
        raise typer.Exit(code=1)

    if not status_data.get("agent_enabled"):
        typer.echo(
            "Agent is disabled on the server. "
            "Re-enable it with AGENT_ENABLED=true before chatting.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Connected to {base_url}  (agent enabled)")
    typer.echo("Type your message and press Enter. Type 'exit' or Ctrl-C to quit.\n")

    async with httpx.AsyncClient(timeout=None) as client:
        while True:
            try:
                raw = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                typer.echo("\nGoodbye.")
                break

            if not raw or raw.lower() in ("exit", "quit"):
                typer.echo("Goodbye.")
                break

            # Submit chat message
            try:
                post_resp = await client.post(
                    f"{base_url}/agent/chat",
                    json={"message": raw},
                    timeout=30.0,
                )
                post_resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                typer.echo(f"Error: {exc.response.status_code} — {exc.response.text}", err=True)
                continue
            except Exception as exc:
                typer.echo(f"Request failed: {exc}", err=True)
                continue

            task_id = post_resp.json().get("task_id", "")
            typer.echo(f"[task: {task_id}]")

            # Stream SSE events
            await _stream_task_events(client, base_url, task_id)
            typer.echo()  # blank line before next prompt


async def _stream_task_events(
    client: "httpx.AsyncClient",
    base_url: str,
    task_id: str,
) -> None:
    """Consume /tasks/{task_id}/events and print formatted output."""
    import httpx

    url = f"{base_url}/tasks/{task_id}/events"
    summary_started = False

    try:
        async with client.stream("GET", url, timeout=None) as response:
            response.raise_for_status()
            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                data_str = raw_line[6:].strip()
                if not data_str:
                    continue
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                evt_type = str(event.get("type", ""))

                if evt_type == "agent_started":
                    typer.echo("Agent: [started]")

                elif evt_type == "llm_call_started":
                    iteration = event.get("iteration", "?")
                    typer.echo(f"  ● Thinking… (iter {iteration})", nl=False)
                    sys.stdout.flush()

                elif evt_type == "llm_call_finished":
                    typer.echo("  ✓")

                elif evt_type == "tool_call_started":
                    name = event.get("tool_name") or event.get("name") or "unknown"
                    typer.echo(f"  → {name}", nl=False)
                    sys.stdout.flush()

                elif evt_type == "tool_call_finished":
                    typer.echo("  ✓")

                elif evt_type == "persist_started":
                    typer.echo("  [Persisting programs…]")

                elif evt_type == "persist_finished":
                    typer.echo("  [Persist done]")

                elif evt_type == "summary_started":
                    typer.echo("\nAgent: ", nl=False)
                    summary_started = True
                    sys.stdout.flush()

                elif evt_type == "summary_delta":
                    delta = str(event.get("delta", ""))
                    typer.echo(delta, nl=False)
                    sys.stdout.flush()

                elif evt_type == "summary_finished":
                    if summary_started:
                        typer.echo()  # newline after streaming text
                    summary_started = False

                elif evt_type in ("agent_done", "agent_failed"):
                    if evt_type == "agent_failed":
                        typer.echo(f"\n[Failed: {event.get('error', '')}]", err=True)
                    break

    except httpx.RemoteProtocolError:
        pass  # Server closed the stream cleanly
    except KeyboardInterrupt:
        typer.echo("\n[Interrupted]")
    except Exception as exc:
        typer.echo(f"\n[Stream error: {exc}]", err=True)

    # Fallback: if no summary events, show agent_response from task result
    if not summary_started:
        try:
            result_resp = await client.get(f"{base_url}/tasks/{task_id}", timeout=10.0)
            if result_resp.status_code == 200:
                task_data = result_resp.json()
                result = task_data.get("result") or {}
                output = result.get("output") or {}
                agent_resp = str(output.get("agent_response", "") or "").strip()
                if agent_resp:
                    typer.echo(f"\nAgent: {agent_resp}")
                elif task_data.get("state") == "FAILED":
                    typer.echo(f"\n[Task failed: {task_data.get('error', '')}]", err=True)
        except Exception:
            pass


if __name__ == "__main__":
    app()
