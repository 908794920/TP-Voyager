# v1.0.7 Release Documentation and Canonical Lite Design

## Goal

Make the v1.0.7 source, generated default configuration, tests, and public
release material use the live Qoder dispatch identifier `qoder:lite`, and make
the release evidence accurately distinguish tested facts from provider-omitted
usage.

## Scope

- Canonicalize the bundled/default Qoder Lite route to `qoder:lite`.
- Update fixtures and routing-profile keys that are part of the bundled
  contract. Existing user config is not rewritten.
- Make server integration tests independent of the host user configuration.
- Document the model-parameter preflight, Qoder startup context window,
  explicit Usage status, and v1.0.7 release gate.

## Non-goals

- Do not add a migration that rewrites an existing user configuration.
- Do not estimate provider token or credit usage.
- Do not commit, tag, push, or otherwise perform a release operation.

## Acceptance

1. `VoyagerUserConfig.defaults()` emits `qoder:lite`.
2. A temporary default home can dispatch the live catalog ID `lite` in the
   Qoder server integration fixtures without reading the machine home.
3. Documentation identifies `qoder:lite` as the dispatch ID and `Lite` only as
   a display label.
4. Changelog and testing material record the actual v1.0.7 parameter/Usage
   evidence and its limits.
