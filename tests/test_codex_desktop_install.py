from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
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

    def test_repository_skill_has_no_machine_absolute_paths_and_manifest_uses_bindings(self) -> None:
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

    def test_windows_default_target_is_user_codex_skills_directory(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install")
        drive = "C:"  # generic test drive, not a machine-specific repository path
        profile = str(PureWindowsPath(drive + "/") / "Users" / "example")
        home = installer.resolve_codex_home(
            environ={"USERPROFILE": profile}, platform="nt", home="ignored"
        )
        expected = str(PureWindowsPath(profile) / ".codex")
        self.assertEqual(home, expected)
        self.assertEqual(
            installer.installed_skill_path(home, platform="nt"),
            str(PureWindowsPath(expected) / "skills" / "tp-voyager-captain"),
        )

    def test_install_deploys_managed_files_preserves_unknown_files_and_is_idempotent(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install")
        codex_home = self.tempdir() / "codex-home"
        target = codex_home / "skills" / "tp-voyager-captain"
        target.mkdir(parents=True)
        extra = target / "my-user-notes.md"
        extra.write_text("keep me", encoding="utf-8")
        first = installer.install(
            SKILL,
            codex_home,
        )
        self.assertTrue(first["ok"])
        self.assertEqual(first["action"], "changed")
        self.assertIn(first["mcp_action"], {"added", "updated"})
        self.assertTrue((target / "install_codex_desktop.py").is_file())
        self.assertTrue((target / "sync_codex_desktop.py").is_file())
        self.assertEqual((target / "tp-voyager.manifest.json").read_bytes(), MANIFEST.read_bytes())
        self.assertEqual(extra.read_text(encoding="utf-8"), "keep me")
        binding_payload = json.loads((target / "tp-voyager.bindings.json").read_text(encoding="utf-8"))
        self.assertEqual(binding_payload["values"]["repository_root"], str(ROOT.resolve()))

        second = installer.install(
            SKILL,
            codex_home,
        )
        self.assertTrue(second["ok"])
        self.assertEqual(second["action"], "no-op")
        self.assertEqual(second["mcp_action"], "no-op")
        self.assertEqual(extra.read_text(encoding="utf-8"), "keep me")

    def test_installed_skill_cli_check_uses_saved_repository_root_and_is_read_only(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install")
        codex_home = self.tempdir() / "codex-home"
        codex_home.mkdir(parents=True)
        config = codex_home / "config.toml"
        unrelated = (
            '# user comment\nmodel = "gpt-5.6"\n\n'
            '[mcp_servers.other]\ncommand = "other-server"\n\n'
            '[projects."E:\\\\work"]\ntrust_level = "trusted"\n\n'
            '[plugins.example]\nenabled = true\n'
        )
        config.write_text(unrelated, encoding="utf-8")
        installer.install(SKILL, codex_home)
        target = codex_home / "skills" / "tp-voyager-captain"
        installed = _module(target / "install_codex_desktop.py", "tp_voyager_installed_check")

        bindings = json.loads((target / "tp-voyager.bindings.json").read_text(encoding="utf-8"))
        self.assertEqual(bindings["values"]["repository_root"], str(ROOT.resolve()))
        shutil.rmtree(target / "__pycache__", ignore_errors=True)
        before_skill = {
            path.relative_to(target).as_posix(): path.read_bytes()
            for path in target.rglob("*")
            if path.is_file()
        }
        before_config = config.read_bytes()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = installed.main(["--codex-home", str(codex_home), "--check"])
        checked = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertTrue(checked["ok"])
        self.assertEqual(checked["action"], "check-ok")
        self.assertFalse(checked["provider_invocation_performed"])
        self.assertFalse(checked["task_dispatch_performed"])
        after_skill = {
            path.relative_to(target).as_posix(): path.read_bytes()
            for path in target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before_skill, after_skill)
        self.assertEqual(before_config, config.read_bytes())
        self.assertIn(unrelated, config.read_text(encoding="utf-8"))

    def test_installed_skill_check_without_repository_root_fails_closed(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install")
        codex_home = self.tempdir() / "codex-home"
        installer.install(SKILL, codex_home)
        target = codex_home / "skills" / "tp-voyager-captain"
        binding_path = target / "tp-voyager.bindings.json"
        payload = json.loads(binding_path.read_text(encoding="utf-8"))
        payload["values"].pop("repository_root")
        binding_path.write_text(json.dumps(payload), encoding="utf-8")
        before_binding = binding_path.read_bytes()
        config = codex_home / "config.toml"
        before_config = config.read_bytes()
        installed = _module(target / "install_codex_desktop.py", "tp_voyager_installed_missing_root")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = installed.main(["--codex-home", str(codex_home), "--check"])
        checked = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 1)
        self.assertFalse(checked["ok"])
        self.assertIn("repository_root binding is unavailable", checked["error"])
        self.assertEqual(before_binding, binding_path.read_bytes())
        self.assertEqual(before_config, config.read_bytes())
        self.assertNotIn(str(codex_home.resolve()), binding_path.read_text(encoding="utf-8"))

    def test_installed_skill_check_missing_or_corrupt_bindings_fails_read_only(self) -> None:
        for mode in ("missing", "corrupt"):
            with self.subTest(mode=mode):
                installer = _module(INSTALLER, f"tp_voyager_install_{mode}")
                codex_home = self.tempdir() / f"codex-home-{mode}"
                installer.install(SKILL, codex_home)
                target = codex_home / "skills" / "tp-voyager-captain"
                binding_path = target / "tp-voyager.bindings.json"
                if mode == "missing":
                    binding_path.unlink()
                else:
                    binding_path.write_text("{broken", encoding="utf-8")
                before_binding = binding_path.read_bytes() if binding_path.exists() else None
                config = codex_home / "config.toml"
                before_config = config.read_bytes()
                installed = _module(target / "install_codex_desktop.py", f"tp_voyager_installed_{mode}")

                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = installed.main(["--codex-home", str(codex_home), "--check"])
                checked = json.loads(stdout.getvalue())

                self.assertEqual(exit_code, 1)
                self.assertFalse(checked["ok"])
                after_binding = binding_path.read_bytes() if binding_path.exists() else None
                self.assertEqual(before_binding, after_binding)
                self.assertEqual(before_config, config.read_bytes())

    def test_check_detects_skill_or_config_drift_without_writing(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install")
        codex_home = self.tempdir() / "codex-home"
        installer.install(SKILL, codex_home)
        target = codex_home / "skills" / "tp-voyager-captain"

        ok = installer.install(SKILL, codex_home, check_only=True)
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["action"], "check-ok")

        victim = target / "CODEX_DESKTOP.md"
        before = victim.read_bytes()
        victim.write_text(victim.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")
        failed = installer.install(SKILL, codex_home, check_only=True)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["action"], "check-failed")
        self.assertIn("CODEX_DESKTOP.md", failed["skill_drift"])
        self.assertNotEqual(before, victim.read_bytes())

        # Restore Skill, then drift the managed MCP cwd. --check must report it and leave bytes unchanged.
        shutil.copy2(SKILL / "CODEX_DESKTOP.md", victim)
        config = codex_home / "config.toml"
        config_text = config.read_text(encoding="utf-8").replace(
            f'cwd = "{str(ROOT.resolve()).replace(chr(92), chr(92) * 2)}"',
            'cwd = "drifted"',
        )
        config.write_text(config_text, encoding="utf-8")
        config_before = config.read_bytes()
        config_failed = installer.install(SKILL, codex_home, check_only=True)
        self.assertFalse(config_failed["ok"])
        self.assertEqual(config_failed["action"], "check-failed")
        self.assertTrue(config_failed["mcp_drift"])
        self.assertEqual(config_before, config.read_bytes())

    def test_repository_install_requires_no_machine_cli_binding(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install")
        codex_home = self.tempdir() / "codex-home"
        result = installer.install(SKILL, codex_home, bindings={})
        self.assertTrue(result["ok"])
        self.assertEqual(result["binding_keys"], ["repository_root"])

    def test_manifest_missing_required_field_fails_closed(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install")
        source = self.tempdir() / "skill"
        shutil.copytree(SKILL, source)
        payload = json.loads((source / "tp-voyager.manifest.json").read_text(encoding="utf-8"))
        payload["mcp"].pop("required_captain_tools")
        (source / "tp-voyager.manifest.json").write_text(json.dumps(payload), encoding="utf-8")
        codex_home = self.tempdir() / "codex-home"
        with self.assertRaises((installer.InstallError, ValueError)):
            installer.install(
                source,
                codex_home,
                bindings={"repository_root": str(ROOT.resolve())},
            )
        self.assertFalse((codex_home / "skills" / "tp-voyager-captain").exists())

    def test_audit_output_exposes_only_repository_root_binding_key(self) -> None:
        installer = _module(INSTALLER, "tp_voyager_install")
        codex_home = self.tempdir() / "codex-home"
        result = installer.install(SKILL, codex_home)
        self.assertEqual(result["binding_keys"], ["repository_root"])
        self.assertFalse(result["provider_invocation_performed"])
        self.assertFalse(result["task_dispatch_performed"])


if __name__ == "__main__":
    unittest.main()
