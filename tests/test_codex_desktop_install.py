from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "tp-voyager-captain"
INSTALLER = SKILL / "install_codex_desktop.py"
MANIFEST = SKILL / "tp-voyager.manifest.json"


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodexDesktopInstallTests(unittest.TestCase):
    def tempdir(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="tp-codex-install-"))
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def _fake_codex_cli(self, script_body: str) -> Path:
        base = self.tempdir() / "codex"
        script = base.with_suffix(".py")
        script.write_text(script_body, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        if os.name == "nt":
            launcher = base.with_suffix(".cmd")
            launcher.write_text(
                f'@echo off\r\n"{sys.executable}" "%~dp0{script.name}" %*\r\n',
                encoding="utf-8",
            )
            return launcher
        return script

    def test_repository_bootstrap_has_no_machine_absolute_paths_and_manifest_uses_bindings(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mcp = manifest["mcp"]
        self.assertEqual(mcp["name"], "tp_voyager")
        self.assertEqual(mcp["command"], ["python", "-m", "agent_runtime.server"])
        self.assertEqual(mcp["cwd"], {"binding": "repository_root", "required": True})
        self.assertEqual(mcp["env"], {})
        for path in SKILL.rglob("*"):
            if not path.is_file() or path.name.endswith((".pyc", ".pyo")):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("updateProject", text, str(path))
            self.assertNotIn("Program Files", text, str(path))
            self.assertNotIn("Users\\tangpeng", text, str(path))

    def test_windows_codex_home_and_legacy_skill_path_are_portable(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install_path")
        profile = str(PureWindowsPath("C:/") / "Users" / "example")
        home = installer.resolve_codex_home(
            environ={"USERPROFILE": profile}, platform="nt", home="ignored"
        )
        expected = str(PureWindowsPath(profile) / ".codex")
        self.assertEqual(home, expected)
        self.assertEqual(
            installer.installed_skill_path(home, platform="nt"),
            str(PureWindowsPath(expected) / "skills" / "tp-voyager-captain"),
        )

    def test_clean_install_converges_single_plugin_mcp_and_agents_without_installing_global_skill(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_clean_install")
        user_home = self.tempdir() / "user"
        codex_home = user_home / ".codex"
        codex_home.mkdir(parents=True)
        agents = codex_home / "AGENTS.md"
        agents.write_text("# User rules\n\nkeep\n", encoding="utf-8")

        first = installer.install(SKILL, codex_home, user_home=user_home, codex_cli="")

        self.assertTrue(first["ok"], first)
        self.assertTrue(first["mcp_registered"])
        self.assertTrue(first["plugin_files_installed"])
        self.assertEqual(first["plugin_name"], "tp-voyager")
        self.assertFalse((codex_home / "skills" / "tp-voyager-captain").exists())
        plugin = codex_home / "plugins" / "tp-voyager"
        self.assertTrue((plugin / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((plugin / "skills" / "captain" / "SKILL.md").is_file())
        self.assertFalse((plugin / ".mcp.json").exists())
        self.assertIn("# User rules", agents.read_text(encoding="utf-8"))
        self.assertIn("TP-Voyager managed guidance", agents.read_text(encoding="utf-8"))
        self.assertTrue(first["new_conversation_required"])

        marketplace = user_home / ".agents" / "plugins" / "marketplace.json"
        market = json.loads(marketplace.read_text(encoding="utf-8"))
        self.assertEqual([item["name"] for item in market["plugins"]], ["tp-voyager"])
        self.assertEqual(market["plugins"][0]["source"]["path"], "./.codex/plugins/tp-voyager")

        second = installer.install(SKILL, codex_home, user_home=user_home, codex_cli="")
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["mcp_action"], "no-op")
        self.assertEqual(second["action"], "no-op")
        self.assertFalse((codex_home / "skills" / "tp-voyager-captain").exists())

    def test_migration_preserves_legacy_skill_and_observability_plugin_and_returns_cleanup_steps(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_legacy_preserve")
        user_home = self.tempdir() / "user"
        codex_home = user_home / ".codex"
        legacy_skill = codex_home / "skills" / "tp-voyager-captain"
        legacy_plugin = codex_home / "plugins" / "tp-voyager-observability"
        legacy_skill.mkdir(parents=True)
        legacy_plugin.mkdir(parents=True)
        (legacy_skill / "sentinel.txt").write_text("legacy skill", encoding="utf-8")
        (legacy_plugin / "sentinel.txt").write_text("legacy plugin", encoding="utf-8")

        result = installer.install(SKILL, codex_home, user_home=user_home, codex_cli="")

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["legacy_skill_present"])
        self.assertTrue(result["legacy_observability_plugin_present"])
        self.assertEqual((legacy_skill / "sentinel.txt").read_text(encoding="utf-8"), "legacy skill")
        self.assertEqual((legacy_plugin / "sentinel.txt").read_text(encoding="utf-8"), "legacy plugin")
        cleanup = "\n".join(result["legacy_cleanup_steps"])
        self.assertIn("tp-voyager-observability", cleanup)
        self.assertIn("tp-voyager-captain", cleanup)
        self.assertNotIn("sentinel", cleanup)

    def test_agents_first_insert_preserves_existing_user_bytes_before_managed_block(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_agents_prefix_preservation")
        existing = "# User rules\nkeep trailing spaces  \n\n"
        rendered, changed = installer._managed_block(existing)
        self.assertTrue(changed)
        self.assertTrue(rendered.startswith(existing))
        self.assertIn("TP-Voyager managed guidance", rendered)

    def test_install_updates_only_managed_agents_block_and_reports_override_shadowing(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install_agents_update")
        user_home = self.tempdir() / "user"
        codex_home = user_home / ".codex"
        codex_home.mkdir(parents=True)
        agents = codex_home / "AGENTS.md"
        agents.write_text(
            "before\n\n<!-- >>> TP-Voyager managed guidance >>> -->\nold voyager text\n"
            "<!-- <<< TP-Voyager managed guidance <<< -->\n\nafter\n",
            encoding="utf-8",
        )
        (codex_home / "AGENTS.override.md").write_text("temporary override\n", encoding="utf-8")

        result = installer.install(SKILL, codex_home, user_home=user_home, codex_cli="")

        rendered = agents.read_text(encoding="utf-8")
        self.assertIn("before", rendered)
        self.assertIn("after", rendered)
        self.assertNotIn("old voyager text", rendered)
        self.assertEqual(rendered.count("<!-- >>> TP-Voyager managed guidance >>> -->"), 1)
        self.assertTrue(result["agents_guidance_installed"])
        self.assertFalse(result["agents_guidance_effective"])
        self.assertTrue(result["agents_override_present"])

    def test_check_detects_plugin_agents_marketplace_or_mcp_drift_without_writing(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_check_drift")
        user_home = self.tempdir() / "user"
        codex_home = user_home / ".codex"
        installer.install(SKILL, codex_home, user_home=user_home, codex_cli="")

        plugin_manifest = codex_home / "plugins" / "tp-voyager" / ".codex-plugin" / "plugin.json"
        plugin_manifest.write_text("{}\n", encoding="utf-8")
        agents = codex_home / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8").replace("render_voyager_panel", "render_missing_panel"), encoding="utf-8")
        marketplace = user_home / ".agents" / "plugins" / "marketplace.json"
        market = json.loads(marketplace.read_text(encoding="utf-8"))
        market["plugins"] = []
        marketplace.write_text(json.dumps(market, indent=2) + "\n", encoding="utf-8")
        config = codex_home / "config.toml"
        config.write_text(config.read_text(encoding="utf-8").replace(str(ROOT.resolve()).replace("\\", "\\\\"), "drifted"), encoding="utf-8")
        before = {p: p.read_bytes() for p in (plugin_manifest, agents, marketplace, config)}

        checked = installer.install(SKILL, codex_home, user_home=user_home, codex_cli="", check_only=True)

        self.assertFalse(checked["ok"])
        self.assertFalse(checked["plugin_files_installed"])
        self.assertFalse(checked["agents_guidance_installed"])
        self.assertFalse(checked["marketplace_registered"])
        self.assertTrue(checked["mcp_drift"])
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_installer_uses_codex_cli_to_install_and_verify_new_plugin(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install_codex_cli")
        user_home = self.tempdir() / "user"
        codex_home = user_home / ".codex"
        state = self.tempdir() / "installed"
        fake_cli = self._fake_codex_cli(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            f"state = pathlib.Path({str(state)!r})\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['plugin', 'list']:\n"
            "    installed = [{'name':'tp-voyager','marketplaceName':'personal','installed':True,'enabled':True}] if state.exists() else []\n"
            "    print(json.dumps({'installed': installed, 'available': []}))\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['plugin', 'add']:\n"
            "    state.write_text('installed')\n"
            "    print(json.dumps({'name':'tp-voyager','marketplaceName':'personal','installedPath':'cache/local'}))\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['plugin', 'remove']:\n"
            "    state.unlink(missing_ok=True)\n"
            "    print(json.dumps({'name':'tp-voyager','removed':True}))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(2)\n"
        )

        first = installer.install(SKILL, codex_home, user_home=user_home, codex_cli=fake_cli)
        self.assertTrue(first["ok"], first)
        self.assertTrue(first["plugin_installed"])
        self.assertTrue(first["plugin_enabled"])
        self.assertFalse(first["plugin_installation_pending"])
        self.assertEqual(first["plugin_install_method"], "codex-cli")

        second = installer.install(SKILL, codex_home, user_home=user_home, codex_cli=fake_cli)
        self.assertEqual(second["action"], "no-op")
        self.assertFalse(second["plugin_cache_refreshed"])

    def test_managed_plugin_drift_forces_remove_add_cache_refresh_without_touching_legacy(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_cache_refresh")
        source = self.tempdir() / "tp-voyager-captain"
        shutil.copytree(SKILL, source)
        user_home = self.tempdir() / "user"
        codex_home = user_home / ".codex"
        legacy = codex_home / "plugins" / "tp-voyager-observability"
        legacy.mkdir(parents=True)
        (legacy / "sentinel").write_text("keep", encoding="utf-8")
        state = self.tempdir() / "installed"
        calls = self.tempdir() / "calls"
        fake_cli = self._fake_codex_cli(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            f"state = pathlib.Path({str(state)!r})\n"
            f"calls = pathlib.Path({str(calls)!r})\n"
            "args = sys.argv[1:]\n"
            "calls.write_text((calls.read_text() if calls.exists() else '') + ' '.join(args) + '\\n')\n"
            "if args[:2] == ['plugin', 'list']:\n"
            "    installed = [{'name':'tp-voyager','marketplaceName':'personal','installed':True,'enabled':True}] if state.exists() else []\n"
            "    print(json.dumps({'installed': installed, 'available': []})); raise SystemExit(0)\n"
            "if args[:2] == ['plugin', 'add']:\n"
            "    state.write_text('installed'); print(json.dumps({'name':'tp-voyager'})); raise SystemExit(0)\n"
            "if args[:2] == ['plugin', 'remove']:\n"
            "    state.unlink(missing_ok=True); print(json.dumps({'name':'tp-voyager'})); raise SystemExit(0)\n"
            "raise SystemExit(2)\n"
        )
        bindings = {"repository_root": str(ROOT.resolve())}
        installer.install(source, codex_home, user_home=user_home, codex_cli=fake_cli, bindings=bindings)
        calls.write_text("", encoding="utf-8")
        plugin_readme = source / "integrations" / "codex" / "local-marketplace" / "plugins" / "tp-voyager" / "README.md"
        plugin_readme.write_text(plugin_readme.read_text(encoding="utf-8") + "\ncachebuster\n", encoding="utf-8")

        refreshed = installer.install(source, codex_home, user_home=user_home, codex_cli=fake_cli, bindings=bindings)

        command_log = calls.read_text(encoding="utf-8")
        self.assertTrue(refreshed["plugin_cache_refreshed"], refreshed)
        self.assertIn("plugin remove tp-voyager@personal --json", command_log)
        self.assertIn("plugin add tp-voyager@personal --json", command_log)
        self.assertEqual((legacy / "sentinel").read_text(encoding="utf-8"), "keep")

    def test_check_only_uses_codex_cli_status_without_installing_or_refreshing(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_check_codex_cli")
        user_home = self.tempdir() / "user"
        codex_home = user_home / ".codex"
        calls = self.tempdir() / "calls"
        fake_cli = self._fake_codex_cli(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            f"calls = pathlib.Path({str(calls)!r})\n"
            "args = sys.argv[1:]\n"
            "calls.write_text((calls.read_text() if calls.exists() else '') + ' '.join(args) + '\\n')\n"
            "if args[:2] == ['plugin', 'list']:\n"
            "    print(json.dumps({'installed': [], 'available': []})); raise SystemExit(0)\n"
            "raise SystemExit(9)\n"
        )
        installer.install(SKILL, codex_home, user_home=user_home, codex_cli="")

        checked = installer.install(SKILL, codex_home, user_home=user_home, codex_cli=fake_cli, check_only=True)

        self.assertTrue(checked["plugin_files_installed"])
        self.assertFalse(checked["plugin_installed"])
        command_log = calls.read_text(encoding="utf-8")
        self.assertIn("plugin list --json", command_log)
        self.assertNotIn("plugin add", command_log)
        self.assertNotIn("plugin remove", command_log)

    def test_repository_install_requires_only_repository_root_binding_and_never_dispatches(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install_audit")
        codex_home = self.tempdir() / "codex-home"
        result = installer.install(SKILL, codex_home, codex_cli="")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["binding_keys"], ["repository_root"])
        self.assertFalse(result["provider_invocation_performed"])
        self.assertFalse(result["task_dispatch_performed"])
        self.assertFalse((codex_home / "skills" / "tp-voyager-captain").exists())

    def test_manifest_missing_required_field_fails_closed_before_install(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install_invalid_manifest")
        source = self.tempdir() / "tp-voyager-captain"
        shutil.copytree(SKILL, source)
        payload = json.loads((source / "tp-voyager.manifest.json").read_text(encoding="utf-8"))
        payload["mcp"].pop("required_captain_tools")
        (source / "tp-voyager.manifest.json").write_text(json.dumps(payload), encoding="utf-8")
        user_home = self.tempdir() / "user"
        codex_home = user_home / ".codex"
        with self.assertRaises((installer.InstallError, ValueError)):
            installer.install(
                source,
                codex_home,
                user_home=user_home,
                codex_cli="",
                bindings={"repository_root": str(ROOT.resolve())},
            )
        self.assertFalse((codex_home / "plugins" / "tp-voyager").exists())
        self.assertFalse((codex_home / "skills" / "tp-voyager-captain").exists())


if __name__ == "__main__":
    unittest.main()
