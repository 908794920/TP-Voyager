# TP-Voyager T3 Real CLI Live Acceptance Report

> Generated: 2026-08-08
>
> Scope: **only T3 controlled read-only Crew** (Qoder `acp_read_only`, CodeBuddy
> `sdk_context_read_only`). No Regression / Stress / Release run.

---

## 1. Versions

```text
CodeBuddy CLI         2.133.0
CodeBuddy Agent SDK   0.3.237
Qoder CLI             1.1.17
Qoder model list      Lite (lite, 0.00x Credit) / Qwen3.8-Max (qmodel_38max)
```

---

## 2. Live checks

```text
MCP small discovery               PASS
Crew health/recommend             PASS
CodeBuddy bounded context task    PASS
CodeBuddy context drift           PASS
Qoder read-only task              PASS (Lite free model)
No hidden fallback                PASS
```

Detail:

- MCP discovery: all 9 Captain-facing tools discoverable
  (`crew_catalog`, `crew_health`, `crew_recommend`, `voyager_overview`,
  `task_dispatch`, `task_result`, `context_register`, `context_verify`,
  `context_status`).
- Crew health: `codebuddy` -> `dispatch_ready=true`, `availability=available`,
  `region=cn`, `cli_installed=true`, `sdk_installed=true`,
  `auth_probe_performed=false` (expected). `qoder` -> `dispatch_ready=true`,
  `availability=available`, version 1.1.17. `crew_recommend(research)` ->
  CodeBuddy compatible=true, Qoder compatible=true, selection/dispatch not
  performed (expected).
- CodeBuddy bounded context task: `task_dispatch` returned
  `ok=true`, `crew=codebuddy`, `dispatch_performed=true`. Result identified
  `TP_VOYAGER_ALLOWED_MARKER_84F2`; result did NOT reveal
  `TP_VOYAGER_SECRET_MARKER_91C7`; `allowed.txt` and `secret.txt` SHA-256
  unchanged.
- CodeBuddy context drift: after `allowed.txt` was modified, `task_dispatch`
  returned `ok=false`, `reason_code=CONTEXT_DRIFT`, `dispatch_performed=false`.
  No Crew task created (fail-closed confirmed).
- Qoder read-only task: `task_dispatch(model="lite")` returned
  `ok=true`, `crew=qoder`, `dispatch_performed=true`, route `acp_read_only`.
  Result summarized the fixture file; `source.txt` SHA-256 unchanged; no new
  files created in the fixture directory. Model pinned to `lite`
  (0.00x Credit, free) via `session/set_config_option`.
- No hidden fallback: `task_dispatch(crew="workbuddy")` returned
  `ok=false`, `reason_code=CREW_NOT_SUPPORTED`, `dispatch_performed=false`.
  WorkBuddy refusal confirmed.

---

## 3. Fixes applied within T3 (controlled routes only)

Two real route bugs were found by the live gate and repaired inside T3:

1. `agent_runtime/backends/codebuddy/sdk_client.py`
   - Symptom: every new CodeBuddy task failed with
     `BackendProtocolError: CodeBuddy SDK changed session identity`.
   - Root cause: the official SDK (0.3.237) **ignores the `session_id`
     parameter on `query()` for string prompts** (it only applies to streamed
     dicts) and uses the CLI-captured session id instead. TP-Voyager pre-generated
     a `uuid4` dispatch id, so the session-identity guard always fired.
   - Fix: pass the dispatch id through `CodeBuddyAgentOptions.session_id` (the
     official "custom session ID" field) before the dispatch gate, so the SDK
     returns exactly that id and durable resume identity stays stable.

2. `agent_runtime/backends/qoder/acp_client.py`
   - Symptom: every Qoder task failed with
     `BackendProtocolError: Model "lite" is not available in the current Qoder catalog`.
   - Root cause: Qoder ACP `session/new` reports model `currentValue="lite"`,
     which is not present in the account catalog (only `qmodel_38max` /
     Qwen3.8-Max). A bare Captain dispatch left the stale default model in place.
   - Fix: when no model is requested, pin the first catalog model value from the
     session's `configOptions` before `session/prompt` (`config_option_update`
     observed; prompt then starts normally).

Verified with affected targeted tests:

```text
tests.test_codebuddy_backend                            10/10 PASS
tests.test_qoder_backend                                7/7  PASS
tests.test_qoder_acp_client / test_backend_integration
  / test_crew_registry / test_captain_boundary
  / test_v12_context                                    27/27 PASS
Smoke profile                                           PASS except
  tests.test_tp_voyager_architecture (baseline doc gap,
  unrelated to these fixes; not in T3 acceptance scope)
```

---

## 4. Qoder read-only task — re-verification

Initially the Qoder live task was blocked by account credit limits after the
model fix. After the account was topped up, the check was re-run with the
free model explicitly selected:

- `session/new` ok; model pinned to `lite` (0.00x Credit, free) via
  `session/set_config_option` with `configId=model`, `value=lite`
- `task_dispatch` `ok=true`, `crew=qoder`, route `acp_read_only`,
  `dispatch_performed=true`
- prompt completed with a valid summary answer
- `source.txt` SHA-256 unchanged; no new files in the fixture directory

Re-verification outcome: **PASS**.

---

## 5. Unexpected file changes

```text
Unexpected file changes    0
```

All fixture SHA-256 hashes unchanged after live tasks; no new files were
created in the Qoder fixture directory.

---

## 6. Gate status

```text
6 of 6 live checks PASS

T3 = ACCEPTED   -> CLAIMABLE (all controlled read-only Crew checks green)
T3 = REJECTED   -> NO (no functional regression; all fixes verified in T3)

Status: T3 controlled read-only closure is complete. T4 (patch/write/command
controlled readiness) may be scoped next.
```
