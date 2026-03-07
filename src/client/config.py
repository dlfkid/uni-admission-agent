"""Config helpers for ``adm-agent-client``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
import uuid


CLIENT_HOME_DIR = ".adm-agent"
CLIENT_CONFIG_FILE = "client.toml"


@dataclass
class ClientConfig:
    """Client connection and identity settings."""

    server_host: str
    server_port: int
    client_name: str
    client_id: str
    workdir: str


def get_client_home() -> Path:
    """Return client config home directory."""
    return Path.home() / CLIENT_HOME_DIR


def get_client_config_path() -> Path:
    """Return the default client config path."""
    return get_client_home() / CLIENT_CONFIG_FILE


def ensure_client_id(value: str | None) -> str:
    """Return existing client id or generate a stable-looking short uuid."""
    text = str(value or "").strip()
    if text:
        return text
    return uuid.uuid4().hex[:16]


def save_client_config(config: ClientConfig) -> Path:
    """Persist config as TOML file."""
    path = get_client_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    def _esc(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    content = "\n".join(
        [
            f'server_host = "{_esc(config.server_host)}"',
            f"server_port = {int(config.server_port)}",
            f'client_name = "{_esc(config.client_name)}"',
            f'client_id = "{_esc(config.client_id)}"',
            f'workdir = "{_esc(config.workdir)}"',
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    return path


def load_client_config() -> ClientConfig | None:
    """Load client config from TOML file."""
    path = get_client_config_path()
    if not path.exists():
        return None

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return ClientConfig(
        server_host=str(data.get("server_host") or "127.0.0.1"),
        server_port=int(data.get("server_port") or 8910),
        client_name=str(data.get("client_name") or "adm-agent-client"),
        client_id=ensure_client_id(str(data.get("client_id") or "")),
        workdir=str(data.get("workdir") or str(Path.cwd())),
    )

