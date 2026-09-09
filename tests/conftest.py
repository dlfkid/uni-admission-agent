"""
pytest configuration and global fixtures.

This file contains shared test fixtures and pytest configuration
that will be available to all test modules.
"""


import pytest  # noqa: E402  pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def _no_network_strategy_discovery(monkeypatch):
    """Keep crawl_url's strategy discovery off the network in every test.

    crawl_url runs discover_with_default_adapters — a real HTTP fetch of the
    index URL — whenever page_type_hint is "index" and no URLs were
    pre-selected. Its default became "index" when "auto" was retired, and a
    handful of plumbing tests that never mentioned page_type_hint started
    fetching https://example.com/... and waiting on timeouts (14–35 s each,
    the suite went from 30 s to 3.5 min). Tests that want a specific
    discovery outcome monkeypatch the same attribute themselves; a patch made
    inside the test runs after this one and wins.
    """
    from src.services.crawl_strategy.discovery import DiscoveryResult  # pylint: disable=import-outside-toplevel
    import src.services.crawler as crawler_mod  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        crawler_mod, "discover_with_default_adapters",
        lambda url, rng: DiscoveryResult(matched=False),
    )
