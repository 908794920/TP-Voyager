from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTAIN = ROOT / "skills" / "tp-voyager-captain"
INTEGRATIONS = CAPTAIN / "integrations"
CODEX = INTEGRATIONS / "codex"
MARKETPLACE_ROOT = CODEX / "local-marketplace"
MARKETPLACE = MARKETPLACE_ROOT / ".agents" / "plugins" / "marketplace.json"
PLUGIN = MARKETPLACE_ROOT / "plugins" / "tp-voyager"
LEGACY_PLUGIN = MARKETPLACE_ROOT / "plugins" / "tp-voyager-observability"
PLUGIN_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
PLUGIN_SKILL = PLUGIN / "skills" / "captain" / "SKILL.md"
LEGACY_ROOT_SKILL = CAPTAIN / "SKILL.md"
RUNTIME_MANIFEST = CAPTAIN / "tp-voyager.manifest.json"


class CodexPluginConsolidationTests(unittest.TestCase):
    def test_captain_manifest_keeps_exactly_seven_existing_tools(self) -> None:
        manifest = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["skill"]["version"], "1.1.0")
        tools = manifest["mcp"]["required_captain_tools"]
        self.assertEqual(len(tools), 7)
        self.assertIn("task_dispatch", tools)
        self.assertIn("task_result", tools)
        self.assertIn("render_voyager_panel", tools)

    def test_new_plugin_is_skills_only_and_exports_exactly_one_namespaced_captain_skill(self) -> None:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "tp-voyager")
        self.assertEqual(manifest["version"], "1.1.0")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["interface"]["displayName"], "TP-Voyager")
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("apps", manifest)
        self.assertFalse((PLUGIN / ".mcp.json").exists())
        self.assertFalse((PLUGIN / ".app.json").exists())

        skill_dirs = [path for path in (PLUGIN / "skills").iterdir() if path.is_dir()]
        self.assertEqual([path.name for path in skill_dirs], ["captain"])
        skill = PLUGIN_SKILL.read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?m)^name:\s*captain\s*$")
        # Codex namespaces plugin skills as <plugin-name>:<skill-name>, so this
        # pair is intentionally chosen to surface as $tp-voyager:captain.
        self.assertEqual(f"{manifest['name']}:captain", "tp-voyager:captain")

    def test_captain_skill_is_the_single_behavioral_source_and_contains_panel_rules(self) -> None:
        skill = PLUGIN_SKILL.read_text(encoding="utf-8")
        for required in (
            "task_dispatch",
            "task_result",
            "render_voyager_panel",
            "presentation_group_id",
            "read-only",
            "Never re-dispatch",
            "prompt",
            "secret",
            "raw tool output",
            "hidden/private",
        ):
            self.assertIn(required.lower(), skill.lower())
        self.assertIn("Captain chooses Crew", skill)
        self.assertIn("Captain chooses model", skill)

        self.assertNotIn("must be pinned to an explicit `task_id`", skill)
        self.assertIn("exact `presentation_group_id` or exact `task_ids`", skill)

        self.assertNotIn("sibling `tp-voyager.manifest.json`", skill)
        self.assertIn("runtime-side `tp-voyager.manifest.json`", skill)

        legacy = LEGACY_ROOT_SKILL.read_text(encoding="utf-8")
        self.assertIn("legacy migration shim", legacy.lower())
        self.assertIn("tp-voyager:captain", legacy)
        self.assertNotIn("## 3. Captain Responsibilities", legacy)
        self.assertNotIn("### Rule 1", legacy)

    def test_repo_marketplace_advertises_only_tp_voyager_but_retains_old_source_for_migration(self) -> None:
        marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        self.assertEqual(marketplace["name"], "tp-voyager-local")
        entries = marketplace["plugins"]
        self.assertEqual([entry["name"] for entry in entries], ["tp-voyager"])
        entry = entries[0]
        self.assertEqual(entry["source"]["source"], "local")
        self.assertEqual(entry["source"]["path"], "./plugins/tp-voyager")
        self.assertEqual(entry["policy"]["installation"], "INSTALLED_BY_DEFAULT")
        self.assertTrue((LEGACY_PLUGIN / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((LEGACY_PLUGIN / "skills" / "tp-voyager-observability" / "SKILL.md").is_file())

    def test_codex_docs_describe_new_conversation_and_explicit_legacy_cleanup(self) -> None:
        codex_doc = (CAPTAIN / "CODEX_DESKTOP.md").read_text(encoding="utf-8")
        integration_doc = (CODEX / "README.md").read_text(encoding="utf-8")
        combined = codex_doc + "\n" + integration_doc
        self.assertIn("tp-voyager:captain", combined)
        self.assertIn("new conversation", combined.lower())
        self.assertIn("tp-voyager-observability", combined)
        self.assertRegex(combined.lower(), r"(remove|uninstall).+tp-voyager-observability")
        self.assertIn("tp-voyager-captain", combined)
        self.assertRegex(combined.lower(), r"(remove|delete).+tp-voyager-captain")
        self.assertIn("清理前验证", combined)
        self.assertIn("清理后最终验收", combined)


if __name__ == "__main__":
    unittest.main()
