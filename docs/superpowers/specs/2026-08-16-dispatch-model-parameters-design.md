# Dispatch model parameters

## Goal

Expose the provider capabilities already discovered by `crew_catalog` as explicit,
per-dispatch controls without automatic selection, fallback, or persistent Crew
setting changes.

## Public contract

`task_dispatch` accepts an optional `model_parameters` object:

```json
{
  "reasoning_effort": "high",
  "context_window_tokens": 200000
}
```

- Both keys are optional; unknown keys and invalid value types are rejected.
- The object requires an explicit `model` and is persisted in the Captain request
  contract and terminal result as requested/applied facts.
- Omitting it preserves existing dispatch behavior. No default or fallback is
  selected by TP-Voyager.

## Backend mapping

### CodeBuddy

- Accept `reasoning_effort` only (`low`, `medium`, `high`, `xhigh`).
- Map it to the official SDK `thinking={"type": "enabled"}` and `effort`.
- Reject `context_window_tokens`; the official SDK has no confirmed per-session
  context-window option.

### Qoder

- Accept both fields only after `session/new` exposes matching `configOptions`.
- Resolve the requested value strictly from the option's declared values and
  apply it through `session/set_config_option` before `session/prompt`.
- If a configuration category/value is absent or the update fails, return a
  controlled pre-prompt rejection. Do not coerce, downgrade, or send the task
  prompt.

## Verification

- Unit tests cover contract validation, CodeBuddy SDK option construction, and
  Qoder declared-option application/rejection.
- MCP catalog reports the controlled capabilities and applied/requested result
  facts.
- With Passenger authorization, dispatch two bounded read-only real tasks:
  Qoder (`context_window_tokens` plus `reasoning_effort`) and CodeBuddy
  (`reasoning_effort`). Neither task may retry, change files, or broaden scope.

