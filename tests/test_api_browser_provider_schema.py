from src.api.schemas import AgentRunRequest, CrawlRequest


def test_crawl_request_accepts_browser_provider_fields() -> None:
    model = CrawlRequest.model_validate(
        {
            "url": "https://example.edu/programmes",
            "univ_slug": "manchester",
            "year": 2026,
            "browser_provider": "client",
            "client_id": "client-123",
            "strict_client": True,
            "candidate_taxonomy_filter_enabled": True,
            "candidate_taxonomy_filter_threshold": 0.81,
            "candidate_taxonomy_filter_top_k": 12,
        }
    )
    assert model.browser_provider == "client"
    assert model.client_id == "client-123"
    assert model.strict_client is True
    assert model.candidate_taxonomy_filter_enabled is True
    assert model.candidate_taxonomy_filter_threshold == 0.81
    assert model.candidate_taxonomy_filter_top_k == 12


def test_crawl_request_defaults_browser_provider_auto() -> None:
    model = CrawlRequest.model_validate(
        {
            "url": "https://example.edu/programmes",
            "univ_slug": "manchester",
            "year": 2026,
        }
    )
    assert model.browser_provider == "auto"
    assert model.strict_client is False
    assert model.candidate_taxonomy_filter_enabled is False
    assert model.candidate_taxonomy_filter_threshold == 0.75
    assert model.candidate_taxonomy_filter_top_k == 30


def test_agent_run_request_accepts_prefer_browser_provider() -> None:
    """The popup's `/agent/run` wire format threads the user-selected
    browser source through ``policy_profile.prefer_browser_provider`` —
    this guards that contract so silent serializer drift can't ship."""
    model = AgentRunRequest.model_validate(
        {
            "url": "https://example.edu/programmes",
            "univ_slug": "manchester",
            "year": 2026,
            "runtime": "pydanticai",
            "autonomous": True,
            "policy_profile": {
                "taxonomy_keep_threshold": 0.80,
                "taxonomy_auto_threshold": 0.92,
                "auto_run_max_candidates": 30,
                "prefer_browser_provider": "client",
            },
        }
    )
    assert model.policy_profile is not None
    assert model.policy_profile.prefer_browser_provider == "client"
