# TP-Voyager Model Evaluation Research Record — 2026-08

## Standard

Research status is recorded independently from score. Allowed states are `FOUND`, `NOT_LISTED`, `IDENTITY_AMBIGUOUS`, `SOURCE_UNAVAILABLE`, and `NOT_APPLICABLE`. A missing score is never silently interpreted as zero.

## Source status review

- **Artificial Analysis Coding Agent** — active Primary research source. Current methodology domain used by this baseline is `AA-CAI-v1.3`; component rows preserve model + agent/harness context and are not treated as pure model scores.
- **DeepSWE** — active Primary candidate for repository engineering; direct rows are version/harness sensitive.
- **Terminal-Bench** — active Primary candidate. Current standalone verified benchmark is Terminal-Bench 2.1 and uses Harbor; 2.0/2.1 are not automatically merged.
- **SWE-Atlas-QnA** — active Primary candidate for codebase understanding.
- **Artificial Analysis model-level Intelligence Index** — active Supplemental only. The composite Intelligence Index does not determine coding Tier.
- **Arena Coding / WebDev** — preference evidence only.
- **LiveBench / SWE-bench Pro** — Supplemental because harness/version comparability and saturation must be reviewed per release.
- **LiveCodeBench** — Historical for current frontier evaluation because the public release used by prior research is old.
- **BigCodeBench** — Archived/Historical; no current Tier promotion.

## Primary research matrix

The three direct benchmark columns indicate whether a separate exact public row was ingested directly from that source. For six models, component results are instead captured through Artificial Analysis Coding Agent v1.3 with their agent/harness context; those component results are Primary and are not duplicated as direct-source records.

| Canonical model | AA Coding Agent v1.3 | DeepSWE direct | Terminal-Bench direct | SWE-Atlas-QnA direct | AA model supplemental | Provider official |
|---|---|---|---|---|---|---|
| `cantus` | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS |
| `deepseek-v4-flash` | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `deepseek-v4-pro` | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND |
| `glm-5.1` | FOUND | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `glm-5.2` | FOUND | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `glm-5.3` | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND |
| `glm-5v-turbo` | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND |
| `hy3` | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `kimi-k2.6` | FOUND | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `kimi-k2.7-code` | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `kimi-k3` | FOUND | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `minimax-m2.7` | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `minimax-m3` | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `qwen3.7-max` | NOT_LISTED | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `qwen3.7-plus` | FOUND | NOT_LISTED | NOT_LISTED | NOT_LISTED | FOUND | FOUND |
| `qwen3.8-max` | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS | IDENTITY_AMBIGUOUS |

## Formal Primary cohort in this refresh

The following five canonical models have compliant current Primary evidence captured in a single calibrated methodology domain, with three benchmark components each:

- `kimi-k3`
- `glm-5.2`
- `glm-5.1`
- `qwen3.7-plus`
- `kimi-k2.6`

Every Primary record contains exact canonical identity, benchmark/version, agent/harness, attempts, result scale, provenance URL, observation time, and explicit approval trail. Other models remain `UNCLASSIFIED` unless compliant Primary evidence exists; provider claims and supplemental composite indices are retained but cannot fill the Primary gap.

## Ambiguity notes

- `glm-5.3`: exact formal model identity is now confirmed (2026-08-14 announcement) and CodeBuddy visibility is confirmed. Current Primary coding-agent rows were not found in this refresh; this is a **coverage gap, not an identity ambiguity**. Do not inherit GLM-5.2 scores.
- `qwen3.8-max`: current Qoder display is `Qwen3.8-Max`, while public Qwen Code material observed during the refresh explicitly references `qwen3.8-max-preview`; this is not treated as exact equivalence.
- `cantus`: no reliable public provider identity was established. It remains identity-ambiguous for external benchmark matching.

## Fresh-release identity notes

- `deepseek-v4-flash`: exact current release is `DeepSeek-V4-Flash-0731` (2026-07-31). Artificial Analysis currently exposes an exact 0731 model-level page; no compatible Primary coding-agent row was captured, so Tier remains `UNCLASSIFIED`.
- `deepseek-v4-pro`: exact current release is `DeepSeek-V4-Pro-0813` (2026-08-13). The previously ingested Artificial Analysis Coding Agent / model-page rows correspond to the earlier preview lineage and are no longer valid current Primary evidence for 0813; they are retained only as historical/legacy context.

## Provenance approval semantics

`primary_approved_by` identifies the reviewer who manually checked the referenced result page and methodology context; `primary_approved_at` records the approval time; `approval_basis_url` points to the result page used for the decision. Schema validation checks the presence and shape of these fields, while human/agent research review remains responsible for transcription correctness.
