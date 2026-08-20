# TP-Voyager Codex plugin

Skills-only Codex plugin for TP-Voyager v1.0.9.3.

It exports exactly one Skill, `captain`, which Codex namespaces as `$tp-voyager:captain`. The plugin deliberately contains no `.mcp.json` or `.app.json`: the existing `tp_voyager` MCP registration remains the sole Runtime/control boundary.

After install/update, start a new Codex conversation/session before validating the bundled Skill and MCP injection.
