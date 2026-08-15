# TP-Voyager v1.0.7 Model Evaluation Scope — 2026-08

## Decision

This work upgrades the model-evaluation evidence contract on the **current TP-Voyager v1.0.7 baseline**. It does not predeclare a new product release.

## Scope-creep gate

The change is accepted under Charter Section 6 because it directly improves all three permitted axes:

- **Captain efficiency** — the current account-visible model cohort changed materially, and the old free-form tier/evidence fields do not let Captain distinguish fixed-model evidence from dynamic tiers or agent+harness results.
- **Execution safety** — authoritative tiers are now fail-closed, versioned, provenance-gated, and cannot be promoted by provider claims or preference Elo alone.
- **Official backend compatibility** — current CodeBuddy/Qoder visibility contains new and duplicated provider models; identity and route facts are separated so backend aliases are not invented from display names.

This is not a cosmetic schema refactor. The previous baseline had 26 materialized routes including `qoder:auto`; the current operator-supplied account snapshot has 27 visible entries, 16 fixed canonical models, and 4 Qoder dynamic tiers, with GLM-5.3 newly visible. The Qoder internal route id for GLM-5.3 is not available in this build environment, so it is recorded as unresolved rather than guessed.

## Explicit non-goals

- No automatic web fetching or scheduled benchmark refresh.
- No model auto-selection or fallback.
- No new Captain MCP tool.
- No new SQLite table or state machine.
- No pseudo single aggregate score.
- No product version bump in this task.

## Current account-visible snapshot

| Backend | Visible entries | Fixed | Dynamic |
|---|---:|---:|---:|
| CodeBuddy | 12 | 12 | 0 |
| Qoder | 15 | 11 | 4 |
| Total | 27 | 23 route appearances | 4 |

After canonical de-duplication there are **16 fixed canonical models**. Seven fixed models appear on both backends.

## Local routing policy

`qoder:auto` is explicitly retired for TP-Voyager and is not included in the current routing baseline. Generic Qoder documentation may continue to describe Auto; this repository intentionally follows the operator's current account policy instead of enabling it implicitly.
