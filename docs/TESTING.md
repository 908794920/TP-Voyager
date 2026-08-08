# TP-Voyager Testing

The test suite protects the current supported contract, not every historical implementation.

## Default

```text
Smoke + directly affected targeted tests
```

## Escalation

- `smoke`: fast current structural confidence.
- `current`: current TP-Voyager Captain/Crew/controlled execution surface.
- `regression`: cross-core changes such as durable lifecycle, shared adapter abstraction, public contract, persistence, workflow/recovery.
- `stress`: explicit scheduler/lease/race checks only.
- `release`: formal release gate only.

Real CodeBuddy/Qoder authentication, SDK/ACP invocation, permissions, model discovery, and patch isolation are targeted `live` acceptance, not fake automated PASS.

Do not run broad historical audit or multi-hour Release/Stress for routine changes. When a feature is removed, remove its current production code, current tests, and current docs together.
