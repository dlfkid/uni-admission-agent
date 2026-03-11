"""Client automation bridge wrappers used by agent runtime skills."""

from __future__ import annotations

from functools import partial
import inspect
from typing import Any, Awaitable, Callable

from src.agent_bridge.contracts import BrowserFetchInput, BrowserFetchOutput
from src.core.async_utils import run_sync
from src.services import browser_provider as browser_provider_service

FetchFn = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]


class ClientAutomationBridge:
    """Bridge adapter over client-browser automation payload fetching."""

    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self._fetch_fn = fetch_fn or browser_provider_service.fetch_index_and_details_via_client

    async def fetch_browser_payload_async(self, payload: BrowserFetchInput) -> BrowserFetchOutput:
        raw = self._fetch_fn(
            url=payload.url,
            page_type_hint=payload.page_type_hint,
            client_id=payload.client_id,
        )
        if inspect.isawaitable(raw):
            raw = await raw
        return BrowserFetchOutput.model_validate(raw or {})

    def fetch_browser_payload(self, payload: BrowserFetchInput) -> BrowserFetchOutput:
        """Synchronous helper for runtimes that execute skills in sync contexts."""
        return run_sync(
            partial(self.fetch_browser_payload_async, payload),
            label="fetch_browser_payload()",
        )
