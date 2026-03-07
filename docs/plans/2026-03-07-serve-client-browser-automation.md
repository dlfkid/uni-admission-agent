# Serve-Client Browser Automation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a unified `crawl` execution mode where `serve` can dispatch browser automation to a connected user-side `adm-agent-client` (local or remote), while keeping extension optional and preserving fallback to server Playwright.

**Architecture:** Introduce a long-lived client bridge channel (`serve` WebSocket hub + client daemon), then add browser provider orchestration in `crawl_url` with `auto|server|client` semantics. In client mode, `serve` first requests browser-rendered index HTML, then (for index pages) requests detail-page HTML batches and reuses existing `detail_pages_batch` ingestion path. Keep API/MCP/CLI entrypoint unchanged; only extend optional request parameters and add `/clients` observability endpoints.

**Tech Stack:** FastAPI (REST + WebSocket), Pydantic v2, Typer CLI, asyncio task orchestration, existing ingestion pipeline, pytest/pytest-asyncio, PyInstaller build pipeline.

---

### Task 1: Add schema coverage for browser provider fields

**Files:**
- Create: `tests/test_api_browser_provider_schema.py`
- Modify: `src/api/schemas.py`

**Step 1: Write failing schema tests for new fields**

```python
from src.api.schemas import CrawlRequest


def test_crawl_request_accepts_browser_provider_fields() -> None:
    model = CrawlRequest.model_validate(
        {
            "url": "https://example.edu/programmes",
            "univ_slug": "manchester",
            "year": 2026,
            "browser_provider": "client",
            "client_id": "client-123",
            "strict_client": True,
        }
    )
    assert model.browser_provider == "client"
    assert model.client_id == "client-123"
    assert model.strict_client is True


def test_crawl_request_defaults_browser_provider_auto() -> None:
    model = CrawlRequest.model_validate(
        {
            "url": "https://example.edu/programmes",
            "univ_slug": "manchester",
            "year": 2026,
        }
    )
    assert model.browser_provider == "auto"
    assert model.strict_client is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_browser_provider_schema.py -v`  
Expected: FAIL (`CrawlRequest` missing fields).

**Step 3: Add minimal schema fields + validation**

```python
class CrawlRequest(BaseModel):
    ...
    browser_provider: str = Field(default="auto")
    client_id: Optional[str] = Field(default=None)
    strict_client: bool = Field(default=False)

    @field_validator("browser_provider")
    @classmethod
    def _validate_browser_provider(cls, value: str) -> str:
        allowed = {"auto", "server", "client"}
        normalized = str(value or "").strip().lower()
        if normalized not in allowed:
            raise ValueError("browser_provider must be auto/server/client")
        return normalized
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_browser_provider_schema.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_api_browser_provider_schema.py src/api/schemas.py
git commit -m "test(api): cover crawl browser provider schema fields"
```

### Task 2: Build client bridge registry and RPC primitives

**Files:**
- Create: `src/services/client_bridge.py`
- Create: `tests/test_client_bridge.py`

**Step 1: Write failing tests for registry lifecycle + client selection**

```python
from src.services.client_bridge import ClientRegistry, ClientSession


def test_registry_register_and_list_clients() -> None:
    registry = ClientRegistry()
    registry.register(
        ClientSession(
            client_id="c1",
            client_name="Rayne-Mac",
            platform="darwin",
            arch="arm64",
            workdir="/Users/rayne",
            capabilities={"browser_automation": True},
        )
    )
    rows = registry.list_clients()
    assert len(rows) == 1
    assert rows[0]["client_id"] == "c1"


def test_registry_prefers_recent_active_client() -> None:
    registry = ClientRegistry()
    ...
    assert registry.select_client_id(preferred_client_id=None) == "c2"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client_bridge.py -v`  
Expected: FAIL (`client_bridge` module not found).

**Step 3: Implement registry + pending RPC waiter map**

```python
@dataclass
class ClientSession:
    client_id: str
    client_name: str
    platform: str
    arch: str
    workdir: str
    capabilities: dict[str, Any]
    last_seen_epoch: float = field(default_factory=time.time)


class ClientRegistry:
    def register(self, session: ClientSession) -> None: ...
    def heartbeat(self, client_id: str) -> None: ...
    def remove(self, client_id: str) -> None: ...
    def list_clients(self) -> list[dict[str, Any]]: ...
    def select_client_id(self, preferred_client_id: str | None) -> str | None: ...
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client_bridge.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/services/client_bridge.py tests/test_client_bridge.py
git commit -m "feat(server): add client bridge registry primitives"
```

### Task 3: Add `serve` client channel endpoints (`/clients`, `/clients/ws`)

**Files:**
- Create: `tests/test_api_clients_channel.py`
- Modify: `src/api/server.py`
- Modify: `src/api/schemas.py`

**Step 1: Write failing API tests for registration and listing**

```python
from fastapi.testclient import TestClient
from src.api.server import app


def test_clients_endpoint_lists_registered_ws_client() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/clients/ws") as ws:
            ws.send_json(
                {
                    "type": "register",
                    "client_id": "c1",
                    "client_name": "Rayne-Mac",
                    "platform": "darwin",
                    "arch": "arm64",
                    "workdir": "/Users/rayne",
                    "capabilities": {"browser_automation": True},
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "registered"
            data = client.get("/clients").json()
            assert any(row["client_id"] == "c1" for row in data)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_clients_channel.py -v`  
Expected: FAIL (`/clients` or `/clients/ws` missing).

**Step 3: Implement WebSocket hub and client list endpoints**

```python
@app.websocket("/clients/ws")
async def ws_clients(websocket: WebSocket) -> None:
    await websocket.accept()
    # register -> heartbeat -> rpc response relay


@app.get("/clients", response_model=list[ClientInfoResponse])
async def api_list_clients() -> list[ClientInfoResponse]:
    return [ClientInfoResponse(**row) for row in client_registry.list_clients()]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_clients_channel.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_api_clients_channel.py src/api/server.py src/api/schemas.py
git commit -m "feat(api): add client ws bridge and clients status endpoints"
```

### Task 4: Implement RPC dispatch from server to connected client

**Files:**
- Modify: `src/services/client_bridge.py`
- Create: `tests/test_client_bridge_rpc.py`

**Step 1: Write failing async tests for request/response correlation + timeout**

```python
import pytest
from src.services.client_bridge import ClientRpcBroker, ClientUnavailableError


@pytest.mark.asyncio
async def test_rpc_broker_resolves_response_by_request_id() -> None:
    broker = ClientRpcBroker(timeout_seconds=0.1)
    req_id, fut = broker.create_pending("c1")
    broker.resolve(req_id, {"html": "<html/>"})
    payload = await fut
    assert payload["html"] == "<html/>"


@pytest.mark.asyncio
async def test_rpc_broker_times_out() -> None:
    broker = ClientRpcBroker(timeout_seconds=0.01)
    with pytest.raises(ClientUnavailableError):
        await broker.wait_for_response("missing")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client_bridge_rpc.py -v`  
Expected: FAIL.

**Step 3: Add broker methods used by server and crawler orchestration**

```python
class ClientRpcBroker:
    def create_pending(self, client_id: str) -> tuple[str, asyncio.Future]: ...
    async def wait_for_response(self, request_id: str) -> dict[str, Any]: ...
    def resolve(self, request_id: str, payload: dict[str, Any]) -> None: ...
    def fail(self, request_id: str, message: str) -> None: ...
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client_bridge_rpc.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/services/client_bridge.py tests/test_client_bridge_rpc.py
git commit -m "feat(server): add client rpc broker with timeout handling"
```

### Task 5: Add browser-provider orchestration in `crawl_url`

**Files:**
- Create: `src/services/browser_provider.py`
- Modify: `src/services/crawler.py`
- Create: `tests/test_crawler_browser_provider.py`

**Step 1: Write failing service tests for `auto|client|server` behavior**

```python
import pytest
from src.services.crawler import crawl_url


@pytest.mark.asyncio
async def test_crawl_url_auto_uses_client_when_available(monkeypatch) -> None:
    monkeypatch.setattr("src.services.browser_provider.has_available_client", lambda *_: True)
    monkeypatch.setattr("src.services.browser_provider.fetch_index_and_details_via_client", lambda **_: {
        "html_content": None,
        "detail_pages_batch": [{"url": "https://d/1", "html_content": "<html>d1</html>"}],
    })
    ...
    result = await crawl_url(..., browser_provider="auto")
    assert result.imported_count >= 0


@pytest.mark.asyncio
async def test_crawl_url_strict_client_raises_when_client_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("src.services.browser_provider.has_available_client", lambda *_: False)
    with pytest.raises(RuntimeError, match="No available client"):
        await crawl_url(..., browser_provider="client", strict_client=True)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawler_browser_provider.py -v`  
Expected: FAIL (`crawl_url` missing new args / provider logic).

**Step 3: Implement orchestration helper + fallback behavior**

```python
async def resolve_browser_inputs(...):
    # returns {"html_content": ..., "detail_pages_batch": ...}
    # client mode: fetch index html; if index then fetch detail batch


async def crawl_url(..., browser_provider: str = "auto", client_id: Optional[str] = None, strict_client: bool = False, ...):
    resolved = await resolve_browser_inputs(...)
    html_content = resolved.get("html_content", html_content)
    detail_pages_batch = resolved.get("detail_pages_batch", detail_pages_batch)
    ...
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawler_browser_provider.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/services/browser_provider.py src/services/crawler.py tests/test_crawler_browser_provider.py
git commit -m "feat(crawl): add auto/client/server browser provider orchestration"
```

### Task 6: Wire provider fields through REST, MCP, and CLI

**Files:**
- Modify: `src/api/server.py`
- Modify: `src/api/schemas.py`
- Modify: `src/cmd/cli.py`
- Create: `tests/test_api_crawl_browser_provider.py`
- Create: `tests/test_cli_crawl_browser_provider.py`

**Step 1: Write failing API plumbing test**

```python
from fastapi.testclient import TestClient
from src.api.server import app


def test_api_crawl_passes_browser_provider_args(monkeypatch) -> None:
    captured = {}

    async def fake_crawl_url(**kwargs):
        captured.update(kwargs)
        class _R:
            def model_dump(self):
                return {"imported_count": 0, "univ_slug": "u", "year": 2026}
        return _R()

    monkeypatch.setattr("src.api.server.crawl_url", fake_crawl_url)
    with TestClient(app) as client:
        res = client.post("/crawl", json={
            "url": "https://example.edu",
            "univ_slug": "u",
            "year": 2026,
            "browser_provider": "client",
            "client_id": "c1",
            "strict_client": True,
        })
    assert res.status_code == 200
```

**Step 2: Write failing CLI plumbing test**

```python
from typer.testing import CliRunner
from src.cmd import cli


def test_cli_crawl_passes_browser_provider(monkeypatch) -> None:
    ...
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_crawl_browser_provider.py tests/test_cli_crawl_browser_provider.py -v`  
Expected: FAIL.

**Step 4: Implement plumbing in API/MCP/CLI**

```python
# src/api/server.py
result = await crawl_url(..., browser_provider=body.browser_provider, client_id=body.client_id, strict_client=body.strict_client)

# src/cmd/cli.py
def crawl(..., browser_provider: str = typer.Option("auto"), client_id: str = typer.Option(None), strict_client: bool = typer.Option(False)):
    ...

# mcp_crawl signature
async def mcp_crawl(..., browser_provider: str = "auto", client_id: Optional[str] = None, strict_client: bool = False) -> dict:
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_crawl_browser_provider.py tests/test_cli_crawl_browser_provider.py -v`  
Expected: PASS.

**Step 6: Commit**

```bash
git add src/api/server.py src/api/schemas.py src/cmd/cli.py tests/test_api_crawl_browser_provider.py tests/test_cli_crawl_browser_provider.py
git commit -m "feat(interface): plumb browser provider args across api mcp cli"
```

### Task 7: Implement `adm-agent-client` CLI/runtime (init/start/status)

**Files:**
- Create: `src/client/config.py`
- Create: `src/client/runtime.py`
- Create: `src/cmd/client_cli.py`
- Create: `tests/test_client_cli.py`

**Step 1: Write failing CLI tests for init/start/status config flow**

```python
from typer.testing import CliRunner
from src.cmd.client_cli import app


def test_client_init_writes_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["init"], input="127.0.0.1\n8910\nRayne-Mac\n")
    assert result.exit_code == 0
    assert (tmp_path / ".adm-agent" / "client.toml").exists()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_client_cli.py -v`  
Expected: FAIL (`client_cli` not found).

**Step 3: Implement minimal client runtime + commands**

```python
@app.command()
def init() -> None: ...

@app.command()
def start() -> None: ...

@app.command()
def status() -> None: ...
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_client_cli.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/client/config.py src/client/runtime.py src/cmd/client_cli.py tests/test_client_cli.py
git commit -m "feat(client): add daemon cli with init start status"
```

### Task 8: Add bootstrap prompt generation (Codex/Claude/OpenClaw/Generic)

**Files:**
- Create: `src/client/bootstrap_prompt.py`
- Modify: `src/cmd/client_cli.py`
- Create: `tests/test_client_bootstrap_prompt.py`

**Step 1: Write failing tests for prompt target selection**

```python
from src.client.bootstrap_prompt import build_bootstrap_prompt


def test_bootstrap_prompt_supports_openclaw_target() -> None:
    prompt = build_bootstrap_prompt(target="openclaw", host="127.0.0.1", port=8910)
    assert "OpenClaw" in prompt
    assert "init" in prompt
    assert "start" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_client_bootstrap_prompt.py -v`  
Expected: FAIL.

**Step 3: Implement prompt builder + CLI command**

```python
@app.command("bootstrap")
def bootstrap(target: str = typer.Option("generic", "--target"), emit_prompt: bool = typer.Option(False, "--emit-prompt")) -> None:
    ...
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_client_bootstrap_prompt.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/client/bootstrap_prompt.py src/cmd/client_cli.py tests/test_client_bootstrap_prompt.py
git commit -m "feat(client): add llm bootstrap prompt templates including openclaw"
```

### Task 9: Extend build pipeline for `adm-agent-client` binary

**Files:**
- Create: `adm-agent-client.spec`
- Modify: `scripts/build_dist.py`
- Modify: `README.md`

**Step 1: Add failing packaging smoke check (script-level)**

```python
# tests/test_build_dist_client_flags.py
# verify parser accepts --client-only and --skip-client-build combinations
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_dist_client_flags.py -v`  
Expected: FAIL.

**Step 3: Implement client build switches and release artifact naming**

```python
parser.add_argument("--client-only", action="store_true")
...
CLIENT_NAME = "adm-agent-client"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_build_dist_client_flags.py -v`  
Expected: PASS.

**Step 5: Manual packaging verification**

Run: `uv run python scripts/build_dist.py --client-only`  
Expected: release folder contains platform-specific `adm-agent-client` artifact.

**Step 6: Commit**

```bash
git add adm-agent-client.spec scripts/build_dist.py tests/test_build_dist_client_flags.py README.md
git commit -m "build: package adm-agent-client binary for all target platforms"
```

### Task 10: Document permissions, setup, and operational flow

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_CONTEXT.md`
- Modify: `change_log.md`
- Modify: `docs/plans/2026-03-07-serve-client-browser-automation-design.md`

**Step 1: Update README with user-safe setup walkthrough**

Add sections:
- `adm-agent-client` quickstart (`init`, `start`, `status`)
- macOS Gatekeeper/quarantine command
- Windows SmartScreen first-run note
- Linux executable permission note
- Example `crawl` with `browser_provider=client`
- `/clients` diagnostics endpoint

**Step 2: Add LLM-assisted bootstrap examples**

Include examples for:
- Codex CLI
- Claude Code
- OpenClaw (interaction-mode agnostic)

**Step 3: Update project context + changelog**

Summarize architecture decisions, fallback semantics, and extension-optional conclusion.

**Step 4: Run focused regression tests**

Run: `uv run pytest tests/test_api_browser_provider_schema.py tests/test_client_bridge.py tests/test_api_clients_channel.py tests/test_client_bridge_rpc.py tests/test_crawler_browser_provider.py tests/test_api_crawl_browser_provider.py tests/test_cli_crawl_browser_provider.py tests/test_client_cli.py tests/test_client_bootstrap_prompt.py tests/test_build_dist_client_flags.py -v`  
Expected: PASS.

**Step 5: Run full project quality gates**

Run: `uv run pytest`  
Expected: PASS.

Run: `uv run pylint $(git ls-files '*.py')`  
Expected: exit code 0.

**Step 6: Commit**

```bash
git add README.md PROJECT_CONTEXT.md change_log.md docs/plans/2026-03-07-serve-client-browser-automation-design.md
git commit -m "docs: add serve-client automation setup permissions and llm bootstrap guides"
```
