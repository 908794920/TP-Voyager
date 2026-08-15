# Model Evaluation Standard v1

TP-Voyager uses this standard to keep model capability evidence comparable across users, dates, providers, agents, harnesses, and benchmark revisions. The standard is **maintenance-time only**: Runtime does not crawl leaderboards, periodically refresh scores, auto-select models, or calculate a single synthetic total score.

## 1. Authority model

For a **fixed model**:

```text
Standard Evidence
    -> persisted Scorecard snapshot
    -> calibrated model_tier_rules/v1
    -> Scorecard.tier  (authoritative)
    -> routing_profiles.capability_tier  (validated projection/cache)
```

`legacy_capability_tier` is historical context only. If persisted `capability_tier` disagrees with a calibrated persisted `scorecard.tier`, profile loading fails closed.

For a **dynamic Qoder tier**:

```text
Ultimate / Performance / Efficient / Lite
    -> capability_tier = DYNAMIC
    -> tier_authority = provider_dynamic
    -> scorecard = null
```

A dynamic tier must never borrow the scorecard of whichever hidden model happened to back it at one point in time. TP-Voyager intentionally retires `qoder:auto` from its local routing baseline.

## 2. User file compatibility and migration

`ModelRoutingProfiles.load()` accepts both:

- `tp-voyager.model_routing_profiles/v1`
- `tp-voyager.model_routing_profiles/v2`

Reading v1 performs an **in-memory, read-only normalization**. It does not modify the operator file.

Use the explicit migration command when you want to persist v2:

```bash
tp-voyager model-routing-migrate --dry-run
tp-voyager model-routing-migrate --write
```

Before calibrated Scorecards exist, migrated fixed models use:

```json
{
  "capability_tier": "UNCLASSIFIED",
  "legacy_capability_tier": "L2",
  "tier_authority": "standard_v1_uncalibrated",
  "scorecard": null
}
```

Migration is semantic-preserving, atomic, and idempotent. Failure must leave the original file unchanged.

## 3. Source Registry

Bundled source policy lives in:

```text
agent_runtime/application/crew/model_evaluation_sources.baseline.json
```

Roles are intentionally distinct:

- `primary` — independent, current, sufficiently reproducible evidence allowed into formal Tier computation.
- `supplemental` — useful context that is not strong/comparable enough to drive Tier.
- `provider` — official vendor identity/spec/benchmark claims; never independently promotes L2/L3.
- `preference` — human preference/Elo signal; never independently determines formal Tier.
- `historical` — legacy/archived evidence for comparison only.
- `experimental` — new source under evaluation.

A source also declares the context it requires. Missing source-required context means the evidence **cannot be Primary**.

## 4. Standard Evidence Record

Schema:

```text
tp-voyager.model_evidence/v1
```

A Primary record must include all context required by its Source Registry entry. In particular, formal scoring requires an exact model identity and a reproducible benchmark/version context.

### Valid Primary example

```json
{
  "evidence_schema": "tp-voyager.model_evidence/v1",
  "evidence_id": "aa-cai-v1.3-kimi-k3-terminal",
  "source_id": "artificial_analysis_coding_agent",
  "source_role": "primary",
  "subject_type": "model_agent",
  "model": {
    "tested_model": "Kimi K3",
    "canonical_family": "kimi-k3",
    "model_match": "exact"
  },
  "benchmark": {
    "id": "terminal-bench",
    "version": "AA-CAI-v1.3",
    "task_count": 84
  },
  "execution": {
    "agent": "Kimi Code CLI",
    "agent_version": null,
    "harness": "Kimi Code CLI",
    "harness_version": "AA-CAI-v1.3",
    "reasoning_effort": null,
    "attempts_per_task": 3
  },
  "result": {
    "metric": "pass@1",
    "value": 84.0,
    "scale": "percent"
  },
  "provenance": {
    "observed_at": "2026-08-15",
    "published_at": null,
    "url": "https://artificialanalysis.ai/agents/coding-agents/comparisons/codex-vs-kimi-code-cli",
    "methodology_url": "https://artificialanalysis.ai/methodology/coding-agents-benchmarking",
    "primary_approved_by": "research-review:openai-gpt-5.6-sol-2026-08-15",
    "primary_approved_at": "2026-08-15T09:00:00Z",
    "approval_basis_url": "https://artificialanalysis.ai/agents/coding-agents/comparisons/codex-vs-kimi-code-cli"
  },
  "relationships": {
    "composite_of": [],
    "duplicate_of": null
  }
}
```

### Invalid workflow example

Do **not** do this:

```text
1. Open a leaderboard.
2. Copy one aggregate score.
3. Decide "this looks frontier".
4. Edit capability_tier to L3.
```

That loses model identity, benchmark version, agent/harness, attempts, provenance, and authority. In v2 the static tier is not an operator override.

## 5. Subject types

- `model_only` — evidence intended to describe the model independently of an agent harness.
- `model_agent` — result is a model + agent/harness pair; do not collapse it into a pure model score.
- `preference` — human preference result such as Elo/rank.
- `provider_claim` — vendor-published capability/benchmark claim.
- `operator_observed` — controlled local/TP-Voyager observation.

`model_agent` results from two different agents or harnesses remain separate evidence even if the model name is identical.

## 6. Exact identity gate

Formal Primary scoring requires:

```text
model.model_match = exact
```

`family`, `near_exact`, `predecessor`, `dynamic_tier`, and `missing` can be retained for context but cannot be silently promoted into formal Tier input.

Example: if a backend displays `Qwen3.8-Max` but the public source explicitly names `qwen3.8-max-preview`, that source is not exact evidence for the backend model until equivalence is proven.

## 7. Benchmark/version isolation

A benchmark name is not enough. Version/harness changes can alter difficulty and comparability.

```text
Terminal-Bench 2.0 != Terminal-Bench 2.1
```

Tier rules therefore contain an explicit `accepted_versions` calibration domain. Evidence outside that domain is still retained, but it is not automatically averaged into the current Scorecard.

## 8. Composite and duplicate protection

If a composite index already contains component benchmarks, do not count the composite and its components twice.

- Prefer component evidence for capability dimensions.
- Composite evidence may remain Supplemental/display-only.
- `relationships.duplicate_of` prevents duplicate rows from affecting computation.

The model-level Artificial Analysis Intelligence Index is Supplemental in the current standard and is not converted into a coding score.

## 9. Provenance approval

Schema validation can prove that an approval trail exists; it cannot prove that a human/agent transcribed `59.6` from a web page correctly. Primary therefore requires:

- `primary_approved_by`
- `primary_approved_at`
- `approval_basis_url`

The approver is responsible for checking that:

1. the result page actually contains the tested model/result;
2. the methodology identifies the benchmark/version and required execution context;
3. aliases have not been treated as exact without evidence;
4. a provider claim has not been misclassified as independent;
5. the metric, scale, and task subset were transcribed correctly.

## 10. Research matrix status

For every canonical model × research source, record one of:

- `FOUND`
- `NOT_LISTED`
- `IDENTITY_AMBIGUOUS`
- `SOURCE_UNAVAILABLE`
- `NOT_APPLICABLE`

This is important: **not found is evidence about research completeness; it is not score zero**.

## 11. Scorecard dimensions

Standard v1 maintains five capability axes:

- `repository_engineering`
- `terminal_agentic`
- `codebase_understanding`
- `general_coding`
- `multimodal_coding`

A dimension may be `measured`, `supplemental`, or `N/A`. `N/A` and `UNCLASSIFIED` are valid outcomes. Guessing a Tier to avoid missing data is not valid.

`multimodal_coding=N/A` does not reduce ordinary coding Tier; it only prevents claims of multimodal specialty without evidence.

## 12. Tier rules

Tier rules are versioned in:

```text
agent_runtime/application/crew/model_tier_rules.baseline.json
```

The current calibration requires multiple independent Primary engineering families for L2/L3 and exact identity. Provider claims and preference evidence cannot independently promote a model.

The rules are calibrated **after** the research cohort is collected. A future benchmark revision requires a new calibration/rules revision rather than silently reusing old thresholds.

## 13. Updating a model manually

Recommended maintenance flow:

```text
1. Confirm backend route id and canonical model identity.
2. Check Source Registry status and required fields.
3. Research each Primary candidate source.
4. Record FOUND / NOT_LISTED / IDENTITY_AMBIGUOUS / ... for every source.
5. For FOUND, create an immutable Standard Evidence record.
6. Manually verify provenance and fill approval trail.
7. Run validator.
8. Rebuild persisted Scorecard using the currently calibrated rules.
9. Persist Scorecard + derived capability_tier together.
10. Run validator again and review legacy -> standard Tier changes.
```

Validation command:

```bash
tp-voyager model-evaluation-validate
```

The validator is read-only, performs no network access, and does not modify model routing or dispatch policy.

## 14. Legacy evidence

All v1 benchmark rows remain immutable historical evidence marked `legacy_v1`. They may be displayed and compared historically, but they do not enter Standard v1 Scorecard computation unless a new Standard Evidence record is created with the required context.

Never rewrite a legacy row in-place to make it look Standard-compliant.

## 15. Runtime boundary

Model Evaluation Standard changes evidence quality, not execution authority:

- Captain still explicitly chooses Crew + model + supported effort.
- `config.json.dispatch` remains the model allowlist authority.
- Provider live catalog remains the availability/identity fact source.
- TP-Voyager does not auto-select a model from the Scorecard.
