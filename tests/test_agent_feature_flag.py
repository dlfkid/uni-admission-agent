from typer.testing import CliRunner

from src.cmd.cli import app


runner = CliRunner()


def test_agent_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_ENABLED", raising=False)

    from src.api.server import is_agent_enabled

    assert is_agent_enabled() is False


def test_serve_agent_flag_enables_runtime(monkeypatch):
    monkeypatch.setenv("AGENT_ENABLED", "false")

    result = runner.invoke(app, ["serve", "--agent", "--dry-run"])

    assert result.exit_code == 0
    assert "agent enabled" in result.stdout.lower()
