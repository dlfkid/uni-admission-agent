"""Client automation bridge wrappers used by agent runtime skills."""

from __future__ import annotations

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
        """Synchronous helper for runtimes that execute skills in sync contexts.

        If the underlying fetch_fn is sync (e.g., _fetch_browser_payload_from_client_sync
        which schedules on the main event loop), calls it directly without creating a
        throwaway event loop.
        """
        raw = self._fetch_fn(
            url=payload.url,
            page_type_hint=payload.page_type_hint,
            client_id=payload.client_id,
        )
        if inspect.isawaitable(raw):
            # Async fetch_fn (e.g., tests or alternative providers)
            return run_sync(
                lambda: self._resolve_and_validate(raw),
                label="fetch_browser_payload()",
            )
        return BrowserFetchOutput.model_validate(raw or {})

    @staticmethod
    async def _resolve_and_validate(awaitable: Any) -> BrowserFetchOutput:
        raw = await awaitable
        return BrowserFetchOutput.model_validate(raw or {})
