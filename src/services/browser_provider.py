"""Browser provider orchestration for crawl service."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

ClientAvailabilityFn = Callable[[Optional[str]], bool]
ClientFetchFn = Callable[..., Awaitable[dict[str, Any]]]
ClientSelectFn = Callable[[Optional[str]], Optional[str]]

_dispatchers: dict[str, Optional[Callable[..., Any]]] = {
    "availability_fn": None,
    "fetch_fn": None,
    "select_client_fn": None,
}


def configure_client_dispatchers(
    *,
    availability_fn: Optional[ClientAvailabilityFn] = None,
    fetch_fn: Optional[ClientFetchFn] = None,
    select_client_fn: Optional[ClientSelectFn] = None,
) -> None:
    """Configure client bridge callbacks used by provider resolution."""
    if availability_fn is not None:
        _dispatchers["availability_fn"] = availability_fn
    if fetch_fn is not None:
        _dispatchers["fetch_fn"] = fetch_fn
    if select_client_fn is not None:
        _dispatchers["select_client_fn"] = select_client_fn


def _select_client_id(preferred_client_id: Optional[str]) -> Optional[str]:
    select_client_fn = _dispatchers.get("select_client_fn")
    if select_client_fn is not None:
        try:
            selected = select_client_fn(preferred_client_id)
            value = str(selected or "").strip()
            return value or None
        except Exception:
            logger.exception("Client selection failed")
            return None
    return None


def has_available_client(preferred_client_id: Optional[str]) -> bool:
    """Whether there is a usable browser-automation client."""
    selected_client_id = _select_client_id(preferred_client_id)
    if selected_client_id:
        return True

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


def resolve_provider_metadata(
    *,
    browser_provider: str = "auto",
    client_id: Optional[str] = None,
    strict_client: bool = False,
) -> dict[str, Any]:
    """Resolve browser provider + selected client metadata without fetching."""
    provider = str(browser_provider or "auto").strip().lower() or "auto"
    if provider not in {"auto", "server", "client"}:
        provider = "auto"

    selected_client_id = _select_client_id(client_id)
    if provider == "server":
        return {
            "resolved_browser_provider": "server",
            "client_id_used": None,
        }

    if selected_client_id:
        return {
            "resolved_browser_provider": "client",
            "client_id_used": selected_client_id,
        }

    if has_available_client(client_id):
        fallback_client_id = str(client_id or "").strip() or None
        return {
            "resolved_browser_provider": "client",
            "client_id_used": fallback_client_id,
        }

    if provider == "client" or strict_client:
        raise RuntimeError("No available client for browser automation")

    return {
        "resolved_browser_provider": "server",
        "client_id_used": None,
    }


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
        return {
            "resolved_browser_provider": "server",
            "client_id_used": None,
        }

    metadata = resolve_provider_metadata(
        browser_provider=browser_provider,
        client_id=client_id,
        strict_client=strict_client,
    )
    if metadata["resolved_browser_provider"] != "client":
        return metadata

    try:
        payload = await fetch_index_and_details_via_client(
            url=url,
            page_type_hint=page_type_hint,
            client_id=metadata["client_id_used"],
        )
    except Exception as exc:
        provider = str(browser_provider or "auto").strip().lower() or "auto"
        if provider == "client" or strict_client:
            raise RuntimeError("Client browser automation failed") from exc
        logger.warning("Client browser automation failed; falling back to server mode: %s", exc)
        return {
            "resolved_browser_provider": "server",
            "client_id_used": None,
        }

    response = {
        key: value
        for key, value in payload.items()
        if key in {"html_content", "detail_pages_batch", "selected_urls", "selected_link_texts"}
    }
    response.update(metadata)
    return response
