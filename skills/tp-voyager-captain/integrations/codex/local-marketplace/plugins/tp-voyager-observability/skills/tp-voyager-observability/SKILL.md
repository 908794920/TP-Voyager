---
name: tp-voyager-observability
description: Keep TP-Voyager delegated Crew visible in the current Codex conversation and open read-only execution details when useful.
---

# TP-Voyager Observability

Use the already mounted `tp_voyager` MCP server. This plugin is presentation/workflow guidance only; it never starts a second server and never bypasses TP-Voyager.

When a bounded task is delegated with `task_dispatch`:

1. Treat the returned `task_id` as the only task identity for that delegation.
2. In an MCP Apps-capable host, the Agent presence card appears automatically from the successful `task_dispatch` result because the dispatch descriptor is associated with the TP-Voyager panel resource. Do not issue a second dispatch to create or refresh the card.
3. While the panel is open, its Refresh action calls the same read-only render tool, `render_voyager_panel`, through MCP Apps `tools/call`. Never re-dispatch merely to refresh or inspect.
4. If the host does not render the automatic card, or for later explicit inspection in the same conversation, call `render_voyager_panel` with that exact `task_id`; never auto-select an unrelated Runtime task.
5. Use `task_result` for the bounded terminal result required by the Captain workflow. Observability is not a second source of Task truth.

The useful human view is: Crew, model, state, latest assistant output, execution timeline, file/artifact list, provider-reported usage, and terminal error/result context. Provider-visible reasoning summaries may be shown when explicitly supplied; never request or expose hidden/private chain-of-thought.

This is an in-conversation TP-Voyager panel. It cannot populate, replace, or impersonate Codex's native subagent list. If MCP Apps UI is unsupported by the host, continue using the structured result returned by `render_voyager_panel`; do not claim that a visual card rendered.
