from __future__ import annotations

import pytest

from src.core.async_utils import run_sync


async def _produce_value() -> int:
    return 42


def test_run_sync_executes_coroutine_outside_loop() -> None:
    result = run_sync(_produce_value, label="demo()")
    assert result == 42


@pytest.mark.asyncio
async def test_run_sync_raises_inside_active_event_loop() -> None:
    with pytest.raises(RuntimeError, match="cannot run inside an active event loop"):
        run_sync(_produce_value, label="demo()")
