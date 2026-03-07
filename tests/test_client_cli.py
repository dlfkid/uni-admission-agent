from pathlib import Path

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

