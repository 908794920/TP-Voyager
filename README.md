# TP-Voyager

**TP-Voyager** is a local AI execution/control system built around one product model:

```text
Passenger → Captain AI → TP-Voyager → Crew
                              ├─ CodeBuddy CLI
                              └─ Qoder CLI
```

The human is the **Passenger** and states the destination. The top-level AI is the **Captain**: it interprets the goal, decomposes work, understands Crew strengths, selects Crew, follows progress, judges risk, reviews results, and owns final delivery.

TP-Voyager is not a second Captain. It is the reliable execution/control layer below the Captain.

## Current baseline

T0–T4 are accepted:

```text
T0 Target Architecture       ACCEPTED
T1 Crew Registry             ACCEPTED
T2 Captain Boundary          ACCEPTED
T3 Controlled Read-only      ACCEPTED
T4 Controlled Patch          ACCEPTED
```

The supported Crew backends are now only:

```text
CodeBuddy CLI
Qoder CLI
```

WorkBuddy execution has been physically removed from the current production path. Historical `workbuddy.* /v1` schema names and the old WorkBuddy runtime-home location remain only where required to read or migrate accepted historical durable data. They are compatibility data contracts, not a supported Crew backend.

## Durable foundation

TP-Voyager reuses the accepted Agent Runtime V2 durable core rather than rewriting it:

```text
Task / Session / Attempt / Event / Idempotency
Lease / Fencing / Cancel / Restart Reconciliation
Workflow / PlanExecution
Context / Knowledge / Tool Runtime
Structured Result / Evidence / Artifact / Verification / Plan Result
SQLite Durable Row = Source of Truth
```

The Python package remains `agent_runtime/`. Product renaming does not justify import churn.

## Captain surface

Normal Captain usage should stay compact:

```text
crew_catalog
crew_health
crew_recommend
voyager_overview
task_dispatch
task_result
```

Existing generic task/status/cancel APIs remain available where the accepted durable core needs them, but vendor-specific tools are not the normal Captain interface.

## Controlled Crew routes

### CodeBuddy

```text
sdk_context_read_only
sdk_patch
```

China accounts use `CODEBUDDY_INTERNET_ENVIRONMENT=internal`.

### Qoder

```text
acp_read_only
acp_patch
```

Controlled routes do not use `--yolo`; legacy uncontrolled Qoder `acp`/`print` execution routes are not exposed by the current Runtime.

### Patch boundary

Patch dispatch uses:

```text
clean source Git tree
→ Runtime-owned isolated worktree
→ allowed/forbidden path policy
→ exact argv command whitelist
→ patch/hash capture
→ deterministic verification/evidence
→ no automatic merge into Passenger worktree
```

## Current source of truth

Read in this order:

1. [`TP_VOYAGER_CHARTER.md`](TP_VOYAGER_CHARTER.md)
2. [`TP_VOYAGER_DIRECTORY_BASELINE.md`](TP_VOYAGER_DIRECTORY_BASELINE.md)
3. [`AGENTS.md`](AGENTS.md)
4. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
5. [`docs/TESTING.md`](docs/TESTING.md)
6. [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
7. [`docs/BACKEND_CODEBUDDY.md`](docs/BACKEND_CODEBUDDY.md)
8. [`docs/BACKEND_QODER.md`](docs/BACKEND_QODER.md)

Historical acceptance records live under `docs/records/` and are not current contracts.

## Start

Python 3.10+ is required.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:AGENT_RUNTIME_PYTHON = ".\.venv\Scripts\python.exe"
.\start_runtime.cmd
```

Canonical runtime variables:

```text
AGENT_RUNTIME_PYTHON
AGENT_RUNTIME_DB
AGENT_RUNTIME_HOME
```

## Test rule

For normal work:

```text
Smoke + directly affected targeted tests
```

Do not repeat the historical multi-hour Release/Stress suite for routine changes.
