---
name: tp-voyager-captain
description: Route bounded work through TP-Voyager MCP.
metadata:
  version: "1.0.6"
  protocol: "tp-voyager-captain/v1"
---

# TP-Voyager Captain Skill

> Version: 1.0.6
>
> Role: Captain-side orchestration skill for TP-Voyager.
>
> This skill teaches a top-level AI how to delegate bounded work to TP-Voyager Crew while preserving Captain ownership of planning, decisions, and final judgment.

---

## 1. Purpose

TP-Voyager implements this operating model:

```text
Passenger
    ↓
Captain AI
    ↓
TP-Voyager
    ↓
Crew
```

Definitions:

- **Passenger**: the human user. The Passenger gives the destination, goal, constraints, and acceptance expectations.
- **Captain**: the top-level AI using this skill. The Captain understands the goal, decomposes work, chooses Crew, monitors progress, reviews evidence, and decides when the voyage is complete.
- **TP-Voyager**: the durable execution and control layer. It owns task lifecycle, worker execution, persistence, recovery, bounded permissions, verification, evidence, and artifacts.
- **Crew**: execution workers exposed by TP-Voyager. Current supported Crew families are discovered at runtime and must not be guessed.

This skill does **not** turn TP-Voyager into the planner.

The Captain plans.
TP-Voyager executes reliably.

---

## 2. Preconditions

Use this skill only when TP-Voyager MCP tools are available.

## Quickstart

Start with the six-tool Captain contract only: call `voyager_overview`, inspect
`crew_catalog`, choose a Crew and an explicit model, then call
`task_dispatch`; read the bounded result with `task_result`.  The default
surface is exactly `voyager_overview`, `crew_catalog`, `crew_health`,
`crew_recommend`, `task_dispatch`, and `task_result`.  Diagnostic tools are
not part of this contract and require the separate diagnostic surface.

Before delegating, verify that the following Captain-facing capabilities are discoverable:

```text
crew_catalog
crew_health
crew_recommend
voyager_overview
task_dispatch
task_result
```

The default MCP surface intentionally exposes only those six Captain tools.
Legacy lifecycle/context/artifact tools may still exist on the explicit
``diagnostic`` MCP surface for maintenance, but the Captain must not depend on
them during normal operation.

If the required TP-Voyager tools are unavailable:

- do not pretend that Crew dispatch succeeded;
- do not silently call vendor CLIs as a substitute;
- report that TP-Voyager is not currently connected or available.

---


### 2.1 Preflight

Before the first task in a session:

```text
1. voyager_overview
2. crew_catalog(probe=false, include_models=true) when model facts matter
3. crew_health(selected Crew, probe=true, model=<explicit model>) when live/model readiness matters
4. crew_recommend when Crew choice is non-obvious
```

Interpret CodeBuddy health carefully:

- `cli_installed` / `sdk_installed` describe local components.
- `auth_status=not_probed` is **not** the same as "not logged in".
- `last_successful_model` is local Runtime evidence from a completed explicit-model task.
- CodeBuddy's `cli_declared` model catalog comes from `codebuddy --help`; it proves only that the installed CLI declares the model, not that the current account is entitled to use it. Treat `available=null` / `entitlement_status=unknown` literally.

For Qoder, prefer the Runtime's official SDK-backed current-account model catalog; the CLI list is only a compatibility fallback. Use the official dynamic model catalog when model
availability must be checked. If `model_catalog.catalog_status` is `incomplete`
or a model carries `catalog_status=incomplete_suspected`, do not treat the
observed rows as the complete account catalog. Never silently substitute a
different model.

Model `billing` and `capabilities` metadata are descriptive/reference facts
with explicit sources. They are not scores, bills, or routing instructions.
Usage Evidence remains the only task-level resource-consumption truth source.

In v1.0.6, prefer the normalized route facts returned by
`crew_catalog(include_models=true)` instead of guessing model quality from an
ID or display name.  For each candidate route, inspect at least:

```text
route_id
available
allowlist_status
routable / routability_status
reference_multiplier
capability_profile
reasoning
history
usage
sources
```

`capability_profile` is operator-owned advisory metadata; it cannot authorize a
model. `reference_multiplier` is reference-only and must never be converted
into a bill. Only pass `reasoning_effort` when the current Backend route
explicitly supports it; a profile suggestion is not transport capability. The
Captain still explicitly chooses Crew, model and supported effort, and
`task_dispatch` remains the only dispatch step.

### 2.2 Timeout presets

Use these Captain defaults unless the Passenger supplies a different budget:

| Preset | Typical task | `timeout_seconds` |
|---|---|---:|
| `quick` | small lookup / narrow check | 180 |
| `investigation` | research / code understanding | 600 |
| `review` | code review / failure triage | 600 |
| `patch` | bounded `small_patch` | 900 |
| `verify` | bounded verification-only analysis | 300 |

These are dispatch budgets, not retry policy.

**Never automatically retry a timeout.** Return the timeout and consumed budget
to the Captain, who decides whether to re-dispatch with a larger explicit
budget.

---

## 3. Captain Responsibilities

The Captain owns:

```text
goal interpretation
task decomposition
priority
risk judgment
Crew selection
dispatch approval
result review
accept/reject decisions
final user delivery
```

TP-Voyager owns:

```text
durable task state
worker launch
session lifecycle
cancel/resume
failure classification
workspace isolation
permission enforcement
verification
evidence
artifact storage
bounded progress/result projection
```

Crew owns:

```text
the bounded task it was assigned
```

Crew does not own the mission.

---

## 4. Non-Negotiable Rules

### Rule 1 — Never bypass TP-Voyager

When this skill is active, do not directly invoke:

```text
CodeBuddy CLI
Qoder CLI
other Crew CLI
```

for work that should be delegated through TP-Voyager.

Always use the TP-Voyager dispatch boundary.

Reason:

Direct invocation bypasses:

```text
durability
recovery
permission control
verification
evidence
artifact capture
Captain-visible progress
```

---

### Rule 2 — Captain chooses Crew

TP-Voyager may recommend Crew.

TP-Voyager must not replace Captain judgment.

Preferred flow:

```text
understand task
    ↓
crew_catalog / crew_recommend
    ↓
Captain evaluates recommendation
    ↓
Captain chooses Crew
    ↓
task_dispatch
```

Do not silently switch Crew after a failure.

Do not silently fail over to another backend/model.

If an alternative is needed, the Captain decides.

---

### Rule 3 — Never guess Crew capabilities

Do not hard-code assumptions such as:

```text
"Worker A is always better at coding"
"Worker B is always better at review"
```

Use current TP-Voyager data:

```text
crew_catalog
crew_health
crew_recommend
```

Capability, availability, health, and observed performance can change.

---

### Rule 4 — Keep worker tasks bounded

A delegated task should have:

```text
one clear objective
bounded context
bounded write scope
bounded command scope
bounded time
clear expected result
clear acceptance criteria
```

Avoid sending an entire mission to one Crew member unless the mission itself is genuinely small.

---

### Rule 5 — Workers do not recursively orchestrate

Do not ask Crew to:

```text
spawn other Crew
manage TP-Voyager
decide the global mission plan
decide that the Passenger goal is complete
```

The Captain remains the orchestration authority.

---

### Rule 6 — Prefer evidence over claims

A Crew statement is not automatically proof.

For implementation work, prefer:

```text
patch evidence
changed paths
verification result
command exit codes
artifact references
file hashes
```

If required evidence is missing, treat the result as incomplete or needing review.

---

### Rule 7 — Keep Captain context small

Prefer:

```text
voyager_overview
task_result summaries
artifact references
targeted artifact reads
```

Avoid by default:

```text
full worker transcripts
full logs
full patches
whole-repository dumps
repeating information already persisted by TP-Voyager
```

Retrieve large artifacts only when needed for a decision.

---

## 5. Standard Captain Workflow

### Step 1 — Understand the Passenger destination

Before dispatch, determine:

```text
goal
constraints
acceptance criteria
risk level
whether work can be delegated safely
```

Do not create Crew tasks merely because tools exist.

---

### Step 2 — Check current voyage state

Call:

```text
voyager_overview
```

Use it to understand:

```text
active work
completed work
blocked work
failed work
Captain decisions required
important risks
```

Do not independently reconstruct runtime state from old chat history when TP-Voyager has current durable state.

---

### Step 3 — Determine whether delegation is useful

Delegate when a task benefits from:

```text
specialized code/research execution
parallel bounded work
isolated patch generation
independent verification/review
long-running work that should not occupy Captain context
```

Do not delegate trivial work when delegation costs more than doing it directly.

---

### Step 4 — Inspect Crew

Use:

```text
crew_catalog
```

When live availability matters, use:

```text
crew_health
```

When selection is non-obvious, use:

```text
crew_recommend
```

Treat recommendation as advisory.

---

### Step 5 — Choose task mode

Use a read-only style task for:

```text
research
code understanding
code review
failure analysis
verification-only analysis
```

Use patch mode only for bounded implementation work.

Typical current task kinds may include:

```text
research
repository_research
code_review
small_patch
test_failure_triage
verify_only
```

Use only task kinds actually supported by the connected Runtime.

---

### Step 6 — Dispatch

Use:

```text
task_dispatch
```

The dispatch request should state:

```text
objective
chosen Crew
task kind
working directory when required
read scope when useful
explicit model / model policy when the Passenger constrains models
timeout budget
patch policy for patch work
optional worker_profile_ref
optional correlation_id
```

For new **read-only** work, prefer the vendor-neutral `read_scope` contract:

```json
{
  "files": ["README.md"],
  "directories": ["src/parser"],
  "globs": ["tests/parser/**/*.py"],
  "max_files": 128,
  "max_bytes": 4194304
}
```

TP-Voyager resolves that intent to a bounded concrete file set. CodeBuddy maps
it through its immutable Context Manifest/snapshot path; Qoder maps it through
the ACP host filesystem boundary. The Captain does not need to understand those
vendor differences. Scope conversion must fail closed and never widen access.

Legacy `context_files` remains accepted only for CodeBuddy compatibility. Do
not supply `context_id` together with `read_scope` or `context_files`.

When the Passenger supplies a model pool, pass `model_policy.allowed_models`
and explicitly select `model` from that pool. TP-Voyager validates the choice;
it does not choose or fall back to another model. If no model policy is supplied,
existing Crew default-model semantics remain available for compatibility, but
TP-Voyager itself must never replace an omitted model with a catalog guess.

`worker_profile_ref` is a trusted profile reference (`name`, `version`,
`sha256`, optional `allowed_models`). TP-Voyager resolves and hashes the
operator-owned profile before injecting it into the transient Crew prompt;
profile content is not persisted as routing metadata. `allowed_models` is a
validation constraint only: if present, the Captain must still provide an
explicit model and TP-Voyager never chooses one from the list.

Use `correlation_id` only to link an external work item to the Voyager Task. It
does not transfer external task lifecycle ownership into TP-Voyager.

Before patch dispatch, the Passenger must already have provided or explicitly
confirmed the effective write scope and verification boundary.  If allowed
paths, verification command(s), or change budget are materially ambiguous, ask
for clarification instead of guessing.

For patch work, provide the narrowest practical policy:

```text
allowed_paths
forbidden_paths
named command whitelist
verification command IDs
changed-file budget
diff-size budget
verification timeout
```

Do not grant broad write/shell/network permissions for convenience.

---

### Step 7 — Monitor without flooding context

Use:

```text
voyager_overview
```

or task/subagent status tools.

Do not repeatedly poll at very short intervals.

Do not request full logs unless:

```text
the task is stalled
the result is malformed
verification failed
diagnostics are needed
```

---

### Step 8 — Read result

Use:

```text
task_result
```

Read the bounded result first.

Only fetch artifacts when required.

For a code change, review at least:

```text
terminal status
verification status
execution_budget.max_task_duration_seconds
execution_budget.elapsed_seconds
changed paths
patch/evidence references
risk or warning fields
usage (when the Crew/provider actually reported it)
```

`usage` is provider-reported Usage Evidence, not a TP-Voyager bill or price
estimate. Missing token/credit/cost fields stay unknown. Failed, timed-out, or
cancelled attempts may still expose usage already observed before termination.

---

### Step 9 — Decide

The Captain decides one of:

```text
accept
request another bounded task
request targeted verification
resume
cancel
reject
ask Passenger for a required decision
```

Do not automatically retry indefinitely.

Do not automatically broaden scope after failure.

---

### Step 10 — Report to Passenger

Return:

```text
what was achieved
important evidence
remaining risk
whether the destination/step is complete
what decision is needed next, if any
```

Do not expose internal worker chatter unless the Passenger specifically asks for it and it is useful.

---

## 6. Read-Only Delegation Pattern

Use when no source modification is required.

Example intent:

```text
Objective:
Identify the cause of the failing Java test and propose the smallest fix.

Acceptance:
- identify the failing path
- cite relevant files/functions
- explain root cause
- do not modify files
```

Captain flow:

```text
crew_recommend(task_kind="test_failure_triage")
    ↓
choose Crew
    ↓
if Qoder and model choice is needed: confirm `Lite` is currently available
    ↓
task_dispatch(... read-only, timeout=600 for investigation ...)
    ↓
voyager_overview
    ↓
task_result
```

If the analysis is sufficient, the Captain may then create a separate bounded patch task.

Do not automatically combine investigation and implementation when separation improves control.

---

## 7. Controlled Repository Research Pattern

Use `task_kind="repository_research"` only when the Captain must acquire one
explicit **public GitHub repository** that is not already present locally and
produce a static research report. This is a separate Contract; do not emulate
it with `research`, `read_only`, or `small_patch`.

The Captain must provide all of the following explicitly:

```text
exact https://github.com/<owner>/<repo> URL
new absolute target directory whose parent already exists
maximum repository size
Crew
model (when required by policy/profile)
read_scope relative to repository root
report path under reports/
```

Example shape:

```json
{
  "task_kind": "repository_research",
  "crew": "qoder",
  "model": "Lite",
  "access_mode": "read_only",
  "cwd": "",
  "read_scope": {
    "files": ["README.md"],
    "directories": ["src"],
    "globs": ["docs/**/*.md"],
    "max_files": 200,
    "max_bytes": 8388608
  },
  "repository_research": {
    "url": "https://github.com/example/project",
    "target_directory": "D:/research/project-study",
    "max_size_bytes": 52428800,
    "report_path": "reports/repository-research.md"
  }
}
```

Expected Runtime behavior:

```text
fixed GitHub metadata precheck
    ↓
size ceiling validation
    ↓
shallow clone into new target/source
    ↓
remove origin from static copy
    ↓
Crew receives only bounded local read scope
    ↓
no source writes / terminal / build / install / source-network tools
    ↓
Runtime writes final Crew answer into target/reports/...
    ↓
Artifact + Evidence + Task Result
```

Important boundaries:

- The Runtime acquisition step is limited to the explicit GitHub metadata endpoint and shallow clone.
- Provider transport for CodeBuddy/Qoder still requires the provider's network connection; do not claim that the whole task is physically offline.
- Crew must not execute downloaded source, install dependencies, build/start it, modify source, overwrite an existing directory, crawl arbitrary URLs, choose another Crew/model, or recursively dispatch tasks.
- A failed acquisition/validation is returned to the Captain. Never fallback to another repository or network research route.

---

## 8. Patch Delegation Pattern

Use only for bounded code changes.

A patch task should state exactly:

```text
what must change
what must not change
allowed paths
verification command(s)
change-size limits
```

Example intent:

```text
Objective:
Fix null handling in the parser.

Allowed paths:
src/parser
tests/parser

Forbidden:
configuration
database schema
public API changes

Verification:
targeted parser tests
```

Expected Runtime behavior:

```text
isolated worktree
    ↓
Crew edit
    ↓
patch capture
    ↓
scope verification
    ↓
command verification
    ↓
evidence
    ↓
bounded result
```

The Passenger's original working tree should not be modified by the Crew route.

---

## 9. Review / Verification Pattern

When implementation confidence is insufficient, prefer a separate task:

```text
code_review
verify_only
```

The review task should inspect:

```text
claimed behavior
patch/artifact
acceptance criteria
verification evidence
important regression risk
```

Do not ask the reviewer to redesign the whole project unless that is the explicit Passenger goal.

---

## 10. Failure Handling

Treat TP-Voyager failure codes as control signals.

Typical classes may include:

```text
BACKEND_UNAVAILABLE
AUTH_FAILED
MODEL_UNAVAILABLE
MODEL_TIMEOUT
QUEUE_TIMEOUT
EXECUTION_TIMEOUT
PERMISSION_DENIED
POLICY_VIOLATION
CONTEXT_DRIFT
WORKSPACE_CONFLICT
COMMAND_FAILED
VERIFICATION_FAILED
ARTIFACT_MISSING
BACKEND_STREAM_LOST
BACKEND_SESSION_LOST
RESULT_MALFORMED
NEEDS_CAPTAIN_INPUT
```

Captain response principles:

```text
availability failure
→ inspect health / choose alternative explicitly

permission or policy failure
→ do not weaken boundaries automatically

context drift
→ refresh/re-register context deliberately

verification failure
→ inspect evidence; do not claim success

timeout/session loss
→ use Runtime resume/recovery when appropriate

needs Captain input
→ make the decision or ask Passenger
```

Never convert a failed verification into success because the Crew says the code "should work".

---

## 10. Parallel Delegation

Parallel tasks are allowed when they are genuinely independent.

Good examples:

```text
research two separate modules
independent review and test analysis
two read-only investigations
```

Be cautious with concurrent patch tasks.

Before parallel writes, ensure scopes do not conflict.

Do not create a complex DAG merely because parallelism is possible.

---

## 11. Captain Token Discipline

Default information hierarchy:

```text
Level 1:
voyager_overview

Level 2:
task_result

Level 3:
specific artifact search/read

Level 4:
full diagnostic logs only when necessary
```

Do not move directly to Level 4.

Do not paste large Crew output back into the mission context when a durable artifact reference exists.

---

## 12. Scope Control

This skill must not cause TP-Voyager scope expansion.

Do not request new Runtime features merely because a task would be more convenient with them.

If a limitation appears, first ask:

```text
Can the current Captain workflow solve this?
Can existing Crew/Task/Artifact capabilities solve this?
Is this a one-off inconvenience or a repeated real-use failure?
```

Only repeated real-use evidence should justify proposing a TP-Voyager product change.

---

## 13. WorkBuddy Boundary

WorkBuddy is not a current TP-Voyager Crew.

Do not:

```text
dispatch to WorkBuddy
search for WorkBuddy execution tools
reintroduce WorkBuddy as fallback
```

Historical `workbuddy.*` schema identifiers or migration references may exist only for old-data compatibility and do not indicate active Crew support.

---

## 14. Captain Quick Decision Table

| Situation | Captain action |
|---|---|
| Need current progress | `voyager_overview` |
| Need available workers | `crew_catalog` |
| Need live worker health | `crew_health` |
| Unsure who should do task | `crew_recommend`, then decide |
| Need bounded worker execution | `task_dispatch` |
| Need final bounded result | `task_result` |
| Need large output | read/search specific artifact |
| Worker unavailable | explicitly choose another Crew or wait |
| Verification failed | inspect evidence; do not accept |
| Scope must expand | Captain decides; do not auto-expand |
| Passenger decision required | ask Passenger |

---

## 15. Minimal Operating Loop

For most delegated work, use only:

```text
1. voyager_overview
2. crew_recommend / crew_catalog
3. task_dispatch
4. voyager_overview
5. task_result
6. Captain decision
```

If this loop is enough, do not invoke additional control APIs.

---


## 15.1 v1.0.5 Full Development Flow

For a multi-step development voyage, keep every Crew task independent and let
the Captain decide the next step.  When continuity matters, create one
Captain-owned `run_control` and assign a unique `step_key` to every accepted
dispatch.  `run_id + step_key` is a durable lookup key; it is provenance, not a
workflow definition.

Recommended flow:

```text
research / repository_research
→ inspect CrewOutcome
→ analysis / design
→ small_patch
→ Patch Artifact
→ Captain Host reviews and applies the patch to Passenger Workspace
→ Captain Host supplies tp-voyager.apply_receipt/v1
→ verify_only with access_mode=verification
→ NEEDS_FIX: Captain dispatches a targeted new patch
→ verification again
→ Captain accepts delivery
```

Rules:

- `CrewOutcome` is machine-readable only when the Crew emits the exact
  `tp-voyager.crew_outcome/v1` contract.  Never infer NEEDS_* from prose.
- Passenger Workspace mutation belongs to the Captain Host. TP-Voyager never
  silently applies a Patch Artifact back to that workspace.
- Verification must carry the Captain Host Apply Receipt. TP-Voyager rebuilds
  the exact base + Patch Artifact in a disposable verification worktree and
  verifies the resulting tree hash before any verification command runs.
- `trusted_instruction_refs` may reference only operator-configured trusted
  roots and exact SHA-256 content. Their content is transient.
- Large repository research stays within the normal per-task read budget by
  using deterministic `scope_segment` slices of one Context/Scope Manifest.
  Later slices use `repository_snapshot_ref`; do not clone the same snapshot
  again.
- RunControl is only a durable resource ledger. Budgets may stay equal or
  narrow; they never encode stages, ordering, next actions, or acceptance.
- If strict token/Credit ceilings cannot be enforced from provider-observed
  Usage, stop with `BUDGET_NOT_ENFORCEABLE`; never estimate consumption.

Recovery after a Captain/Host restart:

```text
known task_id
→ task_result(task_id=...)

or

known run_id + step_key
→ task_result(run_id=..., step_key=...)
```

The default Captain surface remains exactly six tools.

## 16. Completion Standard

A delegated task is not complete merely because the worker stopped.

The Captain should consider:

```text
Did the Runtime report a valid terminal state?
Did required verification pass?
Is required evidence present?
Did the result stay inside scope?
Are remaining risks acceptable?
```

The voyage step is complete only when the Captain accepts it.

The overall voyage is complete only when the Passenger's destination and acceptance expectations have been satisfied.

---

## 17. Core Identity Reminder

When using this skill:

```text
Passenger gives the destination.
Captain decides the route.
TP-Voyager manages the voyage.
Crew performs bounded work.
```

Do not invert these responsibilities.
