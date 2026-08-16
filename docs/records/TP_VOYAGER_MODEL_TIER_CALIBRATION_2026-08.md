# TP-Voyager Model Tier Calibration Record — 2026-08

## Calibration domain

`tp-voyager.model_tier_rules/v1` is calibrated only against the three component benchmarks captured under the Artificial Analysis Coding Agent Index v1.3 methodology domain:

- DeepSWE → `repository_engineering`
- Terminal-Bench → `terminal_agentic`
- SWE-Atlas-QnA → `codebase_understanding`

Accepted persisted evidence version for these components is `AA-CAI-v1.3`. Scores from different benchmark/harness versions are retained as evidence but do not enter this calibration unless a future tier-rules revision explicitly accepts them.

## Thresholds

| Tier | Min primary dimensions | Min independent benchmark families | Agentic/repository required | DeepSWE | Terminal-Bench | SWE-Atlas-QnA |
|---|---:|---:|---|---:|---:|---:|
| L3 | 2 | 2 | yes | 55 | 60 | 30 |
| L2 | 2 | 2 | yes | 35 | 40 | 20 |
| L1 | 1 | 1 | no | 20 | 20 | 10 |
| L0 | 1 | 1 | no | 0 | 0 | 0 |

A model must meet the tier's minimum number of benchmark-family thresholds; a single strong Terminal-Bench score cannot by itself create L2/L3.

## Calibration rationale

The bands were selected after inspecting the current comparable cohort instead of fixing them before data collection. The intent is category separation, not forced distribution:

- L3 requires strong evidence in at least two independent software-engineering dimensions and is reserved for models that clear frontier-like bands in the calibrated cohort.
- L2 requires two meaningful engineering families and prevents a terminal-only spike from being classified as broadly strong.
- L1 allows one credible primary engineering family but does not imply broad long-horizon reliability.
- L0 is only available when formal Primary evidence exists but remains below L1 thresholds.
- No Primary evidence → `UNCLASSIFIED`, never synthetic L0.

Provider claims, model-level composite intelligence indices, preference Elo, legacy scores, and incompatible benchmark versions are excluded from the tier computation.

## Current calibrated outcomes

| Canonical model | Standard Tier | Primary coverage | Reason |
|---|---|---|---|
| `kimi-k3` | L3 | high | clears L3 bands in multiple calibrated engineering families |
| `glm-5.2` | L2 | high | clears L2 bands in Terminal-Bench + SWE-Atlas-QnA |
| `glm-5.1` | L2 | high | clears L2 bands in Terminal-Bench + SWE-Atlas-QnA |
| `qwen3.7-plus` | L2 | high | clears L2 bands in Terminal-Bench + SWE-Atlas-QnA |
| `kimi-k2.6` | L1 | high | formal Primary exists; does not satisfy two-family L2 threshold |

All other fixed models are `UNCLASSIFIED` in this snapshot due insufficient compatible Primary evidence, regardless of legacy tier or provider marketing claim. DeepSeek V4 Pro is specifically excluded from the current calibrated cohort because the current route is `DeepSeek-V4-Pro-0813`, while the previously captured AA Coding Agent rows belong to the earlier preview lineage and cannot be promoted across the release boundary.

## Stability checks

- Supplemental and provider sources are excluded from Tier calculation, so removing them does not alter formal Tier.
- Each formal L2/L3 result requires multiple benchmark families, reducing dependence on one source component.
- Dynamic Qoder tiers are excluded entirely from fixed-model calibration.
- No threshold was moved to preserve a prior legacy tier.

A future benchmark version or changed methodology requires a new calibration record/rules revision rather than silent reuse of these bands.
