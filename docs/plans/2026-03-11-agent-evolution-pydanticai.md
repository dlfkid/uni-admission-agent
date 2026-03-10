# PydanticAI Agent Evolution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an opt-in agent runtime powered by PydanticAI that orchestrates existing serve/MCP capabilities through bridge contracts, while preserving current stable behavior as the default path.

**Architecture:** Keep all existing crawl/analyze/persist logic as the execution core. Introduce a new runtime abstraction (`AgentRuntime`) and bridge layer (`ServeToolBridge` + `ClientAutomationBridge`) so the agent never directly manipulates DB internals. Route agent requests through feature flags and explicit commands; fall back to `LegacyRuntime` on runtime/schema/provider failures.

**Tech Stack:** Python 3.12, FastAPI, Typer, MCP, Pydantic v2, SQLModel, existing router providers, PydanticAI, pytest.

---

### Task 1: Add failing tests for agent feature flag default-off behavior

**Files:**
- Create: `tests/test_agent_feature_flag.py`
- Modify: `src/api/server.py`
- Modify: `src/cmd/cli.py`

**Step 1: Write failing test for server default behavior (agent disabled)**

```python
def test_agent_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_ENABLED", raising=False)
    from src.api.server import is_agent_enabled
    assert is_agent_enabled() is False
```

**Step 2: Write failing test for `serve --agent` override**

```python
def test_serve_agent_flag_enables_runtime(cli_runner):
    result = cli_runner.invoke(app, ["serve", "--agent", "--dry-run"])
    assert result.exit_code == 0
    assert "agent enabled" in result.stdout.lower()
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_feature_flag.py -v`  
Expected: FAIL (`is_agent_enabled` / flag plumbing not implemented).

**Step 4: Implement minimal feature-flag helpers**

```python
def is_agent_enabled(explicit_flag: bool | None = None) -> bool:
    if explicit_flag is not None:
        return bool(explicit_flag)
    return str(os.getenv("AGENT_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}
```

**Step 5: Re-run tests and commit**

Run: `uv run pytest tests/test_agent_feature_flag.py -v`  
Expected: PASS.

```bash
git add tests/test_agent_feature_flag.py src/api/server.py src/cmd/cli.py
git commit -m "feat(agent): add explicit feature flag with default-off behavior"
```

### Task 2: Add failing tests for runtime abstraction and factory selection

**Files:**
- Create: `tests/test_agent_runtime_factory.py`
- Create: `src/agent_runtime/base.py`
- Create: `src/agent_runtime/runtime_factory.py`

**Step 1: Write failing tests for runtime resolution**

```python
def test_factory_returns_legacy_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    runtime = build_agent_runtime(...)
    assert runtime.name == "legacy"


def test_factory_returns_pydanticai_when_configured(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "pydanticai")
    runtime = build_agent_runtime(...)
    assert runtime.name == "pydanticai"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_runtime_factory.py -v`  
Expected: FAIL.

**Step 3: Implement runtime interface and factory**

```python
class AgentRuntime(Protocol):
    name: str
    async def run(self, request: AgentRequest) -> AgentResponse: ...
```

```python
def build_agent_runtime(config, bridge, model_adapter):
    mode = str(config.runtime).lower()
    if mode == "pydanticai":
        return PydanticAIRuntime(...)
    return LegacyRuntime(...)
```

**Step 4: Re-run tests and commit**

Run: `uv run pytest tests/test_agent_runtime_factory.py -v`  
Expected: PASS.

```bash
git add tests/test_agent_runtime_factory.py src/agent_runtime/base.py src/agent_runtime/runtime_factory.py
git commit -m "feat(agent): add runtime abstraction and factory selection"
```

### Task 3: Add failing tests for bridge contracts (serve + client automation)

**Files:**
- Create: `tests/test_agent_bridge_contracts.py`
- Create: `src/agent_bridge/contracts.py`
- Create: `src/agent_bridge/serve_tool_bridge.py`
- Create: `src/agent_bridge/client_automation_bridge.py`

**Step 1: Write failing contract tests for typed input/output**

```python
def test_serve_tool_bridge_analyze_contract(monkeypatch):
    bridge = ServeToolBridge(...)
    output = bridge.analyze_page(AnalyzeInput(url="https://x", page_type_hint="auto"))
    assert isinstance(output, AnalyzeOutput)


def test_client_automation_bridge_fetch_contract(monkeypatch):
    bridge = ClientAutomationBridge(...)
    output = bridge.fetch_browser_payload(BrowserFetchInput(url="https://x", page_type_hint="index"))
    assert output.html_content is not None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_bridge_contracts.py -v`  
Expected: FAIL.

**Step 3: Implement minimal bridge contracts + wrappers**

```python
class AnalyzeInput(BaseModel): ...
class AnalyzeOutput(BaseModel): ...
```

```python
class ServeToolBridge:
    def analyze_page(self, payload: AnalyzeInput) -> AnalyzeOutput:
        raw = analyze_page_external(...)
        return AnalyzeOutput.model_validate(raw)
```

**Step 4: Re-run tests and commit**

Run: `uv run pytest tests/test_agent_bridge_contracts.py -v`  
Expected: PASS.

```bash
git add tests/test_agent_bridge_contracts.py src/agent_bridge/contracts.py src/agent_bridge/serve_tool_bridge.py src/agent_bridge/client_automation_bridge.py
git commit -m "feat(agent-bridge): add typed bridge contracts for serve and client automation"
```

### Task 4: Add failing tests for skill registry and skill execution contracts

**Files:**
- Create: `tests/test_agent_skill_registry.py`
- Create: `src/agent_runtime/skills/contracts.py`
- Create: `src/agent_runtime/skills/registry.py`
- Create: `src/agent_runtime/skills/impl/*.py`

**Step 1: Write failing tests for required skills presence**

```python
def test_required_skills_registered():
    registry = build_skill_registry(...)
    assert "analyze_page_skill" in registry
    assert "crawl_detail_batch_skill" in registry
```

**Step 2: Write failing tests for schema validation**

```python
def test_skill_input_validation_errors_on_bad_payload(registry):
    with pytest.raises(ValidationError):
        registry.execute("analyze_page_skill", {"url": ""})
```

**Step 3: Run tests to verify fail**

Run: `uv run pytest tests/test_agent_skill_registry.py -v`  
Expected: FAIL.

**Step 4: Implement registry and first skill wrappers**

```python
class SkillDef(BaseModel):
    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
```

```python
def execute(self, name: str, payload: dict) -> dict:
    model_in = skill.input_model.model_validate(payload)
    raw = skill.handler(model_in)
    return skill.output_model.model_validate(raw).model_dump(mode="json")
```

**Step 5: Re-run tests and commit**

Run: `uv run pytest tests/test_agent_skill_registry.py -v`  
Expected: PASS.

```bash
git add tests/test_agent_skill_registry.py src/agent_runtime/skills/
git commit -m "feat(agent): add typed skill registry and core skill wrappers"
```

### Task 5: Add failing tests for policy profile precedence and normalization

**Files:**
- Create: `tests/test_policy_profile_precedence.py`
- Create: `src/agent_runtime/policy.py`
- Modify: `src/api/schemas.py`

**Step 1: Write failing tests for precedence**

```python
def test_policy_precedence_request_over_client_over_server_defaults():
    merged = merge_policy(request_overrides={"batch_size": 3}, client_policy={"batch_size": 2}, server_defaults={"batch_size": 1})
    assert merged.batch_size == 3
```

**Step 2: Write failing tests for invalid values normalization**

```python
def test_policy_normalization_clamps_thresholds():
    merged = merge_policy(request_overrides={"taxonomy_auto_threshold": 1.5}, ...)
    assert merged.taxonomy_auto_threshold == 1.0
    assert merged.warnings
```

**Step 3: Run tests to verify fail**

Run: `uv run pytest tests/test_policy_profile_precedence.py -v`  
Expected: FAIL.

**Step 4: Implement policy models and merge/normalize logic**

```python
class PolicyProfile(BaseModel):
    auto_run_max_candidates: int = Field(default=10, ge=1, le=200)
    taxonomy_auto_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
```

```python
def merge_policy(*, request_overrides, client_policy, server_defaults):
    merged = {**server_defaults, **client_policy, **request_overrides}
    model = PolicyProfile.model_validate(merged)
    return PolicyMergeResult(profile=model, warnings=warnings)
```

**Step 5: Re-run tests and commit**

Run: `uv run pytest tests/test_policy_profile_precedence.py -v`  
Expected: PASS.

```bash
git add tests/test_policy_profile_precedence.py src/agent_runtime/policy.py src/api/schemas.py
git commit -m "feat(agent-policy): add request/client/server precedence with normalization"
```

### Task 6: Add failing tests for model provider adapter (internal/external)

**Files:**
- Create: `tests/test_agent_model_provider_adapter.py`
- Create: `src/agent_runtime/model_provider.py`
- Modify: `src/agents/factory.py`

**Step 1: Write failing tests for provider mode selection**

```python
def test_model_provider_uses_internal_router_when_enabled(monkeypatch):
    adapter = ModelProviderAdapter(allow_internal=True, allow_external=False)
    client = adapter.resolve(mode="internal")
    assert client.mode == "internal"


def test_model_provider_uses_external_client_context(monkeypatch):
    adapter = ModelProviderAdapter(allow_internal=False, allow_external=True)
    client = adapter.resolve(mode="external", external_context={"session_id": "abc"})
    assert client.mode == "external"
```

**Step 2: Run tests to verify fail**

Run: `uv run pytest tests/test_agent_model_provider_adapter.py -v`  
Expected: FAIL.

**Step 3: Implement adapter and explicit errors**

```python
if mode == "internal" and not self.allow_internal:
    raise AgentConfigError("internal model disabled")
```

**Step 4: Re-run tests and commit**

Run: `uv run pytest tests/test_agent_model_provider_adapter.py -v`  
Expected: PASS.

```bash
git add tests/test_agent_model_provider_adapter.py src/agent_runtime/model_provider.py src/agents/factory.py
git commit -m "feat(agent-llm): add internal/external model provider adapter"
```

### Task 7: Add failing tests for PydanticAI runtime execution and legacy fallback

**Files:**
- Create: `tests/test_agent_runtime_fallback.py`
- Create: `src/agent_runtime/pydanticai_runtime.py`
- Create: `src/agent_runtime/legacy_runtime.py`

**Step 1: Write failing test for successful PydanticAI run**

```python
@pytest.mark.asyncio
async def test_pydanticai_runtime_executes_skill_plan(monkeypatch):
    runtime = PydanticAIRuntime(...)
    result = await runtime.run(AgentRequest(...))
    assert result.status == "done"
    assert result.trace
```

**Step 2: Write failing test for automatic fallback on runtime failure**

```python
@pytest.mark.asyncio
async def test_runtime_falls_back_to_legacy_when_pydanticai_errors(monkeypatch):
    runtime = PydanticAIRuntime(..., fallback_runtime=LegacyRuntime(...))
    monkeypatch.setattr(runtime, "_run_agent", failing)
    result = await runtime.run(AgentRequest(...))
    assert result.runtime_used == "legacy"
```

**Step 3: Run tests to verify fail**

Run: `uv run pytest tests/test_agent_runtime_fallback.py -v`  
Expected: FAIL.

**Step 4: Implement runtime skeleton and fallback path**

```python
try:
    return await self._run_agent(request)
except Exception as exc:
    logger.warning("pydanticai runtime failed, falling back: %s", exc)
    return await self.fallback_runtime.run(request)
```

**Step 5: Re-run tests and commit**

Run: `uv run pytest tests/test_agent_runtime_fallback.py -v`  
Expected: PASS.

```bash
git add tests/test_agent_runtime_fallback.py src/agent_runtime/pydanticai_runtime.py src/agent_runtime/legacy_runtime.py
git commit -m "feat(agent-runtime): add pydanticai runtime with automatic legacy fallback"
```

### Task 8: Integrate agent entrypoints into REST/MCP without breaking existing tools

**Files:**
- Modify: `src/api/server.py`
- Modify: `src/services/crawler.py`
- Modify: `src/api/schemas.py`
- Modify: `tests/test_mcp_tool_registration_modes.py`
- Create: `tests/test_agent_api_entrypoints.py`

**Step 1: Add failing API tests for new entrypoints**

```python
async def test_agent_run_endpoint_disabled_returns_409(...):
    ...

async def test_agent_run_endpoint_enabled_returns_task_id(...):
    ...
```

**Step 2: Add failing MCP registration test for optional agent tools**

```python
def test_agent_tools_registered_only_when_agent_enabled(...):
    ...
```

**Step 3: Run tests to verify fail**

Run: `uv run pytest tests/test_agent_api_entrypoints.py tests/test_mcp_tool_registration_modes.py -v`  
Expected: FAIL.

**Step 4: Implement non-breaking integration**

```python
if agent_enabled:
    mcp.tool(name="agent_run")(agent_run_handler)
```

**Step 5: Re-run tests and commit**

Run: `uv run pytest tests/test_agent_api_entrypoints.py tests/test_mcp_tool_registration_modes.py -v`  
Expected: PASS.

```bash
git add src/api/server.py src/services/crawler.py src/api/schemas.py tests/test_agent_api_entrypoints.py tests/test_mcp_tool_registration_modes.py
git commit -m "feat(agent-api): add opt-in agent REST/MCP entrypoints without changing default tools"
```

### Task 9: Add client policy profile transport tests and plumbing

**Files:**
- Modify: `src/cmd/client_cli.py`
- Modify: `src/client/config.py`
- Modify: `src/client/runtime.py`
- Create: `tests/test_client_policy_profile.py`

**Step 1: Write failing tests for client-local profile load/serialize**

```python
def test_client_policy_profile_saved_and_loaded(tmp_path, monkeypatch):
    ...
```

**Step 2: Write failing tests for request payload includes policy**

```python
def test_client_runtime_embeds_policy_in_rpc_payload(monkeypatch):
    ...
```

**Step 3: Run tests to verify fail**

Run: `uv run pytest tests/test_client_policy_profile.py -v`  
Expected: FAIL.

**Step 4: Implement profile storage + payload propagation**

```python
class ClientPolicyProfile(BaseModel):
    ...
```

```python
payload["policy_profile"] = config.policy_profile.model_dump(mode="json")
```

**Step 5: Re-run tests and commit**

Run: `uv run pytest tests/test_client_policy_profile.py -v`  
Expected: PASS.

```bash
git add src/cmd/client_cli.py src/client/config.py src/client/runtime.py tests/test_client_policy_profile.py
git commit -m "feat(client): add local policy profile and request transport"
```

### Task 10: Full regression verification + docs updates

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_CONTEXT.md`
- Modify: `change_log.md`

**Step 1: Add docs for agent modes and safety model**

```markdown
- Agent is disabled by default.
- Enable with `serve --agent`.
- Runtime: legacy|pydanticai.
- Fallback behavior and policy profile precedence.
```

**Step 2: Run targeted full test suite for touched domains**

Run:
`uv run pytest tests/test_agent_feature_flag.py tests/test_agent_runtime_factory.py tests/test_agent_bridge_contracts.py tests/test_agent_skill_registry.py tests/test_policy_profile_precedence.py tests/test_agent_model_provider_adapter.py tests/test_agent_runtime_fallback.py tests/test_agent_api_entrypoints.py tests/test_client_policy_profile.py tests/test_mcp_tool_registration_modes.py -v`

Expected: PASS.

**Step 3: Run lint gate**

Run: `uv run pylint $(git ls-files '*.py')`  
Expected: exit code 0.

**Step 4: Run critical backward-compatibility regression**

Run: `uv run pytest tests/test_crawler_service_phase2.py tests/test_api_crawl_browser_provider.py tests/test_mcp_runtime_status.py tests/test_taxonomy_name_resolution.py -v`  
Expected: PASS.

**Step 5: Commit docs and verification artifacts**

```bash
git add README.md PROJECT_CONTEXT.md change_log.md
git commit -m "docs(agent): document opt-in pydanticai runtime and policy profile behavior"
```
