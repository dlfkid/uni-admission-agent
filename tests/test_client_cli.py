import json
from pathlib import Path
import signal

from typer.testing import CliRunner

from src.cmd.client_cli import app


runner = CliRunner()


def test_client_init_writes_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(
        app,
        ["init"],
        input="127.0.0.1\n8910\nRayne-Mac\n",
    )
    assert result.exit_code == 0

    cfg = tmp_path / ".adm-agent" / "client.toml"
    assert cfg.exists()
    content = cfg.read_text(encoding="utf-8")
    assert 'server_host = "127.0.0.1"' in content
    assert "server_port = 8910" in content
    assert 'client_name = "Rayne-Mac"' in content


def test_client_status_reads_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_dir = tmp_path / ".adm-agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "client.toml"
    cfg_path.write_text(
        'server_host = "127.0.0.1"\n'
        "server_port = 8910\n"
        'client_name = "Rayne-Mac"\n'
        'client_id = "client-001"\n'
        'workdir = "/Users/rayne"\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "client-001" in result.stdout


def test_client_fetch_outputs_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.cmd.client_cli.fetch_browser_payload",
        lambda **kwargs: {
            "html_content": "<html></html>",
            "detail_pages_batch": [],
            "selected_urls": [],
        },
    )
    result = runner.invoke(
        app,
        [
            "fetch",
            "--url",
            "https://example.edu/list",
            "--page-type",
            "index",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["html_content"] == "<html></html>"


def test_client_stop_reports_when_not_running(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0
    assert "No running client found" in result.stdout


def test_client_stop_sends_sigterm_and_removes_pid_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    pid_file = tmp_path / ".adm-agent" / "client.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("43210", encoding="utf-8")

    calls: list[tuple[int, int]] = []

    def _fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr("src.cmd.client_cli.os.kill", _fake_kill)
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0
    assert calls == [(43210, 0), (43210, signal.SIGTERM)]
    assert not pid_file.exists()


def test_client_stop_force_sends_sigkill(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    pid_file = tmp_path / ".adm-agent" / "client.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("54321", encoding="utf-8")

    calls: list[tuple[int, int]] = []

    def _fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr("src.cmd.client_cli.os.kill", _fake_kill)
    result = runner.invoke(app, ["stop", "--force"])
    assert result.exit_code == 0
    assert calls == [(54321, 0), (54321, signal.SIGKILL)]
    assert not pid_file.exists()


def test_client_version_outputs_current_version(monkeypatch) -> None:
    monkeypatch.setattr("src.cmd.client_cli.get_current_version", lambda: "v9.9.9")
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "v9.9.9"


def test_client_upgrade_check_uses_client_artifact(monkeypatch) -> None:
    captured: dict = {}

    def _fake_check_for_updates_for_artifact(*, artifact_name: str, verbose: bool):
        captured["artifact_name"] = artifact_name
        captured["verbose"] = verbose
        return {
            "current_version": "v1.0.0",
            "latest_version": "v1.1.0",
            "is_newer": True,
            "asset_available": True,
            "release_url": "https://example.com/release",
        }

    monkeypatch.setattr(
        "src.cmd.client_cli.check_for_updates_for_artifact",
        _fake_check_for_updates_for_artifact,
    )

    result = runner.invoke(app, ["upgrade", "--check"])
    assert result.exit_code == 0
    assert captured["artifact_name"] == "adm-agent-client"
    assert captured["verbose"] is False
    assert "Update available" in result.stdout


def test_client_upgrade_runs_upgrade_artifact(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.cmd.client_cli.check_for_updates_for_artifact",
        lambda **_kwargs: {
            "current_version": "v1.0.0",
            "latest_version": "v1.1.0",
            "is_newer": True,
            "asset_available": True,
            "release_url": "https://example.com/release",
        },
    )
    called: dict = {}

    def _fake_upgrade_artifact(*, artifact_name: str, force: bool, verbose: bool) -> bool:
        called["artifact_name"] = artifact_name
        called["force"] = force
        called["verbose"] = verbose
        return True

    monkeypatch.setattr("src.cmd.client_cli.upgrade_artifact", _fake_upgrade_artifact)

    result = runner.invoke(app, ["upgrade", "--force", "--verbose"])
    assert result.exit_code == 0
    assert called == {
        "artifact_name": "adm-agent-client",
        "force": True,
        "verbose": True,
    }
    assert "Upgrade completed successfully" in result.stdout
