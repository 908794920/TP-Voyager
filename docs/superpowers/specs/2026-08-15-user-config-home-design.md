# TP-Voyager User Configuration and Home Design

## Goal

Make TP-Voyager v1.0.7 use a product-specific user home at `~/.tp-voyager` and consolidate machine-specific, cross-project configuration into one strict `config.json` created by `tp-voyager init`.

## Clean-break constraints

- No compatibility or automatic migration from `~/.agent-runtime`.
- No fallback from `AGENT_RUNTIME_HOME` or `AGENT_RUNTIME_DB`.
- New launch overrides are `TP_VOYAGER_HOME` and `TP_VOYAGER_DB` only.
- Internal Python package name `agent_runtime` remains unchanged.
- Secrets/tokens are never stored in `config.json`.
- Captain must still choose Crew and model explicitly; configuration only narrows authorization.

## User home

Default layout:

```text
~/.tp-voyager/
├─ config.json
├─ model_routing_profiles.json
└─ runtime/
   ├─ tp_voyager.db
   ├─ artifacts/
   ├─ workspaces/
   └─ logs/
```

`TP_VOYAGER_HOME` overrides the home directory. `TP_VOYAGER_DB` overrides the SQLite path.

## Configuration schema

`config.json` uses `schema = tp-voyager.config/v1` and contains five sections:

```json
{
  "schema": "tp-voyager.config/v1",
  "crew": {
    "qoder": {"enabled": true, "cli_path": ""},
    "codebuddy": {"enabled": true, "cli_path": "", "internet_environment": "internal"}
  },
  "dispatch": {
    "allowed_models": [
      "qoder:lite",
      "qoder:qmodel_38max",
      "codebuddy:hy3",
      "codebuddy:deepseek-v4-flash"
    ],
    "preferred_models": [],
    "task_kind_allowed_models": {}
  },
  "trusted_roots": {
    "model_evidence": {},
    "instructions": {}
  },
  "resources": {
    "worker_profiles_root": "",
    "worker_skills_root": ""
  },
  "runtime": {
    "max_concurrent_tasks": 4
  }
}
```

Unknown fields and invalid values fail closed. Root aliases map to absolute local paths. Empty resource paths mean bundled/not-configured behavior as appropriate.

## Resolution rules

Crew CLI resolution keeps explicit environment overrides for temporary/CI use:

1. `QODER_CLI_PATH` / `CODEBUDDY_CODE_PATH`
2. `config.json` crew path
3. executable discovery on `PATH`
4. unavailable

`CODEBUDDY_INTERNET_ENVIRONMENT` similarly overrides the config value; otherwise config defaults to `internal`.

Crew `enabled=false` makes that Crew unavailable without probing/dispatching its CLI.

## Consolidated files

The following standalone user/operator files are removed as active configuration sources:

- `dispatch_model_policy.json` -> `config.json.dispatch`
- `model_evidence_roots.json` -> `config.json.trusted_roots.model_evidence`
- `trusted_instruction_roots.json` -> `config.json.trusted_roots.instructions`

`model_routing_profiles.json` remains separate because it is model knowledge/evidence, not general machine configuration.

Worker profile/skill root environment variables are replaced by `config.json.resources` for normal operation.

## Initialization

`tp-voyager init`:

- creates `~/.tp-voyager` and `runtime/` if absent;
- writes `config.json` only if it does not exist;
- discovers Qoder/CodeBuddy executables on `PATH` and records the resolved absolute path when found;
- initializes the reviewed `model_routing_profiles.json` baseline without overwrite;
- is idempotent and never overwrites an existing `config.json`.

## Runtime concurrency

`runtime.max_concurrent_tasks` is a process-wide dispatch admission limit. Idempotent replay does not consume capacity. A new task is rejected with a bounded `RUNTIME_BUSY` result when all slots are occupied; a slot is always released on worker completion or thread-start failure.

## Codex Desktop integration

The Captain MCP manifest no longer injects Qoder/CodeBuddy CLI paths into MCP environment variables. The installed MCP only needs the repository root binding; TP-Voyager resolves machine configuration from its user home.

## Version/public naming

v1.0.7 public metadata and commands use TP-Voyager naming:

- package version `1.0.7`
- console script `tp-voyager`
- doctor version `1.0.7`
- user-facing docs use `TP_VOYAGER_HOME` / `TP_VOYAGER_DB`

The old `agent-runtime` console script is not retained in this clean break.
