# TP-Voyager Operations

## Start

```bat
start_runtime.cmd
```

or:

```bat
python -m agent_runtime.server
```

Canonical environment variables:

```text
AGENT_RUNTIME_PYTHON
AGENT_RUNTIME_HOME
AGENT_RUNTIME_DB
```

CodeBuddy China environment:

```text
CODEBUDDY_INTERNET_ENVIRONMENT=internal
```

## Diagnostics

Use the local read-only CLI for database/runtime diagnostics. Historical WorkBuddy home variables/paths are migration inputs only, not current runtime configuration guidance.

## Captain operations

Normal Captain use should prefer `voyager_overview`, `crew_*`, `task_dispatch`, and `task_result`. Vendor-specific compatibility tools are not the primary control surface.
