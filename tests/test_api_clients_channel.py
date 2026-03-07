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

