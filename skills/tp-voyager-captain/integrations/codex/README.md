# Codex integration

This directory packages TP-Voyager's Codex-specific **loading and presentation** layer. It does not own Runtime state and never launches a second MCP server.

## Components

- `../../install_codex_desktop.py` is the single Codex host installer. It converges the Captain Skill, existing `tp_voyager` MCP registration, observability plugin, personal marketplace entry, and the TP-Voyager managed block in global `AGENTS.md`.
- `../../sync_codex_desktop.py` remains the compatibility-stable owner of only `mcp_servers.tp_voyager` inside Codex `config.toml`.
- `local-marketplace/` packages the skills-only `tp-voyager-observability` plugin source. The installed plugin relies on the already registered `tp_voyager` MCP server and deliberately has no `.mcp.json` or `.app.json`.

## First-time local experience

From the repository root, initialize TP-Voyager and run the one host installer:

```powershell
python -m agent_runtime.cli init
python .\skills\tp-voyager-captain\install_codex_desktop.py
```

The installer returns explicit states including `mcp_registered`, `plugin_files_installed`, `plugin_installed`, `plugin_enabled`, `plugin_installation_pending`, `marketplace_registered`, `agents_guidance_installed`, `agents_guidance_effective`, `restart_required`, and `new_conversation_required`.

The personal marketplace entry uses `INSTALLED_BY_DEFAULT`. When a stable Codex CLI is available, the installer also runs the official `codex plugin add ... --json` path and verifies with `codex plugin list --json`. If the CLI is unavailable, it does not claim a verified install: `plugin_installation_pending=true` remains until the restarted Desktop host consumes the default-install marketplace entry. The plugin still never owns an MCP server.

When host-facing files changed, fully restart Codex Desktop and create a **new** task/conversation. `task_dispatch` is associated with the MCP Apps resource, so a compatible Codex host can show the Agent presence card as soon as the dispatch result contains the task ID. The card refreshes only through `render_voyager_panel(task_id=...)`; refresh never dispatches another Task.

If the host does not render MCP Apps UI, the render tool still returns structured Agent state, conversation, timeline, files, usage, and safe failure information. The panel is an in-conversation TP-Voyager UI; it does not register or impersonate entries in Codex's native subagent list.

## Global routing guidance

The installer creates or updates only this bounded marker block inside global `$CODEX_HOME\AGENTS.md`:

```text
<!-- >>> TP-Voyager managed guidance >>> -->
...
<!-- <<< TP-Voyager managed guidance <<< -->
```

User rules outside the markers are preserved. The guidance tells Codex to evaluate the mounted Captain MCP for relevant bounded work, not to dispatch automatically. It explicitly forbids automatic retries, silent Crew/model changes, scope widening, permission expansion, or approval bypass.

A non-empty `$CODEX_HOME\AGENTS.override.md` may shadow normal global `AGENTS.md`; the installer reports that condition but never edits the user's override.

## Read-only status check

```powershell
python .\skills\tp-voyager-captain\install_codex_desktop.py --check
```

The check is read-only across Skill, MCP config, plugin, personal marketplace, and managed global guidance. If Codex CLI is available it may call only `codex plugin list --json` to report host installation/enabled state; it never calls `plugin add`, never calls a Crew, and never dispatches a task.
