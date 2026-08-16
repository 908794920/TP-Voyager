# Crew Parameter Capability and Usage Evidence Design

## Goal

Make a Captain-selected Qoder or CodeBuddy parameter fail before task creation
when the current model cannot support it, and make task results distinguish
provider-observed usage from unavailable usage without estimating cost.

## Scope

- Validate `model_parameters.reasoning_effort` against the current model's
  provider-derived `supported_efforts` before dispatch.
- Validate Qoder `context_window_tokens` against the current model's
  provider-derived `context_config` before dispatch.
- Keep CodeBuddy context-window rejection as a route capability failure.
- Require an ACP `thought_level` setting to be declared and accepted before a
  Qoder prompt is sent when Captain requested an effort.
- Project `input_tokens`, `output_tokens`, `credits_used`, `reported_cost`,
  `currency`, and a provider-evidence status in the result only when observed.

## Non-goals

- No model selection, model fallback, retry, inferred token count, or inferred
  currency conversion.
- No use of reference multipliers as task price or credit estimates.
- No change to read-scope, patch, verification, or cancellation policy.

## Design

`CaptainDispatchService` receives the same Crew service that serves the live
catalog.  Before it calls a Crew dispatcher, it resolves the explicit
`backend:model` descriptor and rejects an unsupported requested parameter with
content-free reason codes.  Unknown provider capability remains fail-closed
when a parameter was requested; a request without parameters keeps current
routing behavior.

Qoder continues to start ACP with `--context-window <tokens>`.  Its requested
thought level must exist in ACP `configOptions`, resolve to a declared option
value, and receive a successful `session/set_config_option` response before
the prompt gate.  The successful setting is reported as applied; unavailable
or rejected settings terminate before prompt dispatch.

The existing Usage Evidence record remains the sole source of quantities.  A
small projection layer normalizes recognized scalar provider fields and labels
the absence of fields as `provider_omitted` or protocol-shape mismatch as
`protocol_unrecognized`.  It never manufactures tokens, credits, or cost.

## Acceptance

- A Qoder model advertising only 200K rejects 400K before Task creation.
- A model advertising only `medium` rejects `high` before Task creation.
- An ACP session without an eligible `thought_level` rejects before
  `session/prompt`.
- A provider Usage record yields exact normalized quantities; an empty record
  yields no quantities and `provider_omitted`.
- A real Qoder task still reports Qoder 200K + medium as applied, and result
  usage shows a provider status even if Qoder omits quantities.
