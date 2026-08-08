# TP-Voyager Architecture

## Product model

```text
Passenger → Captain AI → TP-Voyager → Crew
                                  ├─ CodeBuddy CLI
                                  └─ Qoder CLI
```

The Passenger states the destination. The Captain interprets goals, decomposes work, selects Crew, judges risk, reviews results, and owns final delivery. TP-Voyager is the reliable execution/control plane; it does not become a second Captain.

## Durable foundation

The existing `agent_runtime/` package remains the accepted execution core: Task/Session/Attempt/Event/Idempotency, Lease/Fencing, Cancel, Restart Reconciliation, Workflow/PlanExecution, Context/Knowledge/Tool Runtime, Structured Result, Evidence, Artifact, Verification, and Plan Result. SQLite Durable Row remains Source of Truth.

## Current Captain path

```text
crew_catalog / crew_health / crew_recommend
                 ↓
          task_dispatch
                 ↓
   CodeBuddy or Qoder adapter
                 ↓
      Durable Task lifecycle
                 ↓
      Verification/Evidence
                 ↓
 task_result / voyager_overview
```

Crew recommendation is advisory. Captain dispatch is explicit. No hidden fallback, retry, backend switch, or mission expansion is performed.

## Controlled modes

- `read_only`: bounded analysis. CodeBuddy receives a verified Context Manifest snapshot with native tools disabled; Qoder uses workspace-bounded read/search over official ACP without `--yolo`.
- `patch`: Runtime-owned isolated Git worktree, allowed/forbidden path policy, exact argv command whitelist, patch capture, deterministic verification, and evidence. Passenger worktree is never merged automatically.

## Removed backend

WorkBuddy execution is removed. Only historical data/schema migration compatibility remains; see `records/legacy-workbuddy/DATA_COMPATIBILITY.md`.
