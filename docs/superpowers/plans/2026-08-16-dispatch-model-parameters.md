# Dispatch Model Parameters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a Captain to explicitly request supported reasoning effort and Qoder context-window settings on one controlled task dispatch.

**Architecture:** Add a strict `model_parameters` value object at the Captain MCP boundary and persist it with the durable request. Map its values into the existing Qoder ACP config-option handshake and into CodeBuddy's official SDK options. Every provider validates its own declared support before the first prompt and records requested/applied facts.

**Tech Stack:** Python 3, FastMCP tool annotations, Qoder ACP JSON-RPC, CodeBuddy Python Agent SDK, `unittest`.

## Global Constraints

- The Captain always selects Crew/model/parameters explicitly; no automatic selection, fallback, coercion, or retry.
- `model_parameters` requires an explicit `model`; unknown keys fail closed.
- CodeBuddy accepts only `reasoning_effort` in `low|medium|high|xhigh`; it rejects a context window.
- Qoder sends a parameter only after the session declares the category and exact value in `configOptions`; rejection happens before `session/prompt`.
- Real validation uses exactly two authorized bounded read-only tasks, one per backend, with no source writes.

---

### Task 1: Define and persist the Captain model-parameter contract

**Files:**
- Modify: `agent_runtime/domain/dispatch.py`
- Modify: `agent_runtime/api/mcp_server.py`
- Modify: `agent_runtime/domain/structured_result.py`
- Test: `tests/test_captain_boundary.py`

**Interfaces:**
- Consumes: `task_dispatch(..., model_parameters: dict | None)`.
- Produces: `ModelParameters(reasoning_effort: str, context_window_tokens: int | None)` and requested/applied result facts.

- [ ] **Step 1: Write failing contract tests**

```python
assert task_dispatch(..., model="qmodel_38max", model_parameters={"context_window_tokens": 200000})["ok"]
assert task_dispatch(..., model="", model_parameters={"reasoning_effort": "high"})["reason_code"] == "INVALID_MODEL_PARAMETERS"
assert task_dispatch(..., model="hy3", model_parameters={"unknown": 1})["reason_code"] == "INVALID_MODEL_PARAMETERS"
```

- [ ] **Step 2: Run the targeted tests and confirm they fail because `model_parameters` is unsupported.**

Run: `python -m unittest tests.test_captain_boundary -v`

- [ ] **Step 3: Implement the strict value object and MCP parsing.**

```python
@dataclass(frozen=True)
class ModelParameters:
    reasoning_effort: str = ""
    context_window_tokens: int | None = None
```

Reject non-object values, keys other than the two declared keys, empty/invalid effort tokens, non-positive/non-integer context values, and any parameters without an explicit model. Persist `to_dict()` in the Captain request contract.

- [ ] **Step 4: Run the targeted tests and confirm they pass.**

Run: `python -m unittest tests.test_captain_boundary -v`

### Task 2: Map CodeBuddy reasoning effort through the official SDK

**Files:**
- Modify: `agent_runtime/backends/codebuddy/backend.py`
- Modify: `agent_runtime/backends/codebuddy/sdk_client.py`
- Modify: `agent_runtime/backends/codebuddy/capability.py`
- Modify: `agent_runtime/backends/codebuddy/model_catalog.py`
- Test: `tests/test_codebuddy_backend.py`

**Interfaces:**
- Consumes: `BackendStartRequest.reasoning_effort`.
- Produces: `CodeBuddyAgentOptions(thinking={"type": "enabled"}, effort=<explicit value>)` and `reasoning_effort_applied` facts.

- [ ] **Step 1: Write failing SDK option tests.**

```python
options = client._build_options(FakeSdkModule, model="hy3", reasoning_effort="high", session_id="s")
assert options.kwargs["thinking"] == {"type": "enabled"}
assert options.kwargs["effort"] == "high"
```

Also assert an omitted effort adds neither key and a context window is rejected at the Captain boundary for CodeBuddy.

- [ ] **Step 2: Run the CodeBuddy tests and confirm the new assertions fail.**

Run: `python -m unittest tests.test_codebuddy_backend -v`

- [ ] **Step 3: Thread the effort through `CodeBuddyBackend` and `CodeBuddySdkClient`; set `supports_reasoning_effort=True`.**

Pass the value only to the SDK option builder. Preserve `setting_sources=[]`, controlled tools, and all existing permissions. Mark application as true only when the option was explicitly supplied to the SDK.

- [ ] **Step 4: Run the CodeBuddy tests and confirm they pass.**

Run: `python -m unittest tests.test_codebuddy_backend -v`

### Task 3: Apply Qoder declared context-window options before the prompt

**Files:**
- Modify: `agent_runtime/backends/qoder/acp_client.py`
- Modify: `agent_runtime/backends/qoder/backend.py`
- Test: `tests/test_qoder_acp_client.py`
- Test: `tests/test_qoder_backend.py`

**Interfaces:**
- Consumes: `context_window_tokens` in `ModelParameters`.
- Produces: `context_window_tokens_requested`, `context_window_tokens_applied` in the Qoder result and observability.

- [ ] **Step 1: Write failing ACP tests.**

```python
result = client.run(..., model="qmodel_38max", reasoning_effort="medium", context_window_tokens=200000)
assert sent_set_options == [("model", "qmodel_38max"), ("context_window", "200000"), ("thought_level", "medium")]
assert result.context_window_tokens_applied is True
```

Add a second test where the `context_window` option omits `200000`; assert `session/prompt` is never sent and the controlled failure reports the unsupported setting.

- [ ] **Step 2: Run Qoder tests and confirm the new assertions fail.**

Run: `python -m unittest tests.test_qoder_acp_client tests.test_qoder_backend -v`

- [ ] **Step 3: Extend the ACP client with a strict declared-value resolver and context-window application.**

Find the session-declared category by exact `context_window` or a bounded context/window alias. Require a matching declared value before `session/set_config_option`; do not send the request when matching fails. Apply model, context window, then thought level before the prompt. Thread result facts through `QoderBackend`.

- [ ] **Step 4: Run Qoder tests and confirm they pass.**

Run: `python -m unittest tests.test_qoder_acp_client tests.test_qoder_backend -v`

### Task 4: Verify public projection and real controlled transport

**Files:**
- Modify: `tests/test_mcp_live_catalog.py` or the existing MCP dispatch test module
- Modify: `docs/BACKEND_CODEBUDDY.md`
- Modify: `docs/BACKEND_QODER.md`

**Interfaces:**
- Consumes: MCP `task_dispatch.model_parameters`.
- Produces: public terminal result that distinguishes each requested value from whether the provider applied it.

- [ ] **Step 1: Write failing MCP-contract tests for the two parameters and result projection.**

Assert the default six-tool contract exposes `model_parameters`; assert a CodeBuddy context request is rejected and the Qoder projection preserves both requested/applied fields.

- [ ] **Step 2: Run the targeted MCP tests and confirm they fail.**

Run: `python -m unittest tests.test_mcp_live_catalog -v`

- [ ] **Step 3: Implement the result projection and update backend documentation.**

Document that parameters are explicit per-dispatch overrides, depend on the provider's live declaration, and never inherit interactive-app settings.

- [ ] **Step 4: Run targeted regression tests.**

Run: `python -m unittest tests.test_captain_boundary tests.test_codebuddy_backend tests.test_qoder_acp_client tests.test_qoder_backend tests.test_mcp_live_catalog -v`

- [ ] **Step 5: Run the two authorized real read-only MCP tasks after the code is reloaded.**

Use `qoder:qmodel_38max` with `{"context_window_tokens": 200000, "reasoning_effort": "medium"}` and `codebuddy:deepseek-v4-flash` with `{"reasoning_effort": "high"}`. Give each a single README scope, `task_kind="research"`, `access_mode="read_only"`, one explicit timeout, and unique correlation/idempotency keys. Read each `task_result`; do not retry or substitute a model.

