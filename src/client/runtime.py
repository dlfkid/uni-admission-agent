"""Runtime helpers for ``adm-agent-client``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from src.client.config import ClientConfig


@dataclass
class ClientConnectivity:
    """Connectivity probe result."""

    connected: bool
    message: str
    endpoint: str


def build_server_endpoint(config: ClientConfig) -> str:
    """Build human-readable server endpoint string."""
    return f"{config.server_host}:{config.server_port}"


async def probe_server(
    config: ClientConfig,
    timeout_seconds: float = 3.0,
) -> ClientConnectivity:
    """Probe TCP reachability of configured serve endpoint."""
    endpoint = build_server_endpoint(config)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(config.server_host, int(config.server_port)),
            timeout=max(0.1, float(timeout_seconds)),
        )
        writer.close()
        await writer.wait_closed()
        del reader
        return ClientConnectivity(
            connected=True,
            message="reachable",
            endpoint=endpoint,
        )
    except Exception as exc:
        return ClientConnectivity(
            connected=False,
            message=str(exc),
            endpoint=endpoint,
        )


class ClientRuntime:
    """Lightweight runtime wrapper for client commands."""

    def __init__(self, config: ClientConfig) -> None:
        self.config = config

    async def start_once(self) -> ClientConnectivity:
        """One-shot start probe used by CLI start/status."""
        return await probe_server(self.config)

