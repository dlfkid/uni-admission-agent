"""Browser provider orchestration for crawl service."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

ClientAvailabilityFn = Callable[[Optional[str]], bool]
ClientFetchFn = Callable[..., Awaitable[dict[str, Any]]]

_dispatchers: dict[str, Optional[Callable[..., Any]]] = {
    "availability_fn": None,
    "fetch_fn": None,
}


def configure_client_dispatchers(
    *,
    availability_fn: Optional[ClientAvailabilityFn] = None,
    fetch_fn: Optional[ClientFetchFn] = None,
) -> None:
    """Configure client bridge callbacks used by provider resolution."""
    if availability_fn is not None:
        _dispatchers["availability_fn"] = availability_fn
    if fetch_fn is not None:
        _dispatchers["fetch_fn"] = fetch_fn


def has_available_client(preferred_client_id: Optional[str]) -> bool:
    """Whether there is a usable browser-automation client."""
    availability_fn = _dispatchers.get("availability_fn")
    if availability_fn is None:
        return False
    try:
        return bool(availability_fn(preferred_client_id))
    except Exception:
        logger.exception("Client availability check failed")
        return False


async def fetch_index_and_details_via_client(
    *,
    url: str,
    page_type_hint: str,
    client_id: Optional[str],
) -> dict[str, Any]:
    """Fetch browser-rendered payload from connected client."""
    fetch_fn = _dispatchers.get("fetch_fn")
    if fetch_fn is None:
        raise RuntimeError("Client bridge fetch handler is not configured")
    payload = await fetch_fn(
        url=url,
        page_type_hint=page_type_hint,
        client_id=client_id,
    )
    return dict(payload or {})


async def resolve_browser_inputs(
    *,
    url: str,
    page_type_hint: str,
    html_content: Optional[str],
    detail_pages_batch: Optional[list[dict[str, Any]]],
    browser_provider: str = "auto",
    client_id: Optional[str] = None,
    strict_client: bool = False,
) -> dict[str, Any]:
    """Resolve optional browser-rendered inputs before ingestion."""
    if html_content or detail_pages_batch:
        return {}

    provider = str(browser_provider or "auto").strip().lower() or "auto"
    if provider not in {"auto", "server", "client"}:
        provider = "auto"

    if provider == "server":
        return {}

    available = has_available_client(client_id)
    if not available:
        if provider == "client" or strict_client:
            raise RuntimeError("No available client for browser automation")
        return {}

    try:
        payload = await fetch_index_and_details_via_client(
            url=url,
            page_type_hint=page_type_hint,
            client_id=client_id,
        )
    except Exception as exc:
        if provider == "client" or strict_client:
            raise RuntimeError("Client browser automation failed") from exc
        logger.warning("Client browser automation failed; falling back to server mode: %s", exc)
        return {}

    return {
        key: value
        for key, value in payload.items()
        if key in {"html_content", "detail_pages_batch", "selected_urls", "selected_link_texts"}
    }
