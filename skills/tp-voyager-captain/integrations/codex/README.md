# Codex integration

This directory packages TP-Voyager's Codex-specific **loading and presentation** layer. It does not own Runtime state and does not launch a second MCP server.

## Components

- Existing `../../install_codex_desktop.py` and `../../sync_codex_desktop.py` remain the compatibility-stable MCP registration path.
- `local-marketplace/` is a self-contained Codex local marketplace containing the skills-only `tp-voyager-observability` plugin.
- The plugin relies on the already registered `tp_voyager` MCP server. It deliberately has no `.mcp.json` or `.app.json`, preventing duplicate server startup/registration.

## First-time local experience

1. From the repository root, install/sync the Captain Skill and its existing MCP registration:

   `python .\skills\tp-voyager-captain\install_codex_desktop.py`

2. Add this marketplace with Codex CLI:

   `codex plugin marketplace add .\skills\tp-voyager-captain\integrations\codex\local-marketplace`

3. In Codex/ChatGPT Desktop Plugins, select **TP-Voyager Local**, install **TP-Voyager Observability**, then start a new Codex task. A full Desktop restart is the safest way to pick up MCP/plugin metadata changes.

4. `task_dispatch` is associated with the same MCP Apps UI resource, so an MCP Apps-capable Codex host can show the Agent presence card immediately from the dispatch result. The card refreshes only through the separate read-only `render_voyager_panel(task_id=...)` tool; refresh never dispatches another Task. If the host does not auto-render the card, the skill can call the render tool explicitly with the returned `task_id` as a structured/UI fallback.

The panel is an in-conversation MCP Apps UI. It does not register or impersonate entries in Codex's native subagent list. If a host does not render MCP Apps UI, the render tool still returns structured Agent state, conversation, timeline, files, usage, and error information.

## Remove

Uninstall **TP-Voyager Observability** from the plugin browser and remove the marketplace with `codex plugin marketplace remove tp-voyager-local` if that is the name shown by `codex plugin marketplace list`. This does not remove the existing TP-Voyager MCP registration; use the existing Captain Skill lifecycle for that.
