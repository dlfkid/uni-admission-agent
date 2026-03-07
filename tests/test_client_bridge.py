from src.services.client_bridge import ClientRegistry, ClientSession


def test_registry_register_and_list_clients() -> None:
    registry = ClientRegistry()
    registry.register(
        ClientSession(
            client_id="c1",
            client_name="Rayne-Mac",
            platform="darwin",
            arch="arm64",
            workdir="/Users/rayne",
            capabilities={"browser_automation": True},
        )
    )
    rows = registry.list_clients()
    assert len(rows) == 1
    assert rows[0]["client_id"] == "c1"
    assert rows[0]["client_name"] == "Rayne-Mac"
    assert rows[0]["platform"] == "darwin"
    assert rows[0]["arch"] == "arm64"
    assert rows[0]["workdir"] == "/Users/rayne"
    assert rows[0]["capabilities"]["browser_automation"] is True


def test_registry_prefers_recent_active_client() -> None:
    registry = ClientRegistry()
    registry.register(
        ClientSession(
            client_id="c1",
            client_name="Mac-A",
            platform="darwin",
            arch="arm64",
            workdir="/Users/a",
            capabilities={"browser_automation": True},
            last_seen_epoch=100.0,
        )
    )
    registry.register(
        ClientSession(
            client_id="c2",
            client_name="Mac-B",
            platform="darwin",
            arch="arm64",
            workdir="/Users/b",
            capabilities={"browser_automation": True},
            last_seen_epoch=200.0,
        )
    )
    registry.register(
        ClientSession(
            client_id="c3",
            client_name="Mac-C",
            platform="darwin",
            arch="arm64",
            workdir="/Users/c",
            capabilities={"browser_automation": False},
            last_seen_epoch=300.0,
        )
    )
    assert registry.select_client_id(preferred_client_id=None) == "c2"
    assert registry.select_client_id(preferred_client_id="c1") == "c1"
    assert registry.select_client_id(preferred_client_id="missing") == "c2"


def test_registry_returns_none_when_no_automation_client() -> None:
    registry = ClientRegistry()
    registry.register(
        ClientSession(
            client_id="c1",
            client_name="NoAutomation",
            platform="darwin",
            arch="arm64",
            workdir="/Users/a",
            capabilities={"browser_automation": False},
        )
    )
    assert registry.select_client_id(preferred_client_id=None) is None

