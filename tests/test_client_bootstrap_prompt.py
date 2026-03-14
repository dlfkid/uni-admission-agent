from src.client.bootstrap_prompt import build_bootstrap_prompt


def test_bootstrap_prompt_supports_openclaw_target() -> None:
    prompt = build_bootstrap_prompt(
        target="openclaw",
        server_url="http://127.0.0.1:8910",
    )
    assert "OpenClaw" in prompt
    assert "adm-agent-client init" in prompt
    assert "adm-agent-client start" in prompt
    assert "page_type" in prompt
    assert "索引" in prompt or "index" in prompt


def test_bootstrap_prompt_falls_back_to_generic_template() -> None:
    prompt = build_bootstrap_prompt(
        target="unknown",
        server_url="http://10.0.0.12:9100",
    )
    assert "10.0.0.12" in prompt
    assert "9100" in prompt
    assert "adm-agent-client status" in prompt
    assert "page_type_hint=\"auto\"" in prompt
