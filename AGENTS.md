# TP-Voyager — AI Execution Rules

## Mandatory read order

Before any architecture, implementation, test, or documentation change, read:

1. `TP_VOYAGER_CHARTER.md`
2. `TP_VOYAGER_DIRECTORY_BASELINE.md`
3. this `AGENTS.md`

If a proposal conflicts with the Charter or Directory Baseline, stop and report the conflict before implementing it.

## Current baseline

TP-Voyager T0–T4 are accepted. The product model is:

```text
Passenger → Captain AI → TP-Voyager → Crew
                              ├─ CodeBuddy CLI
                              └─ Qoder CLI
```

The Captain owns goal interpretation, decomposition, Crew choice, risk decisions, review, and final delivery. TP-Voyager owns reliable bounded execution, persistence, recovery, policy, verification, evidence, progress projection, and result retrieval. TP-Voyager must not become a second Captain.

## Backend boundary

Supported target Crew backends are only:

```text
CodeBuddy CLI
Qoder CLI
```

All new integration behavior must use their official public CLI/SDK/ACP contracts as Source of Truth.

WorkBuddy is removed from current production execution. Do not reintroduce WorkBuddy transport, Gateway/ACP execution, public tools, tests, or feature abstractions. Historical `workbuddy.* /v1` schema strings and old WorkBuddy runtime-home paths may remain only where necessary to read/migrate accepted historical data.

Controlled readiness is stricter than vendor capability. A Crew capability is Captain-dispatchable only after TP-Voyager has bounded and accepted it.

## Directory boundary

Production code keeps the stable top level:

```text
agent_runtime/
├── api/
├── application/
├── domain/
├── backends/
├── runtime/
├── persistence/
├── verification/
├── testing/
└── server.py
```

Target backend slots are:

```text
backends/codebuddy/
backends/qoder/
```

No new repository top-level or `agent_runtime/` top-level directory without explicit architecture review. Do not introduce generic dumping layers such as `core/`, `platform/`, `services/`, `managers/`, or `engine/`.

## Durable ownership

- SQLite Durable Row remains Source of Truth.
- Existing Task Runtime is the only Task state machine.
- Existing Workflow/PlanExecution remain reusable durable foundations; do not expand Planner intelligence.
- Captain dispatch must not require the internal Planner.
- Backend/model/fallback/retry remain explicit unless a future Charter-approved policy says otherwise.
- Prompt/business content stays transient by default unless an accepted contract explicitly persists it.

## Captain-facing direction

Normal Captain concepts stay compact:

```text
Voyage
Crew
Task
Result
```

Vendor-specific APIs must not become the normal Captain interface. Vendor diagnostics are the exception.

## Test policy

Default:

```text
Smoke + directly affected tests
```

Escalation:

1. Smoke — normal changes/new sessions.
2. Current/targeted — current changed module only.
3. Regression — durable lifecycle, public/shared adapter contract, persistence, workflow/recovery changes.
4. Live — CodeBuddy/Qoder auth, invocation, SDK/ACP, streaming, cancel/resume, model discovery, permissions.
5. Release/Stress — formal release or major core boundary only.

Do not run broad historical discovery/audit by habit. When a supported feature is removed, remove its current production code, current tests, and current docs together.

## AI change rule

An execution AI must not independently decide that TP-Voyager would be cleaner after a large reorganization. Locate the existing responsibility slot, change the smallest required surface, run only justified tests, and report any proposed structural deviation before implementing it.
