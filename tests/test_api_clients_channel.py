from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.server import app
from src.services.client_bridge import ClientRegistry


def test_clients_endpoint_lists_registered_ws_client(monkeypatch) -> None:
    monkeypatch.setattr("src.api.server.client_registry", ClientRegistry())
    monkeypatch.setattr("src.api.server.client_sockets", {})
    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        with client.websocket_connect("/clients/ws") as ws:
            ws.send_json(
                {
                    "type": "register",
                    "client_id": "c1",
                    "client_name": "Rayne-Mac",
                    "platform": "darwin",
                    "arch": "arm64",
                    "workdir": "/Users/rayne",
                    "capabilities": {"browser_automation": True},
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "registered"
            assert ack["client_id"] == "c1"
            data = client.get("/clients").json()
            assert any(row["client_id"] == "c1" for row in data)


def test_clients_ws_rpc_result_routes_to_broker(monkeypatch) -> None:
    class DummyBroker:
        def __init__(self) -> None:
            self.calls = []

        def resolve(self, request_id: str, payload: dict) -> bool:
            self.calls.append((request_id, payload))
            return True

        def fail_all_for_client(self, _client_id: str, _message: str) -> int:
            return 0

    broker = DummyBroker()
    monkeypatch.setattr("src.api.server.client_registry", ClientRegistry())
    monkeypatch.setattr("src.api.server.client_sockets", {})
    monkeypatch.setattr("src.api.server.client_rpc_broker", broker)

    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        with client.websocket_connect("/clients/ws") as ws:
            ws.send_json(
                {
                    "type": "register",
                    "client_id": "c1",
                    "client_name": "Rayne-Mac",
                    "platform": "darwin",
                    "arch": "arm64",
                    "workdir": "/Users/rayne",
                    "capabilities": {"browser_automation": True},
                }
            )
            ws.receive_json()
            ws.send_json(
                {
                    "type": "rpc_result",
                    "request_id": "req-1",
                    "payload": {"html_content": "<html/>"},
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "rpc_ack"
            assert ack["request_id"] == "req-1"
            assert ack["accepted"] is True

    assert broker.calls == [("req-1", {"html_content": "<html/>"})]
