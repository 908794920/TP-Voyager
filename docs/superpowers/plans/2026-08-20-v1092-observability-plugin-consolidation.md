# TP-Voyager v1.0.9.2 Observability & Plugin Consolidation Implementation Plan

> **Execution:** Inline TDD against baseline `4e90f2b8de8e98145adb0035716c6f4eeb6edda7`. No approved v1.0.9.2 requirement may be deferred.

**Goal:** Deliver truthful panel synchronization, canonical terminal result projection, a Chinese result-first panel, explicit concurrent presentation groups, and one Codex `tp-voyager` plugin with one Captain Skill in one increment.

**Architecture:** SQLite Task/Session/Result remains the durable truth. Observability is a bounded read-only projection. `presentation_group_id` is persisted only in existing routing metadata on independently-created Tasks; no Task table/state-machine or second MCP server is added. Codex packaging converges to the skills-only `tp-voyager` plugin whose single `captain` Skill is surfaced as `$tp-voyager:captain`.

**Tech Stack:** Python, SQLite durable Task core, MCP Apps HTML/JavaScript resource, Codex local marketplace/plugin packaging, pytest/unittest.

## Global constraints

- All five requested changes ship together in v1.0.9.2.
- Default Captain MCP surface remains exactly seven tools.
- `render_voyager_panel` stays read-only; refresh cannot dispatch/resume/cancel/mutate.
- Captain continues to choose Crew/model/effort and accept/reject results.
- No prompt/system/secret/raw tool output/absolute host path/hidden reasoning is exposed.
- Group selection is explicit only: exact `task_id`, exact `presentation_group_id`, or explicit bounded `task_ids`.
- No fuzzy correlation/recent/global task selection.
- Old installed global Skill and old observability plugin are preserved until new-plugin live validation; cleanup is explicit and user-controlled.

## Task 1 — Canonical result projection and independent streams

- Extend `AgentObservationStore` with an independent bounded conversation stream.
- Always record the canonical final assistant answer at completion.
- Terminal `VoyageAgentProjection.detail()` prefers durable structured result / canonical final answer over the event window.
- Strip `TP_VOYAGER_CREW_OUTCOME_JSON` from visible prose and project its structured outcome into conclusion/evidence/risk/next-step fields.
- Preserve Markdown whitespace and newlines.
- Bound/aggregate Timeline independently so high-frequency activity cannot evict the final answer.

## Task 2 — Immediate sync and result-first Chinese panel

- Treat `syncing` as UI-only state.
- On dispatch card creation, visibility resume, page show, focus, or manual refresh: render “正在同步” first, then immediately call only `render_voyager_panel`.
- First screen: status, Crew/model/task/duration plus conclusion, key evidence, risks, next steps.
- Foldouts: 完整回答 / 执行活动 / 文件变更 / 用量; hide empty sections and collapse process by default.
- Use `textContent` and pre-wrap formatting; never inject machine envelopes or raw tool output.

## Task 3 — Explicit concurrent presentation groups

- Add `CaptainDispatchRequest.presentation_group_id` and persist it in existing bounded routing metadata.
- Extend `task_dispatch(..., presentation_group_id="")` and `render_voyager_panel(task_id="", presentation_group_id="", task_ids=None, limit=200)`.
- Keep every Task independent; group projection is a read-only exact-membership view.
- Return each child task's state, Crew, model, task_id, duration, safe current/terminal summary, and its own result/activity/files/usage details.
- Preserve single-task behavior.

## Task 4 — One Codex plugin / one Captain Skill

- Create current plugin `integrations/codex/local-marketplace/plugins/tp-voyager/`.
- Plugin exports exactly one `skills/captain/SKILL.md`; plugin manifest name is `tp-voyager`, producing `$tp-voyager:captain` in Codex.
- Bundle no `.mcp.json` / `.app.json`; continue using existing `tp_voyager` MCP.
- Move canonical Captain behavior and panel observability rules into the plugin Skill.
- Replace repository root Skill with a migration shim so two independent Skills do not define the same behavior.
- Installer deploys the new plugin and existing MCP/AGENTS config but never silently deletes old installed `tp-voyager-captain` / `tp-voyager-observability` material.
- If current plugin source changes and Codex CLI confirms it is installed, refresh cache with remove + add, then require a new conversation.

## Task 5 — Version, regression, records, delivery

- Bump all active development declarations to 1.0.9.2 while retaining v1.0.9.1 only in historical/migration material.
- Run affected tests, complete repository regression, `git diff --check`, and compile validation.
- Record the Windows/Codex Desktop live procedure as an external manual acceptance gate; implementation itself is not deferred.
- Produce one incremental ZIP containing only added/modified files plus a manifest/checksums and one unified Git patch relative to `4e90f2b8de8e98145adb0035716c6f4eeb6edda7`.
