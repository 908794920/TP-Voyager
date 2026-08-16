# Crew Parameter Capability and Usage Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject unsupported Crew model parameters before task creation and make provider-observed usage explicitly queryable per task.

**Architecture:** Extend the Captain dispatch boundary with a read-only lookup of the selected descriptor from `CrewRegistryService.model_catalog`. Qoder keeps context as a CLI startup setting but requires ACP thought-level configuration to be declared and accepted. Reuse `BackendUsage` and immutable Usage Evidence, adding one normalized public projection rather than any cost estimator.

**Tech Stack:** Python 3, dataclasses, unittest, TP-Voyager MCP runtime, SQLite-backed Evidence.

## Global Constraints

- Explicit `crew` and `model` remain mandatory whenever `model_parameters` is supplied.
- No automatic model selection, fallback, retry, token estimation, credit estimation, or currency conversion.
- Provider-live descriptor absence or unsupported requested capability fails closed before a task is created.
- Preserve the dirty working tree; no Git commit is authorized for this task.

---

### Task 1: Capability-aware Captain preflight

**Files:**
- Modify: `agent_runtime/application/dispatch/service.py`
- Modify: `tests/test_captain_boundary.py`

**Interfaces:**
- Consumes: `CaptainDispatchRequest.model_parameters`, `CrewRegistryService.model_catalog(backend)`.
- Produces: content-free dispatch rejections `MODEL_PARAMETERS_CAPABILITY_UNKNOWN`, `MODEL_PARAMETERS_UNSUPPORTED_EFFORT`, and `MODEL_PARAMETERS_UNSUPPORTED_CONTEXT`.

- [ ] **Step 1: Write failing preflight tests**

```python
def test_dispatch_rejects_context_not_declared_by_selected_qoder_model(self) -> None:
    result = service.dispatch(request(model_parameters=ModelParameters(context_window_tokens=400000)))
    self.assertFalse(result["ok"])
    self.assertEqual(result["reason_code"], "MODEL_PARAMETERS_UNSUPPORTED_CONTEXT")
    dispatcher.assert_not_called()

def test_dispatch_rejects_effort_not_declared_by_selected_model(self) -> None:
    result = service.dispatch(request(model_parameters=ModelParameters(reasoning_effort="high")))
    self.assertFalse(result["ok"])
    self.assertEqual(result["reason_code"], "MODEL_PARAMETERS_UNSUPPORTED_EFFORT")
    dispatcher.assert_not_called()
```

- [ ] **Step 2: Run the two tests to verify they fail because preflight is absent**

Run: `python -m unittest tests.test_captain_boundary.CaptainBoundaryTests.test_dispatch_rejects_context_not_declared_by_selected_qoder_model tests.test_captain_boundary.CaptainBoundaryTests.test_dispatch_rejects_effort_not_declared_by_selected_model -v`

Expected: failures showing the dispatcher was called or the result was accepted.

- [ ] **Step 3: Implement a bounded descriptor validator**

```python
def _validate_model_parameters(self, *, crew: str, model: str, parameters: ModelParameters | None) -> tuple[str, str] | None:
    if parameters is None:
        return None
    catalog = self._registry.model_catalog(crew)
    descriptor = next((item for item in catalog["models"] if item.model_id == model), None)
    # Reject absent descriptor or unknown required capability, then compare
    # effort against metadata.reasoning.supported_efforts and Qoder context
    # against metadata.context_config token_count values.
```

Call it after the explicit-model policy checks and before selecting a dispatcher. Return only the defined reason code and a generic message; do not return provider raw configuration.

- [ ] **Step 4: Run the preflight tests to verify they pass**

Run: `python -m unittest tests.test_captain_boundary.CaptainBoundaryTests.test_dispatch_rejects_context_not_declared_by_selected_qoder_model tests.test_captain_boundary.CaptainBoundaryTests.test_dispatch_rejects_effort_not_declared_by_selected_model -v`

Expected: both tests PASS and each verifies no task creation.

### Task 2: Qoder thought-level confirmation and Usage projection

**Files:**
- Modify: `agent_runtime/backends/qoder/acp_client.py`
- Modify: `agent_runtime/api/mcp_server.py`
- Modify: `tests/test_qoder_acp_client.py`
- Modify: `tests/test_runtime_diagnostics.py`

**Interfaces:**
- Consumes: ACP `configOptions`, `session/set_config_option` result, `BackendUsage.to_dict()`.
- Produces: pre-prompt `BackendProtocolError` for unavailable requested effort and a public `usage_status` projection with only observed values.

- [ ] **Step 1: Write failing ACP and result-projection tests**

```python
def test_requested_effort_without_declared_acp_option_rejects_before_prompt(self) -> None:
    # Fake session/new returns no thought_level config option.
    with self.assertRaisesRegex(BackendProtocolError, "thinking effort"):
        client.run(
            prompt="inspect", model="model-2", reasoning_effort="medium",
            context_window_tokens=None, idle_timeout_seconds=5,
            max_task_duration_seconds=10, on_dispatch_accepted=lambda _: None,
        )
    self.assertEqual(fake.prompt_requests, 0)

def test_task_result_projects_provider_omitted_usage_without_estimate(self) -> None:
    result = server.task_result(task_id)
    self.assertEqual(result["usage"], {"status": "provider_omitted"})
```

- [ ] **Step 2: Run tests to verify they fail for the missing strict behavior**

Run: `python -m unittest tests.test_qoder_acp_client.QoderAcpProtocolTests.test_requested_effort_without_declared_acp_option_rejects_before_prompt tests.test_runtime_diagnostics.RuntimeDiagnosticsTests.test_task_result_projects_provider_omitted_usage_without_estimate -v`

Expected: the effort test reaches prompt or the result lacks `status`.

- [ ] **Step 3: Implement strict effort and normalized usage semantics**

```python
if reasoning_effort and reasoning_applied is not True:
    raise BackendProtocolError("Qoder ACP did not accept the requested thinking effort")

def _usage_projection(evidence: dict[str, Any]) -> dict[str, Any]:
    # Return {"status": "provider_omitted"} for no Evidence.
    # Return only non-null input_tokens, output_tokens, credits_used,
    # reported_cost, currency and source for observed Evidence.
```

Use `usage_provenance.status` as the Qoder no-quantity classification when it exists. Do not introduce a pricing formula.

- [ ] **Step 4: Run the two tests to verify they pass**

Run: `python -m unittest tests.test_qoder_acp_client.QoderAcpProtocolTests.test_requested_effort_without_declared_acp_option_rejects_before_prompt tests.test_runtime_diagnostics.RuntimeDiagnosticsTests.test_task_result_projects_provider_omitted_usage_without_estimate -v`

Expected: both PASS; no prompt is emitted for unsupported effort and empty provider usage is explicit.

### Task 3: Contract documentation and end-to-end verification

**Files:**
- Modify: `README.md`
- Modify: `docs/BACKEND_QODER.md`
- Modify: `skills/tp-voyager-captain/README.md`
- Test: `tests/test_captain_boundary.py`, `tests/test_qoder_acp_client.py`, `tests/test_runtime_diagnostics.py`

**Interfaces:**
- Consumes: rejection reason codes and public `task_result.usage` projection.
- Produces: Captain guidance that names observed usage versus provider omission.

- [ ] **Step 1: Update Captain-facing documentation**

Document the two preflight rules and this exact result shape:

```json
{
  "usage": {
    "status": "observed",
    "input_tokens": 18836,
    "output_tokens": 858,
    "reported_cost": 0,
    "currency": "USD"
  }
}
```

State that Qoder may instead return `{"status":"provider_omitted"}` and that multipliers never become estimates.

- [ ] **Step 2: Run targeted regression checks**

Run: `python -m unittest tests.test_captain_boundary tests.test_qoder_acp_client tests.test_qoder_backend tests.test_codebuddy_backend tests.test_runtime_diagnostics tests.test_mcp_live_catalog -v`

Expected: zero failures from this feature; record unrelated environment-only failures separately if they recur.

- [ ] **Step 3: Run static and installation checks**

Run: `python -m compileall -q agent_runtime tests; git diff --check; python skills\\tp-voyager-captain\\install_codex_desktop.py; python skills\\tp-voyager-captain\\install_codex_desktop.py --check`

Expected: compilation and diff checks succeed; final installer result is `check-ok`.

- [ ] **Step 4: Restart MCP and run one real bounded Qoder task**

Call `task_dispatch` with `crew="qoder"`, `model="qmodel_38max"`, `model_parameters={"reasoning_effort":"medium","context_window_tokens":200000}`, read-only `README.md` scope, and a new correlation/idempotency key. Then call `task_result` once terminal.

Expected: task completes with both applied flags true and `usage.status` equals either `observed`, `provider_omitted`, or `protocol_unrecognized`; no quantity is invented.
