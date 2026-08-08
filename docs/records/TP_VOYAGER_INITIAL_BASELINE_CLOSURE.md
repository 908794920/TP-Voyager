# TP-Voyager Initial Real-Use Baseline Closure

Status: **ACCEPTED**

This record closes the T0-T4 incubation sequence and the final WorkBuddy architecture-debt removal.

## Accepted capabilities

- T0 target architecture
- T1 Crew Registry
- T2 Captain boundary
- T3 controlled read-only CodeBuddy/Qoder routes
- T4 isolated controlled patch CodeBuddy/Qoder routes
- historical WorkBuddy execution backend/public tools/current tests removed
- historical `workbuddy.* /v1` data/schema and old-home migration compatibility retained where required
- unsupported historical in-flight WorkBuddy sessions reconcile to `LOST`; no Crew substitution occurs
- Qoder production execution surface is controlled `acp_read_only` / `acp_patch` only; no `--yolo` launch path remains

## Real CLI evidence

- T3 live acceptance: 6/6 PASS
- T4 live acceptance: 7/7 PASS

See the adjacent T3/T4 acceptance reports.

## Final cleanup automated evidence

The final cleanup intentionally did not repeat Stress/Release. It changed current backend/public/test boundaries, so the justified gates were targeted tests, Smoke, and maintained Regression.

- targeted current cleanup: 74/74 PASS before final controlled-route tightening
- Qoder/control-route targeted follow-up: 35/35 PASS
- cancel/heartbeat transformed stress subset: 4/4 PASS
- Smoke: 17/17 targets PASS (37 methods)
- Regression: 28/28 maintained modules PASS
- Python compile check: PASS

The development sandbox did not contain the real `mcp` package; MCP-importing automated tests used a process-only identity stub that is not part of the repository. Real MCP/CLI behavior is grounded by the accepted Windows T3/T4 live reports.

## Test-suite cleanup

Retired WorkBuddy transport tests and historical PR-closure suites that depended on removed execution code were deleted rather than preserved through compatibility shims. The maintained profiles are now only:

```text
smoke
current
regression
stress
release
```

There is no maintained historical `audit` profile.

After this closure TP-Voyager enters real-use mode. New feature phases require production evidence and Charter Gate approval.
