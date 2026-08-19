from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTAIN = ROOT / "skills" / "tp-voyager-captain"
INTEGRATIONS = CAPTAIN / "integrations"
CODEX = INTEGRATIONS / "codex"
MARKETPLACE_ROOT = CODEX / "local-marketplace"
MARKETPLACE = MARKETPLACE_ROOT / ".agents" / "plugins" / "marketplace.json"
PLUGIN = MARKETPLACE_ROOT / "plugins" / "tp-voyager-observability"
PLUGIN_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
PLUGIN_SKILL = PLUGIN / "skills" / "tp-voyager-observability" / "SKILL.md"
RUNTIME_MANIFEST = CAPTAIN / "tp-voyager.manifest.json"


class CodexObservabilityPluginTests(unittest.TestCase):
    def test_captain_manifest_exposes_seven_tools_including_render_panel(self) -> None:
        manifest = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["skill"]["version"], "1.0.9.1")
        tools = manifest["mcp"]["required_captain_tools"]
        self.assertEqual(len(tools), 7)
        self.assertIn("render_voyager_panel", tools)

    def test_integration_layout_has_codex_host_slot_and_future_host_guidance(self) -> None:
        integration_doc = (INTEGRATIONS / "README.md").read_text(encoding="utf-8")
        codex_doc = (CODEX / "README.md").read_text(encoding="utf-8")
        self.assertIn("codex/", integration_doc)
        self.assertIn("claude-code/", integration_doc)
        self.assertIn("local-marketplace", codex_doc)
        self.assertIn("render_voyager_panel", codex_doc)

    def test_plugin_is_skills_only_and_does_not_duplicate_existing_mcp_server(self) -> None:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "tp-voyager-observability")
        self.assertEqual(manifest["version"], "1.0.9.1")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("apps", manifest)
        self.assertFalse((PLUGIN / ".mcp.json").exists())
        self.assertFalse((PLUGIN / ".app.json").exists())

    def test_plugin_skill_keeps_dispatch_single_and_observability_read_only(self) -> None:
        skill = PLUGIN_SKILL.read_text(encoding="utf-8")
        self.assertIn("task_dispatch", skill)
        self.assertIn("render_voyager_panel", skill)
        self.assertIn("appears automatically", skill.lower())
        self.assertIn("same read-only render tool", skill.lower())
        self.assertIn("never re-dispatch", skill.lower())
        self.assertIn("read-only", skill.lower())
        self.assertIn("native subagent", skill.lower())

    def test_local_marketplace_points_at_plugin_with_required_policy(self) -> None:
        marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        self.assertEqual(marketplace["name"], "tp-voyager-local")
        entries = marketplace["plugins"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["name"], "tp-voyager-observability")
        self.assertEqual(entry["source"]["source"], "local")
        self.assertEqual(entry["source"]["path"], "./plugins/tp-voyager-observability")
        self.assertEqual(entry["policy"]["installation"], "INSTALLED_BY_DEFAULT")
        self.assertEqual(entry["policy"]["authentication"], "ON_INSTALL")


if __name__ == "__main__":
    unittest.main()
