# Agent Streaming Output Design

## Background
The current agent mode in `uni-admission-agent` is not truly streaming from the user's point of view.

- `POST /agent/run` returns a background `task_id`, then the caller polls `/tasks/{id}`.
- `PydanticAIRuntime` waits for the whole `agent_loop(...)` to finish before returning an `AgentResponse`.
- The core agent loop calls `client.chat.completions.create(...)` without `stream=True`.
- Structured LLM paths such as cleaner extraction, page-type classification, and program-name resolution all wait for full JSON before continuing.

This means users eventually get progress through logs and polling, but they do not get a real-time sense that the agent is actively thinking and moving forward. That creates unnecessary waiting anxiety.

## Goal
Reduce waiting anxiety in agent mode by introducing streaming output where it provides the most value, without destabilizing structured extraction and persistence flows.

## Non-Goals
- Do not convert every LLM call in the project to token streaming.
- Do not change the semantics of `RouterAgent.generate()`.
- Do not make cleaner extraction / page-type classification / program-name resolution token-streamed.
- Do not break existing REST, MCP, crawler, or ingestion behavior.

## Decision
Adopt **Approach B**:

1. Add **event streaming** for the full agent execution lifecycle.
2. Add **token streaming only for the final user-visible summary**.
3. Keep all structured JSON extraction paths **non-streaming**.

This gives the project the biggest UX improvement with the smallest regression risk.

## Current-State Analysis

### 1. Agent runtime output path
- `src/api/server.py:/agent/run` creates a background task and returns `task_id`.
- `src/services/crawler.py:run_agent_crawl()` waits for the runtime response.
- `src/agent_runtime/pydanticai_runtime.py` waits for `agent_loop(...)` to finish, then builds a single `AgentResponse`.

### 2. Agent loop LLM call path
- `src/agent_runtime/loop.py` uses `AsyncOpenAI.chat.completions.create(...)` with tools.
- The loop consumes the completed message and then dispatches tool calls.
- There is no current token-level streaming in this loop.

### 3. Structured LLM call paths
These should remain non-streaming:
- `src/agents/cleaner_agent.py`
- `src/services/page_type_resolution.py`
- `src/services/program_name_resolution.py`
- `src/agents/factory.py` and provider implementations under `src/agents/providers/`

Reason:
- They depend on complete JSON / complete schema validation.
- Streaming here would increase partial-output risk and make tool-call / structured parsing more fragile.

## Architecture

### 1. Two output channels
Split the UX channel into two independent layers:

#### A. Execution event stream
Purpose:
- Show that the agent is progressing, even when the model is not producing user-facing text yet.

Carries structured events such as:
- `agent_started`
- `llm_call_started`
- `llm_call_finished`
- `tool_call_started`
- `tool_call_finished`
- `candidate_review_required`
- `persist_started`
- `persist_finished`
- `runtime_fallback`
- `agent_done`
- `agent_failed`

These events are machine-friendly and stable for UI consumption.

#### B. Final summary token stream
Purpose:
- Let the final natural-language summary appear progressively, reducing the "silent wait" feeling at the end.

Carries text deltas such as:
- `summary_started`
- `summary_delta`
- `summary_finished`

This is only used for user-visible explanation text, not for structured extraction.

### 2. Transport design

#### REST
Keep:
- `POST /agent/run` returning `task_id`
- `GET /tasks/{id}` polling

Add:
- `GET /tasks/{id}/events`

Recommended transport:
- SSE (Server-Sent Events)

Reason:
- Matches the current one-way progress-feed need.
- Simple to consume from extension/web UI.
- Does not require replacing existing polling.

#### MCP
Keep:
- `agent_run` returning final structured result

Do not require token-level MCP streaming in phase 1.

Reason:
- The protocol complexity is higher.
- The main waiting-anxiety problem can already be reduced through REST/SSE and extension integration.

#### Extension / frontend
Primary source:
- SSE event stream

Behavior:
- Show lifecycle progress from structured events.
- If `summary_delta` exists, incrementally render the final summary.
- If no token stream is available, still show events and final complete summary.

## Runtime Changes

### 1. Event emission in the agent loop
Instrument `src/agent_runtime/loop.py` to emit lifecycle events around:
- each LLM request
- each tool call
- persistence-related stages
- review wait states
- runtime fallback / terminal states

This should not require token streaming from the model itself.

### 2. Final summary generation path
Introduce a dedicated final-summary path after orchestration completes.

Suggested shape:
- `agent_loop(...)` continues to produce structured orchestration result
- a separate `agent_finalize_summary(...)` step turns the trace/result into user-facing natural language
- that finalizer may use streaming when supported

This separates:
- structured execution logic
- user-facing explanation rendering

### 3. Provider-layer boundary
Keep existing:
- `RouterAgent.generate()`
- provider `generate(...)` methods

Add new capability only for natural-language streaming, for example:
- `stream_text(...)`
- `generate_text_stream(...)`

This avoids turning the existing structured API into a dual-mode interface and protects current callers from regression.

## Error Handling

### 1. SSE disconnect
- Does not cancel or fail the running task
- Client can reconnect or fall back to polling `/tasks/{id}`

### 2. Summary stream unsupported
- If provider does not support streaming, emit:
  - `summary_started`
  - one complete summary payload
  - `summary_finished`
- Task still succeeds

### 3. Summary stream failure mid-flight
- Fall back to complete one-shot summary if possible
- If summary generation itself fails, preserve the core task result and expose the failure as event metadata

### 4. Runtime fallback
- Preserve existing `PydanticAIRuntime -> LegacyRuntime` fallback
- Emit a `runtime_fallback` event so the user can understand why behavior changed

## Why Not Stream Everything

Streaming every LLM call is explicitly rejected for this phase.

Reasons:
- structured extraction paths need complete validated JSON
- tool-call arguments are more fragile under streaming
- provider support is inconsistent
- broad streaming changes would touch too much of the stable core
- the user-value gain is much lower than event streaming for crawl-heavy workflows

## Testing Strategy

### 1. Unit / integration tests
Add:
- `tests/test_agent_event_stream.py`
- `tests/test_agent_summary_stream_fallback.py`
- `tests/test_agent_task_sse_disconnect.py`

Validate:
- event ordering
- expected event schema
- fallback behavior when streaming is unavailable
- SSE disconnect safety

### 2. Regression protection
Existing tests that must remain green:
- agent runtime tests
- crawl/analyze tests
- MCP registration and runtime metadata tests
- taxonomy / ingestion pipeline regressions
- cleaner-related structured extraction tests

### 3. Smoke-test gate
This is a required acceptance gate.

The repository already has project smoke tests. Extend them to verify streaming behavior:

- event-stream smoke validation:
  - a running agent task must emit ongoing execution events
  - at minimum verify presence of lifecycle events such as:
    - `llm_call_started`
    - `tool_call_finished`
    - `agent_done`

- final-summary streaming smoke validation:
  - if streaming is supported by the active provider, verify incremental `summary_delta` output
  - if streaming is not supported, verify graceful fallback to one-shot summary without task failure

Acceptance rule:
- the change is only considered complete when:
  - existing smoke tests still pass
  - new streaming smoke coverage passes

## Rollout Plan

### Phase 1
- Add SSE event stream for agent tasks
- Keep final summary one-shot if needed
- No changes to structured LLM calls

### Phase 2
- Add streamed final summary (`summary_delta`)
- Extension incrementally renders summary text

### Phase 3
- Evaluate whether MCP needs richer progress transport
- Only revisit deeper model streaming if production evidence shows real need

## Final Recommendation
Implement **event streaming for the whole agent lifecycle** and **token streaming only for the final natural-language summary**.

Do not convert the structured extraction stack to streaming in this phase.
