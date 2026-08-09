# TP-Voyager Project Charter v1.0.3

> **Status:** Revised baseline for v1.0.3. All future requirements, architecture changes, tests, and documentation must obey this charter unless the charter itself is explicitly revised first.

## 1. Product model

```text
Passenger → Captain AI → TP-Voyager → Crew
```

- **Passenger (human):** states destination, constraints, and acceptance expectations.
- **Captain AI:** interprets the goal, decomposes work, understands crew strengths, selects crew, tracks progress, judges risk, reviews results, and owns final delivery.
- **TP-Voyager:** reliable execution/control plane.  It records, dispatches, persists, recovers, constrains, verifies, and returns bounded results.  It is **not** a second Captain.
- **Crew:** bounded external AI workers.  Initial official crew families are **CodeBuddy CLI** and **Qoder CLI**.

## 2. Backend policy

Target backends:

```text
CodeBuddy CLI
Qoder CLI
Future officially documented AI CLI workers
```

WorkBuddy is **not** part of the TP-Voyager target architecture.  Historical WorkBuddy code/data may exist temporarily only as migration/cleanup debt.  No new feature, public API, test baseline, or abstraction may be designed around WorkBuddy.

New CodeBuddy/Qoder capabilities must be grounded in official public documentation or official observable CLI/SDK behavior.  Unknown means unknown; do not infer capabilities from another vendor.

## 3. Reuse the V2 durable core

Reuse before adding.  Do not build a second system for capabilities already present:

```text
Task / Session / Attempt / Event / Idempotency
Lease / Fencing / Cancel / Restart Reconciliation
Workflow / PlanExecution
Context / Knowledge / Tool Runtime
Structured Result / Evidence / Artifact / Verification / Plan Result
SQLite Durable Row = Source of Truth
```

Historical `workbuddy.* /v1` schema strings may remain only when required to read existing durable data.  A schema name is not a supported Backend.

## 4. Captain boundary

The Captain should normally need only a small mental model:

```text
Voyage
Crew
Task
Result
```

The Captain should see compact progress, crew capabilities/health, dispatch status, decisions required, and bounded results.  Vendor CLI flags, ACP framing, SDK callbacks, SQLite internals, raw logs, and full patches stay below the Captain boundary unless explicitly requested for diagnostics.

The Captain decides whom to dispatch. TP-Voyager may expose normalized Crew/model catalogs, provider-declared capability/billing reference metadata, durable historical success/usage facts, and compatible-Crew recommendations, but it must not silently switch backend/model, score/select a model, retry indefinitely, estimate provider charges from public rates, or expand mission scope.

## 5. Worker boundary

Crew receives bounded work.  A worker does not own the mission, decide final completion, or recursively create arbitrary crew tasks by default.

Initial task classes stay intentionally small:

```text
research
repository_research
code_review
small_patch
test_failure_triage
verify_only
```

`repository_research` is a narrowly scoped external-source research contract. The Captain must explicitly provide the public GitHub URL, size ceiling, new target directory, read scope, Crew, and model. Runtime may perform only the bounded source precheck and shallow clone needed to create a local static snapshot; Crew remains on an existing read-only route. The downloaded source must not be executed, built, dependency-installed, modified, overwritten, or used as a reason to enable arbitrary network/shell access. The research report is a Runtime-owned Artifact derived from the Crew result. This contract must reuse the existing Task/Attempt/Evidence/Artifact truth sources and must not become a crawler, Planner, recursive researcher, or generic network fetcher.

Initial execution modes:

```text
read_only
patch
```

Unrestricted direct-write, arbitrary shell, unrestricted network, autonomous repo-wide refactors, and agent societies are not baseline features.

## 6. Scope-creep gate

A proposed feature must satisfy at least one gate:

1. **Captain efficiency:** directly reduces Captain token/context/manual polling/tool-call cost.
2. **Execution safety/reliability:** required for safe worker execution, durability, recovery, permissions, conflicts, verification, or evidence integrity.
3. **Official backend compatibility:** required to correctly support an official CodeBuddy/Qoder capability.

If all three are **no**, default decision is:

```text
REJECT / PARK
```

Explicit non-goals without new real-world evidence:

```text
Unlimited agent swarm
Recursive agent hiring
Autonomous mission expansion
Unlimited retry / silent fallback
Distributed multi-node runtime
Enterprise approval platform
A2A platform
Vector DB / embedding pipeline
Automatic knowledge writeback
Full container orchestration
Team collaboration suite
```

## 7. No-second-system rule

Never introduce a second:

```text
Task state machine
Workflow state machine
Result format
Evidence store
Artifact store
Knowledge system
Retry engine
Session truth
```

New layers must reuse/project existing durable truth.

## 8. Testing charter

Tests protect current behavior; they are not a historical museum.

Normal development:

```text
Smoke + directly affected targeted tests
```

Escalate only when justified:

- Regression: shared durable/public contract boundaries.
- Live: real CodeBuddy/Qoder auth/CLI/SDK/ACP/model/permission changes.
- Release/Stress: formal release, major schema, core lifecycle, or major adapter contract change.

A multi-hour release suite is never a routine development step.

Rules:

- one behavior → one primary test;
- table-drive equivalent cases;
- removed feature → remove its production code + current tests + current docs;
- do not keep every historical version test forever;
- every new test must protect new behavior, a real regression, or a critical boundary.

## 9. Development direction

Initial sequence is fixed:

```text
T0  Target architecture cleanup
T1  Crew Registry
T2  Generic Captain Dispatch
T3  Read-only Worker Closure
T4  Patch Worker Closure
```

After T4, use TP-Voyager in real work.  Do not invent T5/T6/T7 merely because the list ended; further work must be driven by actual usage evidence.

## 10. Final direction

> **The passenger states the destination.  The Captain AI decides how to get there.  TP-Voyager reliably manages the voyage and the crew.  CodeBuddy CLI and Qoder CLI perform bounded work.**

Preferred choices:

```text
Simple over complete
Explicit over automatic
Reuse over rebuilding
Bounded over unrestricted
Evidence over claims
Current need over speculative future
Small test surface over historical accumulation
```
