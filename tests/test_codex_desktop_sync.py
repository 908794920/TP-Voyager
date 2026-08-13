from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "tp-voyager-captain"
SCRIPT = SKILL / "sync_codex_desktop.py"
MANIFEST = SKILL / "tp-voyager.manifest.json"


def _module():
    spec = importlib.util.spec_from_file_location("tp_voyager_codex_sync", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodexDesktopSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sync = _module()
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def temp_path(self, name: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix="tp-codex-sync-"))
        self.addCleanup(__import__("shutil").rmtree, root, True)
        return root / name

    def test_manifest_is_single_launch_truth_for_current_machine_contract(self) -> None:
        mcp = self.manifest["mcp"]
        self.assertEqual(mcp["name"], "tp_voyager")
        self.assertEqual(mcp["command"], ["python", "-m", "agent_runtime.server"])
        self.assertEqual(mcp["cwd"], r"E:\updateProject\TP_Voyager")
        self.assertEqual(mcp["env"]["QODER_CLI_PATH"], r"~\.agent-runtime\bin\qodercli-qoder-client.cmd")
        self.assertEqual(mcp["env"]["CODEBUDDY_CODE_PATH"], r"C:\Program Files\nodejs\node_global\codebuddy.cmd")
        self.assertEqual(len(mcp["required_captain_tools"]), 6)
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("agent_runtime.server", source)
        for tool in mcp["required_captain_tools"]:
            self.assertNotIn(f'"{tool}"', source)
        self.assertNotIn("CODEBUDDY_CLI_PATH", source)

    def test_add_preserves_unrelated_config_comments_other_mcp_and_project_trust(self) -> None:
        config = self.temp_path("config.toml")
        unrelated = (
            '# user comment\nmodel = "gpt-5.6"\n\n'
            '[mcp_servers.other]\ncommand = "other-server"\n\n'
            '[projects."E:\\\\work"]\ntrust_level = "trusted"\n\n'
            '[plugins.example]\nenabled = true\n'
        )
        config.write_text(unrelated, encoding="utf-8")
        result = self.sync.sync(MANIFEST, config)
        self.assertEqual(result["action"], "added")
        text = config.read_text(encoding="utf-8")
        self.assertIn(unrelated, text)
        self.assertEqual(text.count("[mcp_servers.tp_voyager]"), 1)
        self.assertEqual(text.count("[mcp_servers.tp_voyager.env]"), 1)
        self.assertIn('[mcp_servers.other]\ncommand = "other-server"', text)
        self.assertIn('trust_level = "trusted"', text)
        self.assertFalse(result["secrets_returned"])
        self.assertEqual(result["entry"]["env_keys"], ["CODEBUDDY_CODE_PATH", "QODER_CLI_PATH"])
        serialized = json.dumps(result, ensure_ascii=False)
        for value in self.manifest["mcp"]["env"].values():
            self.assertNotIn(value, serialized)

    def test_existing_target_keeps_unknown_fields_and_comments_while_managed_fields_update(self) -> None:
        config = self.temp_path("config.toml")
        config.write_text(
            '[mcp_servers.tp_voyager]\n'
            '# keep me\n'
            'startup_timeout_sec = 45\n'
            'command = "old-python"\n'
            'args = ["old"]\n'
            'cwd = "C:\\\\old"\n'
            'enabled_tools = ["old_tool"]\n\n'
            '[mcp_servers.tp_voyager.env]\n'
            '# keep env comment\n'
            'CUSTOM_KEEP = "yes"\n'
            'QODER_CLI_PATH = "old"\n'
            'CODEBUDDY_CODE_PATH = "old"\n',
            encoding="utf-8",
        )
        result = self.sync.sync(MANIFEST, config)
        self.assertEqual(result["action"], "updated")
        text = config.read_text(encoding="utf-8")
        self.assertIn("# keep me", text)
        self.assertIn("startup_timeout_sec = 45", text)
        self.assertIn("# keep env comment", text)
        self.assertIn('CUSTOM_KEEP = "yes"', text)
        self.assertNotIn('command = "old-python"', text)
        self.assertEqual(self.sync.check_text(text, self.sync.load_manifest(MANIFEST)[0]), [])

    def test_second_sync_is_noop_and_hash_is_unchanged(self) -> None:
        config = self.temp_path("config.toml")
        first = self.sync.sync(MANIFEST, config)
        second = self.sync.sync(MANIFEST, config)
        self.assertEqual(first["action"], "added")
        self.assertEqual(second["action"], "no-op")
        self.assertEqual(second["config_sha256_before"], second["config_sha256_after"])
        self.assertEqual(config.read_text(encoding="utf-8").count("[mcp_servers.tp_voyager]"), 1)

    def test_manifest_change_updates_only_tp_voyager_managed_fields(self) -> None:
        config = self.temp_path("config.toml")
        config.write_text('# x\n[mcp_servers.other]\ncommand = "keep"\n', encoding="utf-8")
        self.sync.sync(MANIFEST, config)
        before = config.read_text(encoding="utf-8")
        manifest_copy = self.temp_path("manifest.json")
        changed = json.loads(MANIFEST.read_text(encoding="utf-8"))
        changed["mcp"]["cwd"] = r"E:\updateProject\TP_Voyager_2"
        manifest_copy.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        result = self.sync.sync(manifest_copy, config)
        self.assertEqual(result["action"], "updated")
        after = config.read_text(encoding="utf-8")
        self.assertIn('# x\n[mcp_servers.other]\ncommand = "keep"', after)
        self.assertIn("TP_Voyager_2", after)
        self.assertNotEqual(before, after)

    def test_check_is_read_only_and_reports_missing_or_synced(self) -> None:
        config = self.temp_path("config.toml")
        missing = self.sync.sync(MANIFEST, config, check_only=True)
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["action"], "check-failed")
        self.assertFalse(config.exists())
        self.sync.sync(MANIFEST, config)
        before = config.read_bytes()
        checked = self.sync.sync(MANIFEST, config, check_only=True)
        self.assertTrue(checked["ok"])
        self.assertEqual(checked["action"], "check-ok")
        self.assertEqual(before, config.read_bytes())

    def test_duplicate_target_section_fails_closed_without_modifying_config(self) -> None:
        config = self.temp_path("config.toml")
        body = '[mcp_servers.tp_voyager]\ncommand="a"\n[mcp_servers.tp_voyager]\ncommand="b"\n'
        config.write_text(body, encoding="utf-8")
        with self.assertRaises(self.sync.SyncError):
            self.sync.sync(MANIFEST, config)
        self.assertEqual(config.read_text(encoding="utf-8"), body)


if __name__ == "__main__":
    unittest.main()
