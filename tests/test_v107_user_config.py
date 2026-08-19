from __future__ import annotations

import importlib
import importlib.util
import json
import os
import tempfile
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.persistence import runtime_paths


def _import_mcp_server():
    try:
        from agent_runtime.api import mcp_server
        return mcp_server
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fast_mod = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.registered_tools = {}

        def tool(self, *args, **kwargs):
            del args, kwargs
            def decorate(func):
                self.registered_tools[func.__name__] = func
                return func
            return decorate

        def resource(self, *args, **kwargs):
            del args, kwargs
            def decorate(func):
                return func
            return decorate

        def run(self, *args, **kwargs):
            del args, kwargs

    fast_mod.FastMCP = FastMCP
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.fastmcp"] = fast_mod
    from agent_runtime.api import mcp_server
    return mcp_server


class VoyagerHomeV107Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _clean_env(self):
        return patch.dict(
            os.environ,
            {
                "TP_VOYAGER_HOME": "",
                "TP_VOYAGER_DB": "",
                "AGENT_RUNTIME_HOME": "",
                "AGENT_RUNTIME_DB": "",
                "WORKBUDDY_RUNTIME_DB": "",
                "WORKBUDDY_CONFIG_DIR": "",
                "CODEBUDDY_CONFIG_DIR": "",
            },
            clear=False,
        )

    def test_default_home_is_tp_voyager(self) -> None:
        with self._clean_env(), patch.object(Path, "home", return_value=self.root):
            self.assertEqual(runtime_paths.canonical_runtime_home(), (self.root / ".tp-voyager").resolve())
            self.assertEqual(
                runtime_paths.canonical_runtime_database_path(),
                (self.root / ".tp-voyager" / "runtime" / "tp_voyager.db").resolve(),
            )

    def test_only_tp_voyager_home_and_db_override_runtime_paths(self) -> None:
        home = self.root / "voyager-home"
        db = self.root / "custom" / "voyager.db"
        with self._clean_env(), patch.dict(
            os.environ,
            {
                "TP_VOYAGER_HOME": str(home),
                "TP_VOYAGER_DB": str(db),
                "AGENT_RUNTIME_HOME": str(self.root / "ignored-agent-home"),
                "AGENT_RUNTIME_DB": str(self.root / "ignored-agent.db"),
                "WORKBUDDY_RUNTIME_DB": str(self.root / "ignored-workbuddy.db"),
            },
            clear=False,
        ):
            resolution = runtime_paths.resolve_runtime_database()
        self.assertEqual(runtime_paths.canonical_runtime_home() if False else home.resolve(), home.resolve())
        self.assertEqual(resolution.database, db.resolve())
        self.assertEqual(resolution.source, "TP_VOYAGER_DB")
        self.assertEqual(resolution.canonical_database, (home / "runtime" / "tp_voyager.db").resolve())

    def test_legacy_database_is_never_selected_automatically(self) -> None:
        legacy = self.root / ".workbuddy" / "runtime" / "workbuddy_runtime.db"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"legacy")
        with self._clean_env(), patch.object(Path, "home", return_value=self.root):
            resolution = runtime_paths.resolve_runtime_database()
        self.assertEqual(
            resolution.database,
            (self.root / ".tp-voyager" / "runtime" / "tp_voyager.db").resolve(),
        )
        self.assertEqual(resolution.source, "canonical_default")


class VoyagerUserConfigV107Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.home = Path(self.tmp.name) / ".tp-voyager"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _module(self):
        spec = importlib.util.find_spec("agent_runtime.configuration.user_config")
        self.assertIsNotNone(spec, "v1.0.7 must provide agent_runtime.configuration.user_config")
        return importlib.import_module("agent_runtime.configuration.user_config")

    def test_missing_config_loads_safe_in_memory_defaults_without_writing(self) -> None:
        mod = self._module()
        config = mod.VoyagerUserConfig.load(self.home)
        self.assertFalse((self.home / "config.json").exists())
        self.assertEqual(config.schema, "tp-voyager.config/v2")
        self.assertTrue(config.crew.qoder.enabled)
        self.assertTrue(config.crew.codebuddy.enabled)
        self.assertEqual(config.crew.codebuddy.internet_environment, "internal")
        self.assertEqual(
            config.dispatch.allowed_models,
            (
                "qoder:lite",
                "qoder:qmodel_38max",
                "codebuddy:hy3",
                "codebuddy:deepseek-v4-flash",
            ),
        )
        self.assertEqual(config.crew.qoder.max_concurrent_tasks, 2)
        self.assertEqual(config.crew.codebuddy.max_concurrent_tasks, 2)
        self.assertNotIn("runtime", config.to_dict())

    def test_initialize_discovers_cli_paths_and_is_idempotent(self) -> None:
        mod = self._module()
        qoder = Path(self.tmp.name) / "bin" / "qodercli.exe"
        codebuddy = Path(self.tmp.name) / "bin" / "codebuddy.exe"
        qoder.parent.mkdir(parents=True)
        qoder.write_text("q", encoding="utf-8")
        codebuddy.write_text("c", encoding="utf-8")

        def which(name: str):
            if name.startswith("qodercli"):
                return str(qoder)
            if name in {"codebuddy", "codebuddy.cmd", "codebuddy.exe", "cbc", "cbc.cmd", "cbc.exe"}:
                return str(codebuddy)
            return None

        with patch("shutil.which", side_effect=which):
            first = mod.VoyagerUserConfig.initialize(self.home)
        payload = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(first["status"], "installed")
        self.assertEqual(payload["crew"]["qoder"]["cli_path"], str(qoder.resolve()))
        self.assertEqual(payload["crew"]["codebuddy"]["cli_path"], str(codebuddy.resolve()))

        payload["crew"]["qoder"]["cli_path"] = str(Path(self.tmp.name) / "custom-qoder.exe")
        (self.home / "config.json").write_text(json.dumps(payload), encoding="utf-8")
        second = mod.VoyagerUserConfig.initialize(self.home)
        self.assertEqual(second["status"], "already_exists")
        reloaded = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(reloaded["crew"]["qoder"]["cli_path"], payload["crew"]["qoder"]["cli_path"])

    def test_config_is_strict_and_rejects_unknown_top_level_keys(self) -> None:
        mod = self._module()
        self.home.mkdir(parents=True)
        payload = mod.VoyagerUserConfig.defaults(self.home).to_dict()
        payload["mystery"] = True
        (self.home / "config.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(mod.VoyagerUserConfigError):
            mod.VoyagerUserConfig.load(self.home)

    def test_trusted_roots_require_aliases_and_absolute_paths(self) -> None:
        mod = self._module()
        self.home.mkdir(parents=True)
        payload = mod.VoyagerUserConfig.defaults(self.home).to_dict()
        payload["trusted_roots"]["instructions"] = {"ai_work": "relative/path"}
        (self.home / "config.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(mod.VoyagerUserConfigError, "absolute"):
            mod.VoyagerUserConfig.load(self.home)

    def test_dispatch_allowlist_requires_unique_backend_qualified_models(self) -> None:
        mod = self._module()
        self.home.mkdir(parents=True)
        payload = mod.VoyagerUserConfig.defaults(self.home).to_dict()
        payload["dispatch"]["allowed_models"] = ["hy3", "hy3"]
        (self.home / "config.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(mod.VoyagerUserConfigError, "backend-qualified"):
            mod.VoyagerUserConfig.load(self.home)

    def test_crew_concurrency_limits_are_independently_bounded(self) -> None:
        mod = self._module()
        self.home.mkdir(parents=True)
        payload = mod.VoyagerUserConfig.defaults(self.home).to_dict()
        for crew_name in ("qoder", "codebuddy"):
            invalid = json.loads(json.dumps(payload))
            invalid["crew"][crew_name]["max_concurrent_tasks"] = 0
            (self.home / "config.json").write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(
                mod.VoyagerUserConfigError,
                rf"crew\.{crew_name}\.max_concurrent_tasks",
            ):
                mod.VoyagerUserConfig.load(self.home)

    def test_removed_runtime_concurrency_key_is_rejected(self) -> None:
        mod = self._module()
        self.home.mkdir(parents=True)
        payload = mod.VoyagerUserConfig.defaults(self.home).to_dict()
        payload["runtime"] = {"max_concurrent_tasks": 64}
        (self.home / "config.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(mod.VoyagerUserConfigError, "config schema is invalid"):
            mod.VoyagerUserConfig.load(self.home)


if __name__ == "__main__":
    unittest.main()


class VoyagerConfigConsumersV107Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.home = self.root / ".tp-voyager"
        self.home.mkdir(parents=True)

    def tearDown(self) -> None:
        try:
            mcp_server = _import_mcp_server()
            mcp_server.configure_runtime_database(None)
        except Exception:
            pass
        self.tmp.cleanup()

    def _write_config(self, mutate=None):
        from agent_runtime.configuration import VoyagerUserConfig
        payload = VoyagerUserConfig.defaults(self.home).to_dict()
        if mutate is not None:
            mutate(payload)
        (self.home / "config.json").write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _runtime_env(self, **extra):
        values = {
            "TP_VOYAGER_HOME": str(self.home),
            "QODER_CLI_PATH": "",
            "CODEBUDDY_CODE_PATH": "",
            "CODEBUDDY_INTERNET_ENVIRONMENT": "",
        }
        values.update(extra)
        return patch.dict(os.environ, values, clear=False)

    def test_qoder_cli_resolution_uses_config_then_env_then_path(self) -> None:
        from agent_runtime.backends.qoder.process import resolve_qoder_cli
        configured = self.root / "configured-qoder.exe"
        override = self.root / "override-qoder.exe"
        configured.write_text("configured", encoding="utf-8")
        override.write_text("override", encoding="utf-8")
        self._write_config(lambda p: p["crew"]["qoder"].update({"cli_path": str(configured.resolve())}))
        with self._runtime_env(), patch("agent_runtime.backends.qoder.process.shutil.which", return_value=None):
            self.assertEqual(resolve_qoder_cli(), str(configured.resolve()))
        with self._runtime_env(QODER_CLI_PATH=str(override.resolve())), patch(
            "agent_runtime.backends.qoder.process.shutil.which", return_value=None
        ):
            self.assertEqual(resolve_qoder_cli(), str(configured.resolve()))
        self._write_config(lambda p: p["crew"]["qoder"].update({"cli_path": ""}))
        with self._runtime_env(QODER_CLI_PATH=str(override.resolve())), patch(
            "agent_runtime.backends.qoder.process.shutil.which", return_value=None
        ):
            self.assertEqual(resolve_qoder_cli(), str(override.resolve()))

    def test_disabled_crew_is_unavailable_without_cli_probe(self) -> None:
        from agent_runtime.backends.errors import BackendUnavailableError
        from agent_runtime.backends.qoder.process import resolve_qoder_cli
        self._write_config(lambda p: p["crew"]["qoder"].update({"enabled": False}))
        with self._runtime_env(), patch("agent_runtime.backends.qoder.process.shutil.which") as which:
            with self.assertRaisesRegex(BackendUnavailableError, "disabled"):
                resolve_qoder_cli()
        which.assert_not_called()

    def test_codebuddy_environment_uses_env_override_then_config(self) -> None:
        from agent_runtime.backends.codebuddy import process as codebuddy_process
        self._write_config(
            lambda p: p["crew"]["codebuddy"].update({"internet_environment": "ioa"})
        )
        self.assertTrue(
            hasattr(codebuddy_process, "resolve_codebuddy_internet_environment"),
            "v1.0.7 needs one config-aware CodeBuddy environment resolver",
        )
        with self._runtime_env():
            self.assertEqual(codebuddy_process.resolve_codebuddy_internet_environment(), "ioa")
        with self._runtime_env(CODEBUDDY_INTERNET_ENVIRONMENT="public"):
            self.assertEqual(codebuddy_process.resolve_codebuddy_internet_environment(), "public")

    def test_trusted_instruction_and_worker_profile_roots_come_from_config(self) -> None:
        mcp_server = _import_mcp_server()
        instructions = self.root / "instructions"
        profiles = self.root / "profiles"
        skills = self.root / "skills"
        for path in (instructions, profiles, skills):
            path.mkdir()
        self._write_config(
            lambda p: (
                p["trusted_roots"]["instructions"].update({"ai_work": str(instructions.resolve())}),
                p["resources"].update(
                    {
                        "worker_profiles_root": str(profiles.resolve()),
                        "worker_skills_root": str(skills.resolve()),
                    }
                ),
            )
        )
        with self._runtime_env():
            self.assertEqual(mcp_server._trusted_instruction_roots(), {"ai_work": str(instructions.resolve())})
            self.assertEqual(mcp_server._worker_profile_resolver().root, profiles.resolve())
            self.assertTrue(hasattr(mcp_server, "_worker_skill_resolver"))

    def test_crew_concurrency_limits_are_independent_and_release_slots(self) -> None:
        import threading
        import time
        mcp_server = _import_mcp_server()

        def set_limits(payload):
            payload["crew"]["qoder"]["max_concurrent_tasks"] = 1
            payload["crew"]["codebuddy"]["max_concurrent_tasks"] = 1

        self._write_config(set_limits)
        database = self.root / "runtime" / "tp_voyager.db"
        database.parent.mkdir(parents=True)
        mcp_server.configure_runtime_database(database)
        release = threading.Event()

        def worker(task, timeout):
            del task, timeout
            release.wait(2)

        def kwargs(runtime: str):
            return dict(
                runtime=runtime,
                task_type=runtime,
                route="acp_read_only" if runtime == "qoder" else "sdk_context_read_only",
                resumable_routes=frozenset(
                    {"acp_read_only"} if runtime == "qoder" else {"sdk_context_read_only"}
                ),
                worker_target=worker,
                prompt=f"bounded-{runtime}",
                cwd=str(self.root),
                timeout_seconds=2,
                model="lite" if runtime == "qoder" else "hy3",
                idle_timeout_seconds=1,
                max_task_duration_seconds=2,
            )
        with self._runtime_env():
            qoder_first = mcp_server._durable_cli_start(**kwargs("qoder"))
            try:
                codebuddy_first = mcp_server._durable_cli_start(**kwargs("codebuddy"))
                qoder_second = mcp_server._durable_cli_start(**kwargs("qoder"))
                codebuddy_second = mcp_server._durable_cli_start(**kwargs("codebuddy"))
                self.assertTrue(qoder_first["ok"], qoder_first)
                self.assertTrue(codebuddy_first["ok"], codebuddy_first)
                self.assertFalse(qoder_second["ok"], qoder_second)
                self.assertEqual(qoder_second.get("reason_code"), "RUNTIME_BUSY")
                self.assertIn("qoder", qoder_second.get("error", ""))
                self.assertFalse(codebuddy_second["ok"], codebuddy_second)
                self.assertEqual(codebuddy_second.get("reason_code"), "RUNTIME_BUSY")
                self.assertIn("codebuddy", codebuddy_second.get("error", ""))
            finally:
                release.set()
            time.sleep(0.1)
            qoder_third = mcp_server._durable_cli_start(**kwargs("qoder"))
            codebuddy_third = mcp_server._durable_cli_start(**kwargs("codebuddy"))
            release.set()
        self.assertTrue(qoder_third["ok"], qoder_third)
        self.assertTrue(codebuddy_third["ok"], codebuddy_third)


class VoyagerCliV107Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.home = Path(self.tmp.name) / ".tp-voyager"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cli_prog_and_init_materialize_user_config_and_routing_profiles(self) -> None:
        from agent_runtime import cli
        self.assertEqual(cli._build_parser().prog, "tp-voyager")
        with patch.dict(os.environ, {"TP_VOYAGER_HOME": str(self.home)}, clear=False), patch(
            "shutil.which", return_value=None
        ):
            rc = cli.main(["init"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.home / "config.json").is_file())
        self.assertTrue((self.home / "model_routing_profiles.json").is_file())
        self.assertTrue((self.home / "runtime" / "artifacts").is_dir())
        self.assertTrue((self.home / "runtime" / "workspaces").is_dir())
        self.assertTrue((self.home / "runtime" / "logs").is_dir())

    def test_pyproject_exposes_tp_voyager_console_script_and_v107(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "1.0.9"', text)
        self.assertIn('tp-voyager = "agent_runtime.cli:main"', text)
        self.assertNotIn('agent-runtime = "agent_runtime.cli:main"', text)

    def test_public_launchers_and_captain_skill_use_v107_tp_voyager_names(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for script_name in ("start_runtime.cmd", "run_tests.cmd"):
            text = (root / "scripts" / script_name).read_text(encoding="utf-8")
            self.assertIn("TP_VOYAGER_PYTHON", text)
            self.assertNotIn("AGENT_RUNTIME_PYTHON", text)
        skill = (root / "skills" / "tp-voyager-captain" / "SKILL.md").read_text(encoding="utf-8")
        readme = (root / "skills" / "tp-voyager-captain" / "README.md").read_text(encoding="utf-8")
        desktop = (root / "skills" / "tp-voyager-captain" / "CODEX_DESKTOP.md").read_text(encoding="utf-8")
        self.assertIn('version: "1.0.9"', skill)
        self.assertIn("Captain Skill 1.0.9", readme)
        self.assertNotIn("$env:CODEBUDDY_CODE_PATH", desktop)
        self.assertNotIn("$env:QODER_CLI_PATH", desktop)
        self.assertIn("Crew CLI 路径不再属于 Codex MCP binding", desktop)
