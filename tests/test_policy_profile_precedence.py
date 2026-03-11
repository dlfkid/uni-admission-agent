from src.agent_runtime.policy import merge_policy


def test_policy_precedence_request_over_client_over_server_defaults():
    merged = merge_policy(
        request_overrides={"batch_size": 3},
        client_policy={"batch_size": 2},
        server_defaults={"batch_size": 1},
    )

    assert merged.profile.batch_size == 3


def test_policy_normalization_clamps_thresholds():
    merged = merge_policy(
        request_overrides={"taxonomy_auto_threshold": 1.5},
        client_policy={},
        server_defaults={},
    )

    assert merged.profile.taxonomy_auto_threshold == 1.0
    assert merged.warnings


def test_policy_normalization_reports_unknown_key_and_bool_coercion():
    merged = merge_policy(
        request_overrides={
            "unknown_toggle": True,
            "llm_fallback_enabled": "off",
            "require_manual_review_when_low_confidence": "on",
        },
        client_policy={},
        server_defaults={},
    )

    assert merged.profile.llm_fallback_enabled is False
    assert merged.profile.require_manual_review_when_low_confidence is True
    assert any("unknown policy key ignored" in warning for warning in merged.warnings)
