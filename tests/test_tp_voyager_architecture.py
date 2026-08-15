from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG = REPO_ROOT / "agent_runtime"


class TPVoyagerArchitectureBaselineTests(unittest.TestCase):
    def test_governance_documents_and_scripts_are_present(self) -> None:
        self.assertTrue((REPO_ROOT / "docs" / "architecture" / "CHARTER.md").is_file())
        self.assertTrue((REPO_ROOT / "docs" / "architecture" / "DIRECTORY_BASELINE.md").is_file())
        self.assertTrue((REPO_ROOT / "scripts" / "start_runtime.cmd").is_file())
        self.assertTrue((REPO_ROOT / "scripts" / "run_tests.cmd").is_file())

    def test_current_product_docs_define_passenger_captain_and_two_crew_families(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for text in ("TP-Voyager", "Captain", "CodeBuddy CLI", "Qoder CLI"):
            self.assertIn(text, readme)
        self.assertTrue("Passenger" in readme or "乘客" in readme)
        self.assertIn("docs/architecture/CHARTER.md", agents)
        self.assertIn("docs/architecture/DIRECTORY_BASELINE.md", agents)
        self.assertIn("CodeBuddy", agents)
        self.assertIn("Qoder", agents)
        self.assertIn("WorkBuddy", agents)  # only as an explicit do-not-reintroduce boundary

    def test_current_backend_docs_are_official_codebuddy_and_qoder_only(self) -> None:
        current = {path.name for path in (REPO_ROOT / "docs").glob("*.md")}
        self.assertEqual(
            current,
            {"README.md", "ARCHITECTURE.md", "MODEL_ROUTING.md", "MODEL_EVALUATION_STANDARD.md",
             "BACKEND_CODEBUDDY.md", "BACKEND_QODER.md", "TESTING.md", "OPERATIONS.md"},
        )
        codebuddy_doc = (REPO_ROOT / "docs" / "BACKEND_CODEBUDDY.md").read_text(encoding="utf-8")
        qoder_doc = (REPO_ROOT / "docs" / "BACKEND_QODER.md").read_text(encoding="utf-8")
        self.assertIn("https://www.workbuddy.ai/docs/cli/", codebuddy_doc)
        self.assertIn("https://docs.qoder.com/en/cli/acp", qoder_doc)

    def test_retired_workbuddy_execution_files_are_physically_absent(self) -> None:
        for rel in (
            "agent_runtime/backends/workbuddy",
            "agent_runtime/acp.py",
            "agent_runtime/multiplexer.py",
            "agent_runtime/history.py",
            "agent_runtime/review_sessions.py",
            "agent_runtime/identities.py",
            "skills/workbuddy-agent-routing",
            "start_bridge.cmd",
        ):
            self.assertFalse((REPO_ROOT / rel).exists(), rel)

    def test_public_server_has_no_workbuddy_execution_tools_or_registration(self) -> None:
        source = (PKG / "api" / "mcp_server.py").read_text(encoding="utf-8")
        for name in (
            "workbuddy_start", "workbuddy_status", "workbuddy_wait", "workbuddy_result",
            "workbuddy_cancel", "workbuddy_list", "workbuddy_models",
        ):
            self.assertNotIn(f"def {name}(", source)
        self.assertNotIn('_BACKENDS.register("workbuddy"', source)
        self.assertIn('_BACKENDS.register("codebuddy"', source)
        self.assertIn('_BACKENDS.register("qoder"', source)

    def test_agent_runtime_top_level_boundary_remains_stable(self) -> None:
        for name in ("api", "configuration", "application", "domain", "persistence", "verification", "backends", "runtime", "testing"):
            self.assertTrue((PKG / name).is_dir(), name)
        baseline = (REPO_ROOT / "docs" / "architecture" / "DIRECTORY_BASELINE.md").read_text(encoding="utf-8")
        self.assertIn("├── configuration/", baseline)
        for name in ("core", "platform", "services", "managers", "engine"):
            self.assertFalse((PKG / name).exists(), name)

    def test_product_rename_does_not_rename_python_package(self) -> None:
        self.assertTrue(PKG.is_dir())
        for name in ("tp_voyager", "voyager", "workbuddy_bridge"):
            self.assertFalse((REPO_ROOT / name).exists())

    def test_server_remains_thin_compatibility_entry(self) -> None:
        source = (PKG / "server.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 40)
        self.assertIn("agent_runtime.api", source)
        self.assertNotIn("FastMCP(", source)
        self.assertNotIn("@mcp.tool", source)

    def test_captain_normal_api_stays_compact_and_vendor_neutral(self) -> None:
        source = (PKG / "api" / "mcp_server.py").read_text(encoding="utf-8")
        for name in (
            "crew_catalog", "crew_health", "crew_recommend",
            "voyager_overview", "task_dispatch", "task_result",
        ):
            self.assertIn(f"def {name}(", source)
        dispatch = (PKG / "application" / "dispatch" / "service.py").read_text(encoding="utf-8")
        self.assertIn('crew == "workbuddy"', dispatch)  # explicit fail-closed rejection only
        self.assertIn("CREW_NOT_SUPPORTED", dispatch)
        self.assertNotIn("--yolo", dispatch)


    def test_default_mcp_surface_registers_only_six_captain_tools(self) -> None:
        expected = {
            "crew_catalog", "crew_health", "crew_recommend",
            "voyager_overview", "task_dispatch", "task_result",
        }
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp)
            (stub / "mcp" / "server").mkdir(parents=True)
            (stub / "mcp" / "__init__.py").write_text("", encoding="utf-8")
            (stub / "mcp" / "server" / "__init__.py").write_text("", encoding="utf-8")
            (stub / "mcp" / "server" / "fastmcp.py").write_text(
                "class FastMCP:\n"
                "    def __init__(self, *args, **kwargs): self.registered_tools=[]\n"
                "    def tool(self, *args, **kwargs):\n"
                "        def deco(fn): self.registered_tools.append(fn.__name__); return fn\n"
                "        return deco\n"
                "    def run(self, *args, **kwargs): pass\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.pop("TP_VOYAGER_MCP_SURFACE", None)
            env["PYTHONPATH"] = os.pathsep.join([str(stub), str(REPO_ROOT)])
            completed = subprocess.run(
                [sys.executable, "-c", "import json; import agent_runtime.api.mcp_server as m; print(json.dumps(m.mcp.registered_tools))"],
                cwd=str(REPO_ROOT), env=env, text=True, capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(set(json.loads(completed.stdout.strip().splitlines()[-1])), expected)

    def test_diagnostic_mcp_surface_keeps_compatibility_tools_without_becoming_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp)
            (stub / "mcp" / "server").mkdir(parents=True)
            (stub / "mcp" / "__init__.py").write_text("", encoding="utf-8")
            (stub / "mcp" / "server" / "__init__.py").write_text("", encoding="utf-8")
            (stub / "mcp" / "server" / "fastmcp.py").write_text(
                "class FastMCP:\n"
                "    def __init__(self, *args, **kwargs): self.registered_tools=[]\n"
                "    def tool(self, *args, **kwargs):\n"
                "        def deco(fn): self.registered_tools.append(fn.__name__); return fn\n"
                "        return deco\n"
                "    def run(self, *args, **kwargs): pass\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["TP_VOYAGER_MCP_SURFACE"] = "diagnostic"
            env["PYTHONPATH"] = os.pathsep.join([str(stub), str(REPO_ROOT)])
            completed = subprocess.run(
                [sys.executable, "-c", "import json; import agent_runtime.api.mcp_server as m; print(json.dumps(m.mcp.registered_tools))"],
                cwd=str(REPO_ROOT), env=env, text=True, capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            tools = set(json.loads(completed.stdout.strip().splitlines()[-1]))
            self.assertGreater(len(tools), 6)
            self.assertIn("subagent_status", tools)
            self.assertIn("context_register", tools)

    def test_removed_legacy_workbuddy_docs_do_not_reappear_as_current_product_surface(self) -> None:
        # HEAD a4a938a intentionally retired the legacy-workbuddy record tree.
        # Current product docs may mention WorkBuddy only as a historical /
        # fail-closed boundary, never as an executable Crew or current backend.
        self.assertFalse((REPO_ROOT / "docs" / "records" / "legacy-workbuddy").exists())
        current_docs = "\n".join(
            path.read_text(encoding="utf-8") for path in (REPO_ROOT / "docs").glob("*.md")
        )
        self.assertNotIn("workbuddy_start", current_docs)
        self.assertNotIn("workbuddy_models", current_docs)


if __name__ == "__main__":
    unittest.main()
