"""Regression test for the client-mode RPC self-deadlock.

``_fetch_browser_payload_from_client_sync`` (src/api/server.py) is a *sync*
bridge that schedules a coroutine onto the main event loop via
``asyncio.run_coroutine_threadsafe`` and then blocks the calling thread with
``future.result(timeout=...)``. That's only safe when the sync function is
invoked from a *different* thread than the one running that event loop.

``fetch_index_and_details_via_client`` in ``src/services/browser_provider.py``
must therefore run any *sync* ``fetch_fn`` off the event loop thread (e.g.
via ``asyncio.to_thread``). If it instead calls the sync bridge in-line on
the event loop, the bridge deadlocks: it waits on a future for a coroutine
that can only be scheduled by the very loop thread that is now blocked
waiting for it.

This test models that exact shape without touching the real websocket/CLI
machinery: a sync ``fetch_fn`` that schedules a coroutine on the *current*
running loop via ``run_coroutine_threadsafe`` and blocks on ``.result()``.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.services import browser_provider


@pytest.mark.asyncio
async def test_sync_fetch_fn_does_not_block_the_event_loop(monkeypatch) -> None:
    loop = asyncio.get_running_loop()

    async def _rpc_roundtrip() -> dict:
        # Stands in for `_fetch_browser_payload_from_client`, which can only
        # ever run if the event loop is free to schedule it.
        await asyncio.sleep(0.05)
        return {"html_content": "<html>ok</html>"}

    def sync_fetch_fn(*, url: str, page_type_hint: str, client_id):
        # Mirrors `_fetch_browser_payload_from_client_sync`.
        future = asyncio.run_coroutine_threadsafe(_rpc_roundtrip(), loop)
        return future.result(timeout=2)

    monkeypatch.setattr(browser_provider, "_dispatchers", {
        "availability_fn": None,
        "fetch_fn": sync_fetch_fn,
        "select_client_fn": None,
    })

    started = time.monotonic()
    result = await asyncio.wait_for(
        browser_provider.fetch_index_and_details_via_client(
            url="https://example.edu/programmes",
            page_type_hint="index",
            client_id=None,
        ),
        timeout=1.0,
    )
    elapsed = time.monotonic() - started

    assert result["html_content"] == "<html>ok</html>"
    # If the sync bridge ran in-line on the event loop, `_rpc_roundtrip`
    # could never be scheduled and `sync_fetch_fn` would block for the
    # full 2-second internal timeout before raising. Running promptly
    # (well under that) proves the loop stayed free to service it.
    assert elapsed < 1.0
