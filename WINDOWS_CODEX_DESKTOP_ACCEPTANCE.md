# TP-Voyager v1.0.9.3 Windows / Codex Desktop Acceptance

Target version: `1.0.9.3`

Baseline: `e46fe0f` (`feat(v1.0.9.2): observability result projection + Chinese result-first panel + Codex plugin consolidation`), HEAD after this increment on branch `v1.0.9`.

Host: Windows, Codex Desktop (MCP Apps `text/html;profile=mcp-app` iframe).

## Scope closure

v1.0.9.3 delivers six epics: workspace strategy decoupling (EPIC-01), dispatch lifecycle (EPIC-02), panel UI state lifecycle (EPIC-03), concurrent workbench (EPIC-04), Markdown safe reader (EPIC-05), and testing/release (EPIC-06).

---

## A. Workspace strategy contract (EPIC-01 / EPIC-02)

- [ ] `task_dispatch(workspace_strategy="unknown_strategy", ...)` returns `INVALID_WORKSPACE_STRATEGY` (HTTP-shaped `ok:false`), not an exception.
- [ ] `workspace_strategy="model_only"` dispatches with no workspace/snapshot/context preparation; the runtime session routing metadata contains `workspace_strategy="model_only"` and no `repository_snapshot_ref`/`repository_research`.
- [ ] `workspace_strategy="live_readonly"` accepts a non-Git directory and strips `patch_policy`.
- [ ] Default (omitted) strategy is `isolated_patch` and keeps the Runtime-owned Git worktree isolation unchanged.
- [ ] Reusing one `idempotency_key` with a different strategy returns `IDEMPOTENCY_CONFLICT`; the same strategy + same key replays the same task.
- [ ] No change to the durable `task_result` source of truth; Task/Session/Attempt models untouched.

## B. Panel UI state lifecycle (EPIC-03)

- [ ] Open Task A, expand a detail, switch the active tab / scroll → press Refresh (or let auto-refresh fire) → state (tab, expanded details, scroll) is preserved.
- [ ] Task A state never leaks into Task B (per-task composite key).
- [ ] Group A selection/tab never leaks into Group B (per-group composite key).
- [ ] Switching iframe visibility (hide → show), page show, and window focus triggers a read-only sync that preserves the restored state.
- [ ] State lives only in iframe memory: no `localStorage`, no database, no server persistence; a Runtime process restart clears it.

## C. Concurrent workbench (EPIC-04)

- [ ] `render_voyager_panel(presentation_group_id=...)` renders a left task-navigation list; each entry shows 状态 / Crew / Model / task_id / 耗时, and failure reason in red when present.
- [ ] Clicking a task switches the right detail workspace to that task.
- [ ] Right detail has five tabs: 摘要 / 完整回答 / 执行活动 / 文件变更 / 用量.
- [ ] Default tab follows status: running/observing/connecting/queued → 执行活动; completed/failed → 摘要.
- [ ] Single-task view still renders result-first with independent foldouts (摘要/完整回答/执行活动/文件变更/用量) and the existing `appendSection` contract.
- [ ] At width < 520px the navigation collapses into a horizontal strip and content stays readable.

## D. Markdown safe reader (EPIC-05)

- [ ] Model output with headings/bold/italic/lists/fenced code/blockquote/tables/links renders as styled safe HTML.
- [ ] Link destinations other than `http:`/`https:`/`mailto:` render as plain label text (active URLs dropped).
- [ ] Input `<script>alert(1)</script>` is displayed as escaped text and never executes (escape-first + DOMParser nodes; no `innerHTML` assignment in the panel).
- [ ] No `<iframe>`/`onclick`/`javascript:` sink exists anywhere in the panel source.
- [ ] The renderer keeps zero external network dependencies.

## E. Packaging & installation

- [ ] `skills/tp-voyager-captain/install_codex_desktop.py` still converges the single skills-only `tp-voyager` plugin, the seven-tool `tp_voyager` MCP registration, marketplace entry and global `AGENTS.md` managed block; plugin/manifest/SKILL versions report `1.0.9.3`.
- [ ] `new_conversation_required=true` is reported when host-facing state changed; a new Codex conversation is created before acceptance.
- [ ] `python -m agent_runtime.cli init` succeeds with `TP_VOYAGER_HOME` isolation.

## F. Regression gate

- [ ] Full suite: `python -m pytest tests -q --tb=no` → 608 tests, 0 failures, 0 errors, 1 skipped.
- [ ] Existing 501-test baseline keeps passing (no v1.0.9.2 capability regression).
- [ ] `git diff --check` is clean; no `.rej`/`.orig` leftovers.
- [ ] Version declarations are consistent at `1.0.9.3` (`pyproject.toml`, CLI doctor, panel `appInfo`, captain manifest/SKILL/plugin, README, AGENTS.md, CHANGELOG).

## Decision record (user-confirmed)

1. `workspace_strategy` default stays `isolated_patch` (compatible with the CHANGELOG v1.0.9.3 contract landed in `c2ebf9a`).
2. No `WorkspaceStrategyRouter` refactor; lightweight branches + enum + tests are used.
3. EPIC-04 concurrent workbench left/right layout is implemented.
