# Data-Driven Routable Model Catalog

TP-Voyager v1.0.7 keeps `crew_catalog(include_models=true)` as a **decision-support catalog**, not a model selector. Runtime merges provider availability, operator authorization, standardized capability evidence, and Runtime observations, while Captain still explicitly chooses Crew + model + supported effort.

## 1. Fact ownership

```text
Provider live catalog
  owns: backend model id / display name / availability / context / supported parameters

~/.tp-voyager/config.json / dispatch
  owns: authorization / allowlist

model_routing_profiles.json
  owns: canonical identity projection / persisted Scorecard / recommended work / risk boundaries

Model Evaluation Standard v1
  owns: source policy / evidence comparability / Scorecard / Tier authority

Runtime durable Evidence
  owns: observed task success / duration / tokens / provider-reported usage

Captain
  owns: final Crew / model / effort choice
```

TP-Voyager does **not** perform online benchmark scraping, automatic model selection, automatic fallback, or public-price cost estimation.

## 2. Bundled baseline and operator materialization

If `${TP_VOYAGER_HOME}/model_routing_profiles.json` is absent, Runtime loads the bundled baseline read-only. To materialize an operator copy:

```bash
tp-voyager model-routing-init
```

The v1.0.7 loader accepts both routing-profile schemas:

```text
tp-voyager.model_routing_profiles/v1
tp-voyager.model_routing_profiles/v2
```

Reading v1 is compatible and **does not rewrite the file**. To persist v2 explicitly:

```bash
tp-voyager model-routing-migrate --dry-run
tp-voyager model-routing-migrate --write
```

See `docs/MODEL_EVALUATION_STANDARD.md` for migration guarantees.

## 3. Authorization is not capability

`~/.tp-voyager/config.json` contains the hard authorization policy:

```json
{
  "dispatch": {
    "allowed_models": [
      "codebuddy:hy3",
      "codebuddy:deepseek-v4-flash",
      "qoder:Lite",
      "qoder:qmodel_38max"
    ]
  }
}
```

A high Tier does not authorize a route, and an allowed route does not imply a high Tier.

## 4. Model Evaluation Standard v1

The current evidence contract is documented in:

```text
docs/MODEL_EVALUATION_STANDARD.md
```

The central rule for fixed models is:

```text
Standard Evidence
    -> persisted Scorecard snapshot
    -> calibrated tier_rules/v1
    -> Scorecard.tier  [authoritative]
```

The old operator-written Tier is preserved only as:

```text
legacy_capability_tier
```

For calibrated v2 profiles, Runtime requires:

```text
capability_tier == scorecard.tier
tier_authority == standard_v1
```

A mismatch fails closed.

## 5. Transitional v1 -> v2 semantics

Before a migrated v1 operator profile has a calibrated persisted Scorecard, fixed models normalize to:

```json
{
  "capability_tier": "UNCLASSIFIED",
  "legacy_capability_tier": "L2",
  "tier_authority": "standard_v1_uncalibrated",
  "scorecard": null
}
```

Migration itself never invents a new Tier.

## 6. Dynamic Qoder tiers

TP-Voyager intentionally supports four dynamic Qoder tiers:

- Ultimate
- Performance
- Efficient
- Lite

They project as:

```json
{
  "provider_identity": "dynamic_tier",
  "provider_tier_label": "Ultimate",
  "capability_tier": "DYNAMIC",
  "tier_authority": "provider_dynamic",
  "scorecard": null
}
```

`qoder:auto` is retired by TP-Voyager local policy and is absent from the bundled routing baseline. Dynamic tiers never borrow a fixed-model Scorecard.

## 7. Current fixed-model baseline

The current account snapshot contains 16 fixed canonical models. Formal Standard v1 Tier is deliberately conservative:

| Canonical model | Standard Tier | Evidence state |
|---|---|---|
| `kimi-k3` | L3 | compliant current Primary |
| `glm-5.2` | L2 | compliant current Primary |
| `glm-5.1` | L2 | compliant current Primary |
| `qwen3.7-plus` | L2 | compliant current Primary |
| `kimi-k2.6` | L1 | compliant current Primary |
| all other fixed models in the current cohort | UNCLASSIFIED | insufficient compatible Primary evidence |

`UNCLASSIFIED` is intentional. Provider claims, legacy Tier, model-level composite intelligence indices, or a preference leaderboard cannot fill a missing Primary-evidence requirement.

Fresh-release audit rule: an exact new release may be fully routable and still be `UNCLASSIFIED` while independent benchmark providers catch up. Current examples are `DeepSeek-V4-Flash-0731`, `DeepSeek-V4-Pro-0813`, and GLM-5.3. Do not inherit predecessor scores across those release boundaries.

Detailed research and calibration records live under `docs/records/`.

## 8. Evidence classes

Current profiles may contain both:

```text
benchmark_evidence   -> immutable legacy_v1 history
standard_evidence    -> Model Evaluation Standard v1 records
```

Legacy rows are preserved for historical comparison but do not enter the Standard v1 Scorecard automatically.

Standard Evidence distinguishes:

```text
model_only
model_agent
preference
provider_claim
operator_observed
```

A model+agent/harness score is never silently collapsed into a pure model score.

## 9. Benchmark version and double-counting isolation

Formal Tier uses only benchmark versions explicitly accepted by the current tier-rules calibration. For example, Terminal-Bench 2.0 and 2.1 are not assumed comparable.

When a composite index contains component benchmarks, TP-Voyager prefers component records for capability dimensions and prevents the composite from being counted again.

## 10. Trusted local evidence refs

Local research files can still be bound with hash-verified `evidence_refs`. Absolute directories are configured under:

```text
~/.tp-voyager/config.json -> trusted_roots.model_evidence
```

Profiles store only alias + relative path + expected SHA-256. Runtime does not expose the trusted root absolute path or inject the Markdown body into Crew prompts.

This provenance mechanism is independent of Standard Evidence score computation.

## 11. Catalog projection

A fixed-model route with calibrated evidence can project:

```json
{
  "route_id": "codebuddy:glm-5.2",
  "allowlist_status": "allowed",
  "routable": true,
  "capability_profile": {
    "canonical_family": "glm-5.2",
    "capability_tier": "L2",
    "legacy_capability_tier": "L3",
    "tier_authority": "standard_v1",
    "scorecard": {
      "coverage": "high",
      "confidence": "high",
      "tier": "L2"
    }
  }
}
```

The Scorecard is a **persisted maintenance snapshot**. Runtime validates it when profiles load; it does not recompute web benchmark data on every `crew_catalog` call.

## 12. Provider effort remains separate

`recommended/suggested effort` in a capability profile is advisory. Actual effort support comes from the live provider/backend capability contract. Captain may pass an effort only when the selected backend route supports it.

## 13. Operator maintenance

Manual maintenance flow:

```text
confirm backend identity
-> research under Source Registry
-> append immutable Standard Evidence
-> provenance approval
-> build persisted Scorecard
-> validate
-> project to Captain
```

Commands:

```bash
tp-voyager model-routing-init
tp-voyager model-routing-migrate --dry-run
tp-voyager model-routing-migrate --write
tp-voyager model-evaluation-validate
```

`model-evaluation-validate` is read-only and performs no network access.

## 14. Records

Current records:

```text
docs/records/TP_VOYAGER_V107_MODEL_SCOPE_2026-08.md
docs/records/TP_VOYAGER_MODEL_IDENTITY_SNAPSHOT_2026-08.md
docs/records/TP_VOYAGER_MODEL_EVALUATION_RESEARCH_2026-08.md
docs/records/TP_VOYAGER_MODEL_PROVIDER_RESEARCH_2026-08.md
docs/records/TP_VOYAGER_MODEL_TIER_CALIBRATION_2026-08.md
docs/records/TP_VOYAGER_MODEL_EVALUATION_BASELINE_2026-08.md
```
