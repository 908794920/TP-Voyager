# TP-Voyager v1.0.6 Model Routing Benchmark Baseline

> Snapshot: 2026-08-13 (Asia/Singapore)
>
> Purpose: evidence record for the operator-owned `model_routing_profiles.json` baseline. Runtime never computes these tiers online and never auto-selects a model.

## Evidence policy

Capability routing metadata follows these rules:

1. Independent/standardized evaluations are the primary evidence for capability tiers.
2. Provider documentation is primarily used for model identity, context, modality, supported parameters and tier semantics.
3. Benchmark identity is classified as `exact`, `near_exact`, `family`, `predecessor`, `dynamic_tier`, or `missing`.
4. Terminal-Bench values must retain the agent / effort / harness because they are not bare-model scores.
5. Artificial Analysis Intelligence Index v4.1 already incorporates Terminal-Bench 2.1; it must not be blindly double-weighted with the same Terminal-Bench result.
6. Provider reference multipliers are consumption hints only and remain `calculation_allowed=false`.
7. Runtime historical success and Usage Evidence are separate observed facts. Over time they can be more relevant to a specific TP-Voyager route than public benchmark priors.

## Engineering tier meaning

| Tier | Meaning |
|---|---|
| L0 | low-risk, mechanical, easy-to-verify work |
| L1 | routine professional execution |
| L2 | advanced engineering execution / repo work / multi-file debugging |
| L3 | frontier/expert execution, long-horizon or complex cross-module work |
| DYNAMIC | provider-selected bottom model; no fixed model benchmark |
| UNCLASSIFIED | insufficient independent capability evidence |

## Current route baseline

| Route family | Tier | Confidence | Evidence summary |
|---|---|---|---|
| Qoder Lite | L0 | medium | dynamic provider tier; fixed bottom model undisclosed |
| Tencent Hy3 | L1 | medium-high | AA ~41; strong Arena Coding signal but smaller sample; not current top long-horizon agent tier |
| GLM-5.2 | L3 | high | LiveBench 73.2 / Coding 79.7 / Agentic 51.9; AA 51 |
| GLM-5.1 | L2 | high but stale | strong older coding evidence, superseded by 5.2 |
| GLM-5V-Turbo | L1 specialist | medium | strong visual/coding specialty; weak general agentic score |
| MiniMax M3 | L2 | high | LiveBench 67.3 / Coding 68.2 / Agentic 40.7; AA 44 |
| MiniMax M2.7 | L1 | medium | older generation; superseded by M3 |
| Kimi K3 | L3 | high | LiveBench ~78.5 / Coding 81.4 / Agentic 57.6; AA 57; strong Arena Coding/Agent position |
| Kimi K2.7 Code | L2 | high | LiveBench 68.4 / Coding 74.0 / Agentic 45.7; AA 42 |
| Kimi K2.6 | L2 | high but deprecated | LiveBench 70.5 / Coding 78.6 / Agentic 46.9; AA 44 |
| DeepSeek V4 Pro | L2 | high | LiveBench 71.6 / Coding 70.0 / Agentic 42.6; AA 44 |
| DeepSeek V4 Flash 0731 | **L3** | **high** | operator confirms route identity; Artificial Analysis 0731 exact: Index 50, GDPval-AA v2 1559 Elo |
| Qwen3.7 Max | L3 | high | LiveBench 73.1 / Coding 74.2 / Agentic 43.6; AA 46 |
| Qwen3.7 Plus | L1 | medium-high | AA 39 plus multimodal/document evidence |
| Qwen3.8-Max | **L3** | **medium-high** | operator confirms formal-release identity; Arena exact identity with leading Chinese text position and #2 vision position; current Arena evidence still preliminary |
| Cantus | UNCLASSIFIED | low | no sufficient independent benchmark evidence reproduced; high multiplier is not capability evidence |

## Core route corrections from the first v1.0.6 draft

### DeepSeek `deepseek-v4-flash`

Operator confirms that the provider route is **DeepSeek-V4-Flash-0731** rather than the April preview checkpoint.

The first v1.0.6 draft used family-level April evidence and therefore set L2 / medium. That is now obsolete.

Independent evidence for the 0731 checkpoint includes an Artificial Analysis Intelligence Index of **50** and GDPval-AA v2 **1559 Elo**. The operator baseline therefore uses:

```text
canonical_family = deepseek-v4-flash-0731
provider_identity = operator_confirmed
capability_tier = L3
profile_confidence = high
benchmark_model_match = exact
```

This does not mean TP-Voyager should automatically send every hard task to DeepSeek. It means the Captain can treat it as a frontier-level coding/agent candidate while still considering route availability, reference multiplier, task modality and Runtime Evidence.

### Qoder `qmodel_38max`

Operator confirms that the provider route is the **formal Qwen3.8-Max**, not Preview.

Arena independently exposes the formal model identity and places it at the leading end of the text leaderboard for Chinese models and #2 in the vision leaderboard. Because the currently indexed Arena evidence remains preliminary and equivalent exact AA/LiveBench coverage is not yet as mature as Kimi K3 / GLM-5.2, the operator baseline uses:

```text
canonical_family = qwen3.8-max
provider_identity = operator_confirmed
capability_tier = L3
profile_confidence = medium-high
benchmark_model_match = exact
```

## Core operator-local source

The four core delegation routes are additionally grounded in the user's maintained model-routing research document:

```text
Qoder Lite
Tencent Hy3
DeepSeek-V4-Flash-0731
Qwen3.8-Max
```

The baseline references that file using a trusted local evidence ref rather than an arbitrary absolute path:

```json
{
  "kind": "trusted_file",
  "root_alias": "operator_model_research",
  "path": "Codex外部模型CLI委派参考.md",
  "sha256": "df278a0d4fe6d32316539feabc210742a349fc0f422f0d6553ace7f0601a1b82"
}
```

The trusted root is operator-owned and is not returned to Captain/Crew. TP-Voyager verifies the relative path and hash only; the document body is never injected into a model prompt by this feature.

## Primary benchmark surfaces used

- LiveBench — https://livebench.ai/
- Artificial Analysis — https://artificialanalysis.ai/
- Arena Text/Coding/Vision — https://arena.ai/leaderboard/
- Terminal-Bench 2.1 — https://www.tbench.ai/leaderboard/terminal-bench/2.1
- SWE-bench official — https://www.swebench.com/

Provider documentation remains a secondary specification source, not the sole basis for capability tiering.
