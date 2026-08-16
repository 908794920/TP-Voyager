# TP-Voyager Current Model Evaluation Baseline — 2026-08

This record describes the persisted Standard v1 scorecard snapshot shipped on the **v1.0.7 code baseline**. `legacy_capability_tier` is historical context only. `Scorecard.tier` is authoritative for fixed models.

| Canonical model | Legacy tier | Standard tier | Coverage | Confidence | Basis |
|---|---|---|---|---|---|
| `cantus` | UNCLASSIFIED | UNCLASSIFIED | low | low | insufficient compatible Primary evidence |
| `deepseek-v4-flash` | L3 | UNCLASSIFIED | low | low | exact `DeepSeek-V4-Flash-0731`; current AA v4.1.1 supplemental score = 52; no compatible Primary coding-agent evidence |
| `deepseek-v4-pro` | L2 | UNCLASSIFIED | low | low | exact `DeepSeek-V4-Pro-0813`; pre-0813 Primary rows retired from current scoring; fresh exact 0813 Primary evidence pending |
| `glm-5.1` | L2 | L2 | high | high | compliant Primary scorecard |
| `glm-5.2` | L3 | L2 | high | high | compliant Primary scorecard |
| `glm-5.3` | — | UNCLASSIFIED | low | low | exact formal GLM-5.3 release confirmed; provider-reported security claims are supplemental; independent Primary evidence pending |
| `glm-5v-turbo` | L1 | UNCLASSIFIED | low | low | insufficient compatible Primary evidence |
| `hy3` | L1 | UNCLASSIFIED | low | low | insufficient compatible Primary evidence |
| `kimi-k2.6` | L2 | L1 | high | high | compliant Primary scorecard |
| `kimi-k2.7-code` | L2 | UNCLASSIFIED | low | low | insufficient compatible Primary evidence |
| `kimi-k3` | L3 | L3 | high | high | compliant Primary scorecard |
| `minimax-m2.7` | L1 | UNCLASSIFIED | low | low | insufficient compatible Primary evidence |
| `minimax-m3` | L2 | UNCLASSIFIED | low | low | insufficient compatible Primary evidence |
| `qwen3.7-max` | L3 | UNCLASSIFIED | low | low | insufficient compatible Primary evidence |
| `qwen3.7-plus` | L1 | L2 | high | high | compliant Primary scorecard |
| `qwen3.8-max` | L3 | UNCLASSIFIED | low | low | insufficient compatible Primary evidence |

## Dynamic Qoder tiers

| Route | Provider label | Capability tier | Authority | Scorecard |
|---|---|---|---|---|
| `qoder:ultimate` | Ultimate | DYNAMIC | provider_dynamic | null |
| `qoder:performance` | Performance | DYNAMIC | provider_dynamic | null |
| `qoder:efficient` | Efficient | DYNAMIC | provider_dynamic | null |
| `qoder:Lite` | Lite | DYNAMIC | provider_dynamic | null |

`qoder:auto` is retired and absent.

## Legacy preservation

The migration retained all 47 legacy benchmark rows associated with non-retired routes byte-for-semantic-value: the only added field is the `legacy_v1` discriminator. Standard evidence is appended separately and never overwrites a historical metric.

## Persisted snapshot rule

Scorecards are persisted maintenance-time snapshots. Runtime `load()` validates them; it does not recompute benchmark scores on every read. When rules are calibrated and a scorecard exists, `capability_tier` must equal `scorecard.tier` or loading fails closed.

## Fresh-release correction

This snapshot distinguishes **model/release identity** from **benchmark coverage**. DeepSeek V4 Flash 0731, DeepSeek V4 Pro 0813, and GLM-5.3 are treated as exact current model identities. `UNCLASSIFIED` for these models means compatible current Primary evidence is insufficient; it does **not** mean the model identity is ambiguous or unofficial.
