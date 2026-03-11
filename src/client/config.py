"""Config helpers for ``adm-agent-client``."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import tomllib
import uuid

from pydantic import BaseModel, Field, ValidationError


CLIENT_HOME_DIR = ".adm-agent"
CLIENT_CONFIG_FILE = "client.toml"
logger = logging.getLogger(__name__)


@dataclass
class ClientConfig:
    """Client connection and identity settings."""

    server_host: str
    server_port: int
    client_name: str
    client_id: str
    workdir: str
    policy_profile: "ClientPolicyProfile | None" = None


class ClientPolicyProfile(BaseModel):
    """Client-local policy profile sent with browser payload RPC responses."""

    auto_run_max_candidates: int = Field(default=10, ge=1, le=200)
    taxonomy_auto_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    taxonomy_keep_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    prefer_browser_provider: str = Field(default="auto")
    require_manual_review_when_low_confidence: bool = True
    llm_fallback_enabled: bool = True
    batch_size: int = Field(default=4, ge=1, le=50)
    detail_concurrency: int = Field(default=4, ge=1, le=20)


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

    lines = [
        f'server_host = "{_esc(config.server_host)}"',
        f"server_port = {int(config.server_port)}",
        f'client_name = "{_esc(config.client_name)}"',
        f'client_id = "{_esc(config.client_id)}"',
        f'workdir = "{_esc(config.workdir)}"',
    ]
    if config.policy_profile is not None:
        lines.append("")
        lines.append("[policy_profile]")
        for key, value in config.policy_profile.model_dump(mode="json").items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, (int, float)):
                rendered = str(value)
            else:
                rendered = f'"{_esc(str(value))}"'
            lines.append(f"{key} = {rendered}")
    lines.append("")
    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    return path


def load_client_config() -> ClientConfig | None:
    """Load client config from TOML file."""
    path = get_client_config_path()
    if not path.exists():
        return None

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    policy_raw = data.get("policy_profile")
    policy_profile = None
    if isinstance(policy_raw, dict):
        try:
            policy_profile = ClientPolicyProfile.model_validate(policy_raw)
        except ValidationError as exc:
            logger.warning("Invalid client policy_profile in %s, using defaults: %s", path, exc)
            policy_profile = ClientPolicyProfile()

    return ClientConfig(
        server_host=str(data.get("server_host") or "127.0.0.1"),
        server_port=int(data.get("server_port") or 8910),
        client_name=str(data.get("client_name") or "adm-agent-client"),
        client_id=ensure_client_id(str(data.get("client_id") or "")),
        workdir=str(data.get("workdir") or str(Path.cwd())),
        policy_profile=policy_profile,
    )
