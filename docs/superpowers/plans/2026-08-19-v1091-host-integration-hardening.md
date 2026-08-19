# TP-Voyager v1.0.9.1 Host Integration Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the first-use gaps found during Windows Codex Desktop acceptance: nested repository snapshot failure, split MCP/plugin installation, missing global Codex routing guidance, and low-information Agent failure presentation.

**Architecture:** Keep the Runtime/MCP/observability ownership from v1.0.9. Harden the existing read-only snapshot copier, extend the existing Captain Skill installer into the single Codex host installer, merge a bounded TP-Voyager block into the global Codex `AGENTS.md`, and polish the existing MCP Apps panel. Do not create new Runtime state, a second MCP server, or a new repository/`agent_runtime` top-level directory.

**Tech Stack:** Python 3.10+, pathlib/os/shutil, existing FastMCP UI resource, pytest/unittest.

**Spec:** User-approved v1.0.9.1 plan in the 2026-08-19 TP-Voyager conversation; implementation is driven by Windows live evidence from the v1.0.9 first-use test.

## Global Constraints

- Keep the original seven Captain MCP tools and existing request/response contracts compatible.
- The observability plugin must remain skills-only and must not register/start another TP-Voyager MCP server.
- Never overwrite unrelated user `config.toml`, `AGENTS.md`, marketplace, plugin, or skill content.
- Global Codex guidance is managed by explicit TP-Voyager begin/end markers and is idempotent.
- Do not auto-dispatch, auto-retry, auto-switch model, or expand permissions from installed guidance.
- Snapshot pruning must be component-aware at any nesting depth; no substring matching.
- Smoke + directly affected tests are required; full regression before final artifact generation.

---

### Task 1: Nested Sensitive Directory Snapshot Pruning

**Files:**
- Modify: `agent_runtime/backends/workspace_snapshot.py`
- Test: `tests/test_v108_workspace_read_only.py`

**Interfaces:**
- Add internal `_contains_forbidden_component(path: object) -> bool` for snapshot traversal only.
- Keep `sensitive_path_matches()` semantics unchanged for existing policy callers.

- [x] Add a failing regression test with an aggregate workspace containing `dev/repo/.git/refs/codex/turn-diffs/...`, nested `.codebuddy`, and nested `.qoder` trees.
- [x] Run the targeted test and verify RED because the current prefix-only filter descends into nested sensitive directories.
- [x] Prune any directory whose relative path contains a component equal to one of `_MANDATORY_FORBIDDEN`.
- [x] Add a bounded snapshot-copy error wrapper that reports the relative path/stage without leaking full raw exception text to the UI contract.
- [x] Run workspace read-only tests and verify GREEN.

### Task 2: Unified Codex Host Installer and Managed AGENTS Guidance

**Files:**
- Modify: `skills/tp-voyager-captain/install_codex_desktop.py`
- Modify: `skills/tp-voyager-captain/CODEX_DESKTOP.md`
- Modify: `skills/tp-voyager-captain/integrations/codex/README.md`
- Test: `tests/test_codex_desktop_install.py`
- Test: `tests/test_codex_plugin_observability.py`

**Interfaces:**
- `install(..., user_home: str | Path | None = None, codex_cli: str | Path | None = None)` installs/checks Captain Skill + MCP + observability plugin + managed global AGENTS guidance.
- Managed markers: `<!-- >>> TP-Voyager managed guidance >>> -->` / `<!-- <<< TP-Voyager managed guidance <<< -->`.
- Plugin target: `<CODEX_HOME>/plugins/tp-voyager-observability` using the packaged local plugin source; no `.mcp.json` is added.
- Result distinguishes `plugin_files_installed` from Codex-host-confirmed `plugin_installed` / `plugin_enabled`; a personal marketplace `INSTALLED_BY_DEFAULT` fallback avoids a second manual MCP/plugin setup path when CLI verification is unavailable.
- Result also adds `mcp_registered`, `plugin_installation_pending`, `agents_guidance_installed`, `restart_required`, and `new_conversation_required`.

- [x] Add failing tests for create/merge/update/idempotency of `AGENTS.md`, plugin copy/check, user content preservation, and consolidated install status.
- [x] Run targeted tests and verify RED.
- [x] Implement managed-block merge, plugin deployment, and check-only drift reporting inside the existing installer.
- [x] Ensure installation guidance explicitly says restart Codex and start a new conversation/session after MCP/plugin/guidance changes.
- [x] Run installer/plugin tests and verify GREEN.

### Task 3: Agent Panel Information Hierarchy and Failure UX

**Files:**
- Modify: `agent_runtime/api/voyager_panel.py`
- Modify: `agent_runtime/application/voyage/observability.py`
- Modify: `agent_runtime/api/mcp_server.py`
- Test: `tests/test_voyager_panel.py`
- Test: `tests/test_agent_observability.py`

**Interfaces:**
- Panel keeps the same `ui://tp-voyager/agent-panel/v1.html` and `render_voyager_panel` tool.
- Failure projection adds safe `stage` when available; raw backend exception text remains excluded.
- Empty conversation renders a meaningful current-stage fallback rather than `No data.`.
- Timeline shows the latest bounded activity by default; conversation remains expandable/readable.

- [x] Add failing tests for failure stage, human-readable header/meta layout strings, latest timeline default visibility, and non-empty conversation fallback text.
- [x] Run targeted tests and verify RED.
- [x] Add safe phase/stage classification to the failure observation path and projection.
- [x] Rework panel presentation without adding external assets/network dependencies.
- [x] Run panel/observability tests and verify GREEN.

### Task 4: Version, Docs, Verification, and Delivery

**Files:**
- Modify: `pyproject.toml`
- Modify: `skills/tp-voyager-captain/tp-voyager.manifest.json`
- Modify: plugin `.codex-plugin/plugin.json`
- Modify: `AGENTS.md`, `CHANGELOG.md`, `README.md` only where current behavior/version needs updating.
- Create: `docs/records/V1.0.9.1_HOST_INTEGRATION_ACCEPTANCE.md`

- [x] Bump current development version to `1.0.9.1` consistently.
- [x] Run affected targeted suites.
- [x] Run the full pytest suite and static compilation checks.
- [x] Run `git diff --check` and review the complete diff for accidental machine paths/secrets/duplicate MCP configuration.
- [x] Record sandbox verification and Windows live gates honestly.
- [x] Generate a patch relative to uploaded v1.0.9 commit `23d6136` and an increment ZIP containing only added/modified files plus checksums.
