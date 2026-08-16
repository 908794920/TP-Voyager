# TP-Voyager User Configuration and Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build TP-Voyager v1.0.7's clean-break `~/.tp-voyager` home and strict unified user configuration, then route existing machine/operator settings through it.

**Architecture:** Add a focused `agent_runtime.configuration` module as the single reader/initializer for `config.json`. Existing path, Crew, dispatch-policy, trusted-root, and worker-resource consumers depend on that module; task-specific controls remain task-level. Runtime home uses only TP-Voyager environment names and no legacy automatic fallback.

**Tech Stack:** Python 3.10+, stdlib `dataclasses/json/pathlib/threading/shutil`, existing unittest/pytest suite.

## Global Constraints

- Default user home is `~/.tp-voyager`.
- No `.agent-runtime`, `AGENT_RUNTIME_HOME`, or `AGENT_RUNTIME_DB` compatibility/fallback.
- No secrets in `config.json`.
- Environment overrides remain only for Qoder/CodeBuddy CLI paths and CodeBuddy internet environment.
- `model_routing_profiles.json` remains separate.
- Captain explicit Crew/model selection remains a system invariant.
- Internal `agent_runtime` Python package name remains unchanged.

---

### Task 1: User config and TP-Voyager runtime paths

**Files:**
- Create: `agent_runtime/configuration/__init__.py`
- Create: `agent_runtime/configuration/user_config.py`
- Modify: `agent_runtime/persistence/runtime_paths.py`
- Test: `tests/test_v107_user_config.py`

**Interfaces:**
- Produces: `VoyagerUserConfig.load(home=None)`, `VoyagerUserConfig.initialize(home=None)`, typed section accessors, `canonical_runtime_home()`, `canonical_runtime_database_path()`, `resolve_runtime_database()`.

- [ ] Write failing tests for default `~/.tp-voyager`, `TP_VOYAGER_HOME`, `TP_VOYAGER_DB`, strict config parsing, idempotent initialization, CLI discovery, and absence of legacy fallback.
- [ ] Run the focused tests and verify failure due to missing v1.0.7 configuration behavior.
- [ ] Implement strict user configuration and clean-break runtime path resolution.
- [ ] Run the focused tests until green.

### Task 2: Route Crew, policy, roots, and resources through config

**Files:**
- Modify: `agent_runtime/backends/qoder/process.py`
- Modify: `agent_runtime/backends/codebuddy/process.py`
- Modify: `agent_runtime/backends/codebuddy/sdk_client.py`
- Modify: `agent_runtime/application/dispatch/policy.py`
- Modify: `agent_runtime/application/crew/routing_profiles.py`
- Modify: `agent_runtime/api/mcp_server.py`
- Test: `tests/test_v107_user_config.py`
- Modify tests: `tests/test_dispatch_policy.py`, `tests/test_v106_model_routing_catalog.py`

**Interfaces:**
- Consumes: `VoyagerUserConfig.load()`.
- Produces: env -> config -> PATH Crew resolution, config-owned dispatch policy, trusted roots, worker roots, and bounded runtime concurrency.

- [ ] Write/extend failing tests for each migrated configuration source and runtime concurrency admission/release.
- [ ] Verify RED against old standalone files/environment-only behavior.
- [ ] Implement minimal routing through `VoyagerUserConfig` and concurrency slot accounting.
- [ ] Run focused tests until green.

### Task 3: Initialization CLI, Codex Desktop manifest, and public v1.0.7 naming

**Files:**
- Modify: `agent_runtime/cli.py`
- Modify: `pyproject.toml`
- Modify: `skills/tp-voyager-captain/tp-voyager.manifest.json`
- Modify: `skills/tp-voyager-captain/SKILL.md`
- Modify: `skills/tp-voyager-captain/README.md`
- Modify: `skills/tp-voyager-captain/CODEX_DESKTOP.md`
- Modify tests: `tests/test_codex_desktop_install.py`, `tests/test_codex_desktop_sync.py`, CLI/architecture tests as required.

**Interfaces:**
- Produces: `tp-voyager init`, `tp-voyager doctor`, manifest with no Crew CLI env bindings, package version 1.0.7.

- [ ] Write/adjust tests first for the new command/name/manifest contract.
- [ ] Verify RED.
- [ ] Implement `init`, rename the console script, remove MCP Crew-path bindings, and update v1.0.7 metadata.
- [ ] Run focused tests until green.

### Task 4: Documentation and obsolete public configuration cleanup

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/MODEL_ROUTING.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: backend docs where old configuration names appear.

**Interfaces:**
- Produces: one documented source of user configuration: `~/.tp-voyager/config.json`.

- [ ] Update public docs only after behavior is green.
- [ ] Search for obsolete `.agent-runtime`, `AGENT_RUNTIME_HOME`, `AGENT_RUNTIME_DB`, standalone dispatch/evidence/trusted-root configuration references and remove active guidance.
- [ ] Preserve historical records only where clearly archival.

### Task 5: Verification and clean package

**Files:**
- Modify: tests affected by clean-break removal of automatic legacy path behavior.
- Delete generated caches from deliverable: `**/__pycache__`, `*.pyc`, `*.pyo`.

**Interfaces:**
- Produces: validated v1.0.7 source archive.

- [ ] Run focused v1.0.7/config/path/Codex tests.
- [ ] Run the complete test suite.
- [ ] Compile all Python sources.
- [ ] Search active source/docs for obsolete public configuration names and unexpected v1.0.6 markers.
- [ ] Remove generated bytecode/caches and package the source tree.
