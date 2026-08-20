# Codex integration

This directory packages TP-Voyager's Codex-specific loading and presentation layer. It does not own Runtime state and never launches a second MCP server.

## Current v1.0.9.2 shape

- `../../install_codex_desktop.py` is the single Codex host installer.
- `../../sync_codex_desktop.py` remains the compatibility-stable owner of only `mcp_servers.tp_voyager` in Codex `config.toml`.
- `local-marketplace/plugins/tp-voyager/` is the current skills-only plugin.
- That plugin exports exactly one Skill: `skills/captain/SKILL.md`, surfaced by Codex as `$tp-voyager:captain`.
- The plugin contains no `.mcp.json` or `.app.json`; it reuses the already registered `tp_voyager` MCP server.
- `local-marketplace/plugins/tp-voyager-observability/` is retained only as legacy migration evidence and is no longer advertised by the current marketplace.
- `../../SKILL.md` is a legacy migration shim, not a second behavioral specification.

## First-time install or update

From the repository root:

```powershell
python -m agent_runtime.cli init
python .\skills\tp-voyager-captain\install_codex_desktop.py
```

The installer converges the current `tp-voyager` plugin, personal marketplace, existing `tp_voyager` MCP registration, and the bounded TP-Voyager block in global `AGENTS.md`. It does not install a new standalone global Captain Skill on a clean machine.

When a stable Codex CLI is available, the installer uses the official plugin add/list flow. If the installed `tp-voyager` source has changed, it removes and re-adds only the current plugin to refresh its cache. It never removes legacy entries automatically.

After host-facing files change, restart Codex Desktop when requested and open a **new conversation**. Existing conversations are not the acceptance environment for a newly loaded plugin Skill or MCP injection.

## Result-first observability

The canonical Captain Skill owns the panel rules:

- `task_id` identifies a single Task; `presentation_group_id` identifies only an explicit concurrent presentation group.
- `render_voyager_panel` is read-only and refreshes only exact task/group/task-list selectors.
- `task_result` remains the canonical terminal result source.
- Refresh never re-dispatches, resumes, cancels, changes model/Crew, widens scope, or changes permissions.
- Prompt/system/secret/raw tool output/absolute host paths/hidden reasoning are excluded from presentation.
- Machine outcome envelopes are parsed into user-readable result cards instead of being displayed verbatim.
- Canonical final answers are independent of bounded Timeline event windows.

A compatible Codex Host may aggregate concurrent dispatches into one visual tool block. The explicit presentation-group relationship lets a single TP-Voyager card render the exact group members without guessing from recent/global tasks or fuzzy correlation identifiers. Single-task `task_id` rendering remains compatible.

## Read-only status check

```powershell
python .\skills\tp-voyager-captain\install_codex_desktop.py --check
```

The check may use only `codex plugin list --json` when the CLI is available. It does not deploy files, call plugin add/remove, invoke Crew, or dispatch/resume/cancel work.

## Legacy migration cleanup

### 清理前验证

During migration, preserve both old entry points until a real **new conversation** verifies the new `tp-voyager` plugin, `$tp-voyager:captain`, the seven existing Captain tools, task dispatch, read-only panel refresh, canonical `task_result`, and explicit concurrent-group rendering. The old plugin may therefore still be visible before cleanup; “only one plugin remains” is intentionally a post-cleanup criterion.

After that validation, cleanup is explicit and user-controlled:

```text
Remove/uninstall tp-voyager-observability after validation.
Remove/delete the legacy standalone tp-voyager-captain Skill after validation.
```

If they were CLI/plugin registrations, use the matching Codex plugin removal command for the machine's marketplace. If they are only leftover directories, confirm Codex no longer references them before deleting `$CODEX_HOME\plugins\tp-voyager-observability` and `$CODEX_HOME\skills\tp-voyager-captain`. The installer never silently deletes either legacy path.

### 清理后最终验收

Restart Codex Desktop and open another **new conversation**. Confirm the plugin page now contains only `TP-Voyager`, that it exports only `$tp-voyager:captain`, and that the existing `tp_voyager` MCP, single-task result flow, and explicit concurrent-group panel all still work.

## Global routing guidance

The installer owns only the bounded marker block in `$CODEX_HOME\AGENTS.md`:

```text
<!-- >>> TP-Voyager managed guidance >>> -->
...
<!-- <<< TP-Voyager managed guidance <<< -->
```

User rules outside the markers are preserved. A non-empty `$CODEX_HOME\AGENTS.override.md` may shadow normal global guidance; the installer reports that condition but never edits the user's override.
