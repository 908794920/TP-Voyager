# TP-Voyager v1.0.9.3 Test Report

- **Target**: TP-Voyager v1.0.9.2 → v1.0.9.3
- **Repository**: `e:\updateProject\dev\TP_Voyager-Dev` (branch `v1.0.9`, HEAD `c2ebf9a` + this increment)
- **Date**: 2026-08-20
- **Runner**: Windows (win32) / miniconda Python 3.13.5
- **Command**: `python -m pytest tests -q --tb=no`

## 1. Overall Result

| Metric | Value |
|---|---|
| Total test cases | 608 |
| Passed | 607 |
| Skipped | 1 |
| Failed / Errors | 0 |
| Existing suite regression | PASS (baseline 501 passed / 1 skipped / 76 subtests → 608 passed-equivalent) |

> Note: the local runner emits a `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]` teardown notice
> for temporary pytest dirs; it is an environment cleanup prompt and does not affect test results.
> The `--junitxml` result is authoritative: `errors=0 failures=0 skipped=1 tests=608`.

## 2. New Test Files (v1.0.9.3)

| File | Coverage |
|---|---|
| `tests/test_workspace_strategy.py` | `WorkspaceStrategy` enum values/round-trip; default `isolated_patch`; normalization; invalid value `ValueError`; MCP boundary `INVALID_WORKSPACE_STRATEGY`; `model_only` clears cwd/snapshot/research and persists no snapshot routing; `live_readonly` clears `patch_policy`; strategy flow through API → `CaptainDispatchRequest` → routing_metadata; idempotency contract contains the strategy; cross-strategy idempotency conflict; same-strategy replay. |
| `tests/test_panel_state.py` | PanelUIStateStore is iframe-memory only (no `localStorage`/`indexedDB`/`fetch`); state captures activeTab/selectedTaskId/section/scrollTop/expandedDetails/timestamp; per-group/per-task composite key; beforeRefresh/restoreState lifecycle; `pagehide`/`visibilitychange` save + `pageshow`/`focus` resume; five workbench tabs; status-driven default tab (running → 执行活动, completed/failed → 摘要); task navigation fields; responsive collapse. |
| `tests/test_markdown_security.py` | Renderer supports headings/bold/italic/list/code/blockquote/table/link; escape-first pipeline; no `innerHTML` assignment (DOMParser nodes, scripts cannot execute); no `<iframe>`/`onclick`/`javascript:`/`{=html}`/`localStorage`; link protocol allow-list (`http:`/`https:`/`mailto:`); no external network dependency. |

## 3. Modified Files

- `agent_runtime/domain/enums.py` — add `WorkspaceStrategy(str, Enum)`.
- `agent_runtime/domain/dispatch.py` — `WORKSPACE_STRATEGIES` derived from the enum (single source of truth, string-compatible).
- `agent_runtime/api/mcp_server.py` — MCP boundary validation reads `WORKSPACE_STRATEGIES`; `reject` defined before first use (fixes `UnboundLocalError` on invalid strategy); idempotency contract now includes `workspace_strategy`.
- `agent_runtime/api/voyager_panel.py` — workbench (left nav + 5-tab detail), state lifecycle (`pagehide`/`visibilitychange` save, render restore), expanded Markdown renderer, version 1.0.9.3.
- Version declarations: `pyproject.toml`, `agent_runtime/cli.py`, `agent_runtime/backends/codebuddy/model_catalog.py`, captain manifest/SKILL/plugin/README, root `README.md`, `AGENTS.md`, `CHANGELOG.md`.
- Tests: `tests/test_voyager_panel.py` (workbench + version), `tests/test_v107_user_config.py`, `tests/test_runtime_diagnostics.py`, `tests/test_codex_plugin_observability.py` (version 1.0.9.3).

## 4. Release Gate Checklist

| # | Gate | Status |
|---|---|---|
| 1 | `model_only` does not create a workspace snapshot | PASS — `cwd`/`repository_snapshot_ref`/`repository_research` cleared; session routing carries no snapshot/research keys; `snapshot_count == 0` for the stub path |
| 2 | `live_readonly` supports non-Git directories | PASS — strategy only strips `patch_policy`; read-only routes do not require a Git worktree |
| 3 | `frozen_context` is reproducible | PASS — strategy persists in routing metadata and idempotency contract; same strategy + same key replays |
| 4 | `isolated_patch` isolates changes | PASS — default strategy keeps the existing Runtime-owned `git worktree` isolation path unchanged |
| 5 | UI refresh preserves state | PASS — `beforeRefresh()` saves tab/selection/section/scroll/expanded details; restored after render |
| 6 | Task A/B state is isolated | PASS — per-task composite `panelStateKey`; independent `PanelUIStateStore` entries |
| 7 | Markdown has no XSS | PASS — escape-first renderer, whitelisted tags, protocol-allow-listed links, DOMParser node build, no `innerHTML` assignment |
| 8 | `pytest` full suite passes | PASS — 608 tests, 0 failures, 0 errors, 1 skipped |

## 5. Design Decisions (user-confirmed)

1. `workspace_strategy` default stays `isolated_patch` (compatible with the CHANGELOG v1.0.9.3 contract already landed in `c2ebf9a`).
2. No `WorkspaceStrategyRouter` hierarchy: the lightweight conditional branches already in `task_dispatch` are kept; the enum + boundary validation + tests cover the four strategies.
3. EPIC-04 concurrent workbench left/right layout is implemented.

## 6. Deliverables

- `tp-voyager-v1.0.9.3-final-incremental.zip` — changed/new files of this increment.
- `v1.0.9.2-to-v1.0.9.3.patch` — `git diff e46fe0f..HEAD` incremental patch.
- `WINDOWS_CODEX_DESKTOP_ACCEPTANCE.md` — Codex Desktop iframe acceptance checklist.
