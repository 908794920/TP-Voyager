---
name: tp-voyager-captain
description: Legacy migration shim only. Use the TP-Voyager plugin Captain Skill instead.
metadata:
  version: "1.0.9.2"
  protocol: "tp-voyager-captain/v1"
---

# TP-Voyager Captain legacy migration shim

This standalone Skill path is retained in the repository only to support v1.0.9.1 -> v1.0.9.2 migration and the repository installer. It is **not** the behavioral source for new Codex sessions.

Install the `TP-Voyager` plugin and use its single namespaced Captain Skill: `$tp-voyager:captain`. The canonical Captain workflow and Codex panel observability rules now live at `integrations/codex/local-marketplace/plugins/tp-voyager/skills/captain/SKILL.md`.

The v1.0.9.2 installer never silently deletes an already-installed legacy global `tp-voyager-captain` Skill. Keep the old installed copy until the new plugin/MCP has passed validation in a new Codex conversation, then remove the legacy installed directory explicitly using the documented migration cleanup steps.
