# TP-Voyager Directory Baseline v1.0

> **Status:** Frozen Baseline  
> **Purpose:** Prevent architecture drift, directory inflation, responsibility overlap, and AI-driven restructuring without evidence.
>
> This document is subordinate only to `docs/architecture/CHARTER.md`.
>
> If a future implementation proposal conflicts with this directory baseline, the proposal must be rejected unless the baseline itself is explicitly revised first.

---

# 1. Baseline Principle

TP-Voyager uses the following fixed layering:

```text
Passenger
    ↓
Captain AI
    ↓
TP-Voyager
    ↓
Crew
├── CodeBuddy CLI
└── Qoder CLI
```

The repository structure must reflect this responsibility boundary.

The directory tree is not allowed to grow merely because a new feature is added.

Primary rule:

```text
Reuse an existing responsibility slot before creating a new directory.
```

---

# 2. Frozen Repository Root

```text
TP-Voyager/
│
├── README.md
├── AGENTS.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
├── pyproject.toml
├── requirements.txt
├── agent_runtime/
├── skills/
├── tests/
├── docs/
│   └── architecture/
│       ├── CHARTER.md
│       └── DIRECTORY_BASELINE.md
└── scripts/
    ├── start_runtime.cmd
    └── run_tests.cmd
```

These are the expected long-term top-level areas.

## No new top-level directory by default

The following pattern is prohibited without explicit architecture review:

```text
TP-Voyager/
├── orchestrator/
├── captain/
├── scheduler/
├── engine/
├── control_plane/
├── gateway/
├── services/
├── platform/
├── core/
├── infra/
└── ...
```

If a responsibility can fit under the existing `agent_runtime/`, `skills/`, `tests/`, `docs/`, or `scripts/` hierarchy, it must go there.

---

# 3. Product Name vs Python Package

The product is:

```text
TP-Voyager
```

The existing Python package remains:

```text
agent_runtime/
```

Do **not** rename it to:

```text
tp_voyager/
voyager/
voyager_core/
captain_runtime/
```

unless a future real technical requirement makes the rename necessary.

Reason:

```text
Renaming does not create product value.
It creates import churn, migration risk, test churn, and documentation churn.
```

Product naming and implementation package naming are intentionally separated.

---

# 4. Frozen `agent_runtime/` Structure

```text
agent_runtime/
│
├── api/
│   ├── mcp_server.py
│   └── schemas/
│
├── application/
│   ├── voyage/
│   ├── crew/
│   ├── dispatch/
│   ├── context/
│   └── task/
│
├── domain/
│
├── backends/
│   ├── base.py
│   ├── codebuddy/
│   └── qoder/
│
├── runtime/
│
├── persistence/
│
├── verification/
│
├── testing/
│
└── server.py
```

This is the target architecture baseline.

Not every subdirectory must exist immediately.

A directory should only be created when real code for that responsibility exists.

Do not create empty placeholder architecture merely to match the diagram.

---

# 5. `api/` — External Transport Boundary

Purpose:

```text
Expose TP-Voyager capabilities to Captain AI or external clients.
```

Contains:

```text
MCP entry points
request parsing
response projection
public schema adaptation
transport-level validation
```

Must not contain:

```text
Business orchestration
Backend-specific execution logic
SQLite logic
Task lifecycle state machine
Model selection intelligence
Verification implementation
```

Allowed direction:

```text
api
 ↓
application
```

Forbidden direction:

```text
application
 ↓
api
```

---

# 6. `application/` — TP-Voyager Use Cases

This is the primary growth area for future TP-Voyager behavior.

```text
application/
├── voyage/
├── crew/
├── dispatch/
├── context/
└── task/
```

No additional application subdomain should be introduced unless none of these can express the responsibility.

---

# 7. `application/voyage/`

Purpose:

```text
Project Captain-facing overall progress.
```

Responsibilities:

```text
Voyage overview
Overall status projection
Current active tasks
Completed tasks
Blocked tasks
Captain decisions required
Risk summary
Progress aggregation
```

Must not:

```text
Interpret business requirements
Autonomously decompose missions
Choose business strategy
Call AI models to plan
Create a second workflow state machine
```

Voyage is a progress/control projection, not an intelligent planner.

---

# 8. `application/crew/`

Purpose:

```text
Maintain normalized knowledge about available AI workers.
```

Responsibilities:

```text
Crew catalog
Model catalog
Capabilities
Availability
Health history
Latency history
Success history
Cost class
Crew recommendation
```

Official first-class Crew families:

```text
CodeBuddy CLI
Qoder CLI
```

Must not:

```text
Dispatch tasks directly
Modify Task state
Own Backend sessions
Store business source code
Automatically select a worker without Captain authorization
```

---

# 9. `application/dispatch/`

Purpose:

```text
Convert a Captain dispatch decision into a validated Runtime execution.
```

Responsibilities:

```text
Validate requested worker
Validate required capability
Validate execution contract
Create/reuse Durable Task
Invoke backend through shared launch boundary
Record dispatch decision
```

Must not:

```text
Become a Planner
Interpret user goals
Generate business task decomposition
Silently switch backend/model
Maintain its own Task status
```

Primary flow:

```text
Captain decision
    ↓
dispatch
    ↓
Durable Task
    ↓
Backend Adapter
```

---

# 10. `application/context/`

Purpose:

```text
Prepare bounded execution context.
```

Responsibilities may include:

```text
Explicit path selection
Git diff context
Nearby test discovery
Symbol-related context
Context Manifest reuse
Context size limits
Context hash validation
```

Must not become:

```text
Unlimited RAG platform
Vector DB
Automatic knowledge ingestion system
Repository-wide semantic index by default
```

Existing Context/Knowledge foundations must be reused.

---

# 11. `application/task/`

Purpose:

```text
Expose reusable Task-level application use cases over the existing Durable Runtime.
```

Responsibilities:

```text
Task launch boundary
Task query
Task cancel
Task resume
Task result access
Task-to-backend application coordination
```

Must not create:

```text
Second Task entity
Second Task status enum
Second retry system
Second Session truth
```

Existing V2 Durable Task remains authoritative.

---

# 12. `domain/` — Stable Business Contracts

Purpose:

```text
Pure domain models and contracts.
```

Expected concepts may include:

```text
CrewDescriptor
ModelDescriptor
HealthSnapshot
ExecutionContract
VoyageProjection
DispatchDecision
FailureCode
```

Rules:

```text
No MCP dependency
No CLI dependency
No SDK dependency
No SQLite dependency
No process execution
```

Do not split domain into many nested folders unless the file count and real responsibility justify it.

Avoid architecture cosmetics.

---

# 13. `backends/` — Crew Adapters

Frozen target:

```text
backends/
├── base.py
├── codebuddy/
└── qoder/
```

No WorkBuddy in the target architecture.

---

# 14. `backends/base.py`

Purpose:

```text
Shared backend contract only.
```

May define:

```text
Backend protocol
Common request/result envelope
Capability interface
Probe interface
Cancel/resume contract
```

Must not contain:

```text
Vendor conditionals
if backend == codebuddy
if backend == qoder
```

Vendor behavior belongs in the vendor directory.

---

# 15. `backends/codebuddy/`

Suggested internal structure:

```text
codebuddy/
├── backend.py
├── sdk_client.py
├── capability.py
├── model_catalog.py
└── process.py
```

Only create files that are actually required.

Source of truth:

```text
Official CodeBuddy CLI documentation
Official SDK documentation
Official supported behavior
```

Do not depend on:

```text
Private/internal WorkBuddy protocol behavior
Undocumented guessed interfaces
```

---

# 16. `backends/qoder/`

Suggested internal structure:

```text
qoder/
├── backend.py
├── sdk_client.py
├── acp_client.py
├── capability.py
├── model_catalog.py
└── process.py
```

Source of truth:

```text
Official Qoder CLI documentation
Official SDK documentation
Official ACP documentation
```

Existing working ACP implementation may be reused and normalized.

Do not duplicate ACP and SDK lifecycle state machines.

---

# 17. WorkBuddy Removal Rule

Historical source may currently contain:

```text
backends/workbuddy/
workbuddy-specific APIs
workbuddy-specific tests
workbuddy-specific docs
```

Removal sequence must be:

```text
1. Remove new public dependency on WorkBuddy
2. Ensure CodeBuddy/Qoder replacement path exists
3. Remove WorkBuddy-specific application coupling
4. Remove WorkBuddy production backend
5. Remove WorkBuddy-only tests
6. Remove WorkBuddy-only current documentation
7. Keep only historical records if useful
```

Do not preserve zombie compatibility indefinitely.

If WorkBuddy is removed as a supported feature:

```text
production code removed
+
tests removed
+
current docs removed
```

---

# 18. `runtime/` — Durable Execution Machinery

Purpose:

```text
Generic runtime mechanics.
```

Contains existing proven infrastructure such as:

```text
leases
fencing
process/session runtime coordination
reconciliation support
runtime handles
```

Must not become:

```text
Captain logic
Crew catalog
Business planning
Vendor-specific backend folder
Generic dumping ground
```

If a feature is a use case, it belongs in `application/`, not `runtime/`.

---

# 19. `persistence/`

Purpose:

```text
SQLite durable storage and migrations.
```

Contains:

```text
database
repositories
schema migration
runtime paths
artifact persistence integration
```

Rules:

```text
SQLite Durable Row remains Source of Truth.
```

Must not:

```text
Contain orchestration decisions
Call AI CLI
Implement recommendation logic
Store duplicated Task state
```

---

# 20. `verification/`

Purpose:

```text
Deterministic result verification.
```

Responsibilities:

```text
Verification commands
Verification result normalization
Evidence consistency
Claim/evidence gate
Patch/result checks
```

Must not:

```text
Become another worker
Call a hidden AI to judge everything by default
Own Task lifecycle
```

---

# 21. `testing/` inside Production Package

Purpose:

```text
Reusable testing support required by Runtime internals.
```

This is not where ordinary unit tests live.

Avoid growing production package test helpers unless they are genuinely reusable runtime/testing infrastructure.

Main tests belong under root:

```text
tests/
```

---

# 22. `skills/`

Purpose:

```text
Project-level AI usage instructions or reusable skills.
```

Rules:

```text
Tool-independent when possible
No .codex-specific ownership
No business runtime code
No duplicate architecture documentation
```

A Skill does not become a substitute for Runtime policy enforcement.

---

# 23. Frozen Test Structure

Target:

```text
tests/
├── smoke/
├── targeted/
├── integration/
└── live/
```

Interpretation:

```text
smoke
  Fast structural confidence.

targeted
  Current feature / module behavior.

integration
  Cross-boundary behavior.

live
  Real CodeBuddy/Qoder integration.
```

Release is a profile, not necessarily a directory.

---

# 24. Test Naming Rule

Do not organize tests by historical project version.

Prohibited growth pattern:

```text
test_v1_*
test_v12_*
test_v17_*
test_v2_*
test_t0_*
test_t1_*
test_t2_*
```

Long-term tests should be named by current behavior:

```text
test_task_reconciliation.py
test_crew_catalog.py
test_codebuddy_backend.py
test_qoder_backend.py
test_dispatch_policy.py
test_workspace_patch.py
```

Historical names may be cleaned gradually when touched.

Do not perform a mass rename only for aesthetics.

---

# 25. Test Deletion Rule

When a feature is intentionally removed:

```text
feature code
+
feature tests
+
current feature docs
```

must be removed together.

Do not keep tests merely because they existed historically.

Test suite protects current supported behavior.

---

# 26. `docs/`

Frozen current-document shape:

```text
docs/
├── README.md
├── ARCHITECTURE.md
├── MODEL_ROUTING.md
├── TESTING.md
├── OPERATIONS.md
├── BACKEND_CODEBUDDY.md
├── BACKEND_QODER.md
├── architecture/
│   ├── CHARTER.md
│   └── DIRECTORY_BASELINE.md
├── examples/
└── records/
```

Current product/operations documentation stays at the top of `docs/`; governance baselines live in `docs/architecture/`.

Historical documents belong in:

```text
docs/records/
```

Historical documents must never override current documents.

---

# 27. Documentation Anti-Inflation Rule

Do not create one permanent document for every phase.

Avoid:

```text
T0_DESIGN.md
T0_FINAL.md
T0_FINAL_FINAL.md
T1_IMPLEMENTATION.md
T1_REVIEW.md
T1_ACCEPTANCE.md
...
```

Temporary execution/review documents may exist during work.

After acceptance:

```text
merge stable conclusions into current docs
move useful historical evidence to records/
delete obsolete temporary docs
```

---

# 28. Captain-Facing Public API Baseline

Preferred compact public surface:

```text
voyager_overview

crew_catalog
crew_health
crew_recommend

task_dispatch
task_status
task_result
task_cancel
task_resume

artifact_read
artifact_search
```

This is a direction baseline, not a demand to implement all tools immediately.

---

# 29. Public API Anti-Inflation Rule

Do not expose vendor-specific normal-use tools such as:

```text
codebuddy_start
codebuddy_status
qoder_start
qoder_result
```

to the Captain as the primary interface.

Vendor-specific operations belong behind adapters.

Vendor-specific diagnostic tools are allowed only when genuinely necessary.

Default Captain mental model must stay:

```text
Crew
Task
Voyage
Result
```

---

# 30. No New Top-Level State Machines

The following already exist and remain authoritative:

```text
Task
Workflow
PlanExecution
Verification
```

TP-Voyager must project and reuse them.

Do not add:

```text
VoyageTaskStatus
CrewTaskStatus
DispatchTaskStatus
CaptainTaskStatus
```

that duplicate existing Task truth.

Voyage state should be derived/projection-oriented wherever possible.

---

# 31. Schema Change Rule

A new table is allowed only when:

```text
The data must survive process restart
AND
No existing durable table can represent the responsibility cleanly
```

Before adding a table, document:

```text
Why persistence is required
Why existing tables are insufficient
What is the Source of Truth
How migration works
What tests are added
```

Default preference:

```text
0 new tables
```

for ordinary application-level features.

---

# 32. Directory Change Gate

Any proposal that adds:

```text
A new root directory
A new agent_runtime top-level directory
A new application subdomain
A new backend abstraction layer
```

must answer:

```text
1. What responsibility cannot fit the current structure?
2. Why is reuse impossible?
3. Is this driven by real production evidence?
4. Does this create a second source of truth?
5. Does this increase test surface?
6. Which existing directory becomes simpler?
```

If there is no strong answer:

```text
REJECT
```

---

# 33. Forbidden Architecture Patterns

Do not introduce:

```text
agent_runtime/core/
agent_runtime/platform/
agent_runtime/services/
agent_runtime/common/
agent_runtime/managers/
agent_runtime/engine/
```

as generic dumping layers.

Also avoid:

```text
utils.py
helpers.py
common.py
misc.py
```

becoming large cross-domain containers.

Code should live near the responsibility that owns it.

---

# 34. Dependency Direction

Preferred direction:

```text
api
 ↓
application
 ↓
domain

application
 ↓
backends interface
 ↓
backend implementation

application
 ↓
persistence abstraction / existing service

runtime
 ↓
domain / persistence as required
```

Avoid circular dependencies.

Especially forbidden:

```text
application → api
domain → application
domain → backend
backend → api
persistence → application business logic
```

---

# 35. Captain/Worker Separation

Captain-facing code:

```text
api/
application/voyage/
application/crew/
application/dispatch/
```

Worker-facing vendor integration:

```text
backends/codebuddy/
backends/qoder/
```

Worker-scoped tools, when introduced, must not expose Captain-level dispatch APIs.

A worker must not be able to recursively create arbitrary new workers by default.

---

# 36. First Development Sequence Under This Baseline

The intended initial sequence is:

```text
T0
Target architecture cleanup

T1
Crew Registry

T2
Generic Captain Dispatch

T3
Read-only Worker Closure

T4
Patch Worker Closure
```

Do not create new phases merely because this list ends.

After T4:

```text
Use the system in real work.
```

Future phases must come from real usage evidence.

---

# 37. Structure Freeze After T0

At the end of T0, the following should be treated as frozen:

```text
repository top-level structure
agent_runtime top-level structure
application responsibility slots
backend vendor slots
test classification
current documentation structure
Captain-facing mental model
```

Subsequent work should mainly add or modify files **inside these slots**.

Large restructuring after T0 requires explicit Charter-level review.

---

# 38. AI Implementation Rule

Any execution AI working on TP-Voyager must follow this order:

```text
1. Read docs/architecture/CHARTER.md
2. Read docs/architecture/DIRECTORY_BASELINE.md
3. Read AGENTS.md
4. Locate the existing responsibility slot
5. Modify the smallest required surface
6. Run Smoke + affected tests
7. Report any proposed structure deviation before implementing it
```

An AI must not independently decide:

```text
"The architecture would be cleaner if I reorganize..."
```

and perform the reorganization.

---

# 39. Review Checklist

For every PR/change, reviewer asks:

```text
[ ] Did it stay inside an existing responsibility slot?
[ ] Did it introduce a new public concept?
[ ] Did it duplicate an existing state machine?
[ ] Did it add unnecessary persistence?
[ ] Did it expose vendor details to Captain?
[ ] Did it add unnecessary tests?
[ ] Were obsolete tests removed?
[ ] Did documentation remain current and compact?
[ ] Is WorkBuddy being reintroduced accidentally?
[ ] Are CodeBuddy/Qoder capabilities grounded in official contracts?
```

Any unchecked high-risk item blocks acceptance.

---

# 40. Final Frozen Tree

The long-term target is:

```text
TP-Voyager/
│
├── README.md
├── AGENTS.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
├── pyproject.toml
├── requirements.txt
├── scripts/
│   ├── run_tests.cmd
│   └── start_runtime.cmd
│
├── agent_runtime/
│   │
│   ├── api/
│   │   ├── mcp_server.py
│   │   └── schemas/
│   │
│   ├── application/
│   │   ├── voyage/
│   │   ├── crew/
│   │   ├── dispatch/
│   │   ├── context/
│   │   └── task/
│   │
│   ├── domain/
│   │
│   ├── backends/
│   │   ├── base.py
│   │   ├── codebuddy/
│   │   └── qoder/
│   │
│   ├── runtime/
│   │
│   ├── persistence/
│   │
│   ├── verification/
│   │
│   ├── testing/
│   │
│   └── server.py
│
├── skills/
│
├── tests/
│   ├── smoke/
│   ├── targeted/
│   ├── integration/
│   └── live/
│
└── docs/
    ├── ARCHITECTURE.md
    ├── TESTING.md
    ├── OPERATIONS.md
    ├── BACKEND_CODEBUDDY.md
    ├── BACKEND_QODER.md
    └── records/
```

---

# 41. Final Rule

The structure exists to serve one product idea:

> **The passenger states the destination.  
> The Captain decides the work.  
> TP-Voyager manages the voyage and the crew.  
> CodeBuddy CLI and Qoder CLI execute bounded tasks.**

If a proposed directory, abstraction, state machine, API, or test hierarchy does not directly support that idea, it should not be added.

Preferred behavior:

```text
Fill existing slots.
Do not invent new layers.
Delete obsolete structures.
Keep the Captain interface small.
Keep the Runtime durable.
Keep the Crew bounded.
Keep the test suite proportional.
```
