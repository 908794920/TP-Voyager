# TP-Voyager v1.0.9 Agent Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a first usable Codex-facing TP-Voyager agent presence/trace experience that shows live Crew/model/state, assistant output, tool/file activity when available, usage, and terminal results without changing the durable Task truth model.

**Architecture:** Keep SQLite Task/Event/Evidence as authoritative control truth and add a bounded, non-authoritative **in-memory** observation projection under the existing Voyage application boundary. Backend callbacks forward typed activity details to the projection; a single read-only `render_voyager_panel` Captain MCP tool exposes a structured fallback payload and an MCP Apps `ui://` resource renders the explicitly pinned current task in Codex. Existing six tools retain their request/response behavior; the new tool is visibility-only. `task_dispatch` and the render tool both reference the same UI resource so presence can appear immediately, while the UI refresh path is restricted to the read-only render tool. The existing Codex Desktop skill installer remains the MCP registration mechanism, while a packaged Codex plugin supplies host-specific observability guidance without registering a duplicate MCP server.

**Tech Stack:** Python 3.10+, FastMCP (`mcp>=1.28,<2`), SQLite existing durable runtime truth, bounded process-local observation memory, self-contained HTML/CSS/JS MCP App resource, pytest/unittest existing test suite.

**Spec:** TP-Voyager v1.0.9 Agent Observability requirements approved in the project conversation; acceptance evidence is recorded in `docs/records/V1.0.9_AGENT_OBSERVABILITY_ACCEPTANCE.md`.

## Global Constraints

- Do not create a second Task/Session/Result/Evidence state machine.
- Do not persist prompts, system messages, secrets, private chain-of-thought, or raw tool output in durable TaskEvent payloads.
- Observation data is non-authoritative UI/debug telemetry; durable Task/Event/Evidence remains source of truth.
- Keep existing six Captain tool call contracts compatible; add only one read-only Captain visibility tool.
- Do not modify Codex native subagent routing, system proxy/network settings, or passenger repository business code.
- Do not bundle a second TP-Voyager MCP server in the Codex plugin when the existing Codex configuration already owns MCP registration.
- MCP App resource has no external network dependencies and uses a minimal CSP.
- UI failure or unsupported host must not prevent the MCP tool from returning complete structured fallback data.

---

### Task 1: Bounded Agent Observation Projection

**Files:**
- Create: `agent_runtime/application/voyage/observability.py`
- Modify: `agent_runtime/application/voyage/__init__.py`
- Test: `tests/test_agent_observability.py`

**Interfaces:**
- Produces: `AgentObservationStore(root: Path | None = None, ...)`, `append(task_id: str, event: dict[str, Any]) -> dict[str, Any]`, and `read(task_id: str, limit: int = 200) -> list[dict[str, Any]]`; the optional root is diagnostic-only and is never created or written.
- Produces: `VoyageAgentProjection(task_service, observation_store)` with `presence(task_id="", limit=5)`, `trace(task_id, limit=200)`, `detail(task_id, limit=200)`.

- [ ] Write tests for bounded/sanitized append/read, assistant-message aggregation, task/model/usage/file projection, and failure states.
- [ ] Run targeted tests and confirm RED because the module/API does not exist.
- [ ] Implement the minimal observation store/projection under `application/voyage`.
- [ ] Re-run targeted tests until GREEN.

### Task 2: Preserve Supported Backend Stream Facts

**Files:**
- Modify: `agent_runtime/runtime/backend_callbacks.py`
- Modify: `agent_runtime/backends/qoder/acp_client.py`
- Modify: `agent_runtime/backends/codebuddy/sdk_client.py`
- Modify: `agent_runtime/api/mcp_server.py`
- Test: `tests/test_agent_observability_callbacks.py`

**Interfaces:**
- `RuntimeBackendCallbacks(..., on_activity: Callable[[BackendActivity], None])` forwards the typed object instead of dropping `detail`.
- Qoder/CodeBuddy activity detail may include only bounded observation fields (`observation_kind`, assistant text, safe tool name/path/status metadata).
- MCP runtime callback appends presence/activity/usage/terminal observations while keeping existing durable activity events content-free.

- [ ] Write tests proving callback detail survives and Qoder/CodeBuddy assistant output is exposed through typed observation metadata.
- [ ] Run targeted tests and confirm RED on the old detail-dropping behavior.
- [ ] Implement minimal callback/backend changes and runtime observation hooks.
- [ ] Re-run targeted tests until GREEN.

### Task 3: Read-only MCP Apps Panel

**Files:**
- Create: `agent_runtime/api/voyager_panel.py`
- Modify: `agent_runtime/api/mcp_server.py`
- Modify: `agent_runtime/api/schemas.py`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Test: `tests/test_voyager_panel.py`
- Test: `tests/test_mcp_surface.py` (existing affected contract)

**Interfaces:**
- `VOYAGER_PANEL_URI = "ui://tp-voyager/agent-panel/v1.html"`.
- `render_voyager_panel(task_id: str = "", limit: int = 200) -> dict[str, Any]` is read-only and returns `tp-voyager.agent_panel/v1` structured data.
- MCP tool metadata associates the UI resource by `_meta.ui.resourceUri`/compatible `ui/resourceUri` metadata.
- MCP resource returns `text/html;profile=mcp-app`, prefers host border, and has empty external CSP allow-lists.

- [ ] Write tests for panel HTML/status visuals/tools-call refresh/fallback schema and Captain allow-list.
- [ ] Run tests and confirm RED because panel/resource/tool are absent.
- [ ] Implement self-contained panel and MCP resource/tool registration.
- [ ] Re-run targeted tests until GREEN.

### Task 4: Codex On-demand Integration Package

**Files:**
- Create: `skills/tp-voyager-captain/codex-plugin/.codex-plugin/plugin.json`
- Create: `skills/tp-voyager-captain/codex-plugin/skills/tp-voyager-observability/SKILL.md`
- Create: `skills/tp-voyager-captain/codex-plugin/README.md`
- Modify: `skills/tp-voyager-captain/SKILL.md`
- Modify: `skills/tp-voyager-captain/README.md`
- Modify: `skills/tp-voyager-captain/CODEX_DESKTOP.md`
- Modify: `skills/tp-voyager-captain/tp-voyager.manifest.json`
- Test: `tests/test_codex_plugin_observability.py`

**Interfaces:**
- Plugin is skills-only and never registers/starts a duplicate MCP server.
- Existing `sync_codex_desktop.py` remains canonical MCP registration/sync path.
- Skill tells Codex to call `render_voyager_panel(task_id=...)` immediately after a successful delegated task when visual observability is relevant, and on user request thereafter.

- [ ] Write plugin structure/no-duplicate-MCP tests and manifest-version/tool-list tests.
- [ ] Run targeted tests and confirm RED.
- [ ] Add plugin/skill/docs and update manifest.
- [ ] Re-run targeted tests until GREEN.

### Task 5: Architecture Contract, Regression, and Release Artifacts

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/architecture/CHARTER.md`
- Modify: `docs/architecture/DIRECTORY_BASELINE.md` only where public surface/observability ownership changed.
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/records/V1.0.9_AGENT_OBSERVABILITY_ACCEPTANCE.md`

**Interfaces:**
- Governance explicitly distinguishes read-only agent observability from Captain planning/control and from raw/private reasoning.

- [ ] Update architecture docs to authorize the seventh read-only Captain tool and Voyage-owned non-authoritative observation projection.
- [ ] Run targeted observation tests.
- [ ] Run existing smoke/directly affected tests available in the sandbox.
- [ ] Run static compilation/import-independent checks where external MCP/SDK packages are unavailable.
- [ ] Record sandbox limitations honestly; do not claim Codex Desktop rendering without Windows host evidence.
- [ ] Generate patch from v1.0.8 tag and an increment ZIP containing only added/modified files plus deletion manifest if needed.
