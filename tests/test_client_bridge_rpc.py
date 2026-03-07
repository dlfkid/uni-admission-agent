import pytest

from src.services.client_bridge import ClientRpcBroker, ClientUnavailableError


@pytest.mark.asyncio
async def test_rpc_broker_resolves_response_by_request_id() -> None:
    broker = ClientRpcBroker(timeout_seconds=0.1)
    request_id, _future = broker.create_pending("c1")
    broker.resolve(request_id, {"html": "<html/>"})
    payload = await broker.wait_for_response(request_id)
    assert payload["html"] == "<html/>"


@pytest.mark.asyncio
async def test_rpc_broker_times_out() -> None:
    broker = ClientRpcBroker(timeout_seconds=0.01)
    request_id, _future = broker.create_pending("c1")
    with pytest.raises(ClientUnavailableError, match="timed out"):
        await broker.wait_for_response(request_id)


@pytest.mark.asyncio
async def test_rpc_broker_raises_for_missing_request() -> None:
    broker = ClientRpcBroker(timeout_seconds=0.01)
    with pytest.raises(ClientUnavailableError, match="not pending"):
        await broker.wait_for_response("missing")

