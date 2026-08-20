from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.api.mcp_server import _mcp_surface
from agent_runtime.api.schemas import CAPTAIN_TOOL_NAMES
from agent_runtime.cli import _doctor_projection


class McpCaptainContractTests(unittest.TestCase):
    expected = frozenset({
        "voyager_overview", "render_voyager_panel", "crew_catalog", "crew_health", "crew_recommend", "task_dispatch", "task_result",
    })

    def test_default_surface_is_exact_golden_seven(self) -> None:
        self.assertEqual(CAPTAIN_TOOL_NAMES, self.expected)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_mcp_surface(), "captain")

    def test_diagnostic_surface_is_explicit(self) -> None:
        with patch.dict(os.environ, {"TP_VOYAGER_MCP_SURFACE": "diagnostic"}, clear=False):
            self.assertEqual(_mcp_surface(), "diagnostic")

    def test_docs_and_canonical_plugin_skill_repeat_the_same_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        canonical_skill = (
            root
            / "skills"
            / "tp-voyager-captain"
            / "integrations"
            / "codex"
            / "local-marketplace"
            / "plugins"
            / "tp-voyager"
            / "skills"
            / "captain"
            / "SKILL.md"
        )
        for path in (root / "README.md", canonical_skill):
            text = path.read_text(encoding="utf-8")
            for name in self.expected:
                self.assertIn(name, text, path)

        legacy_shim = (root / "skills" / "tp-voyager-captain" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("legacy migration shim", legacy_shim.lower())
        self.assertNotIn("## 3. Captain Responsibilities", legacy_shim)

    def test_doctor_derives_same_exact_contract(self) -> None:
        with patch("agent_runtime.cli.probe_codebuddy_cli", return_value={"installed":True}), patch(
            "agent_runtime.cli.probe_qoder_cli", return_value={"installed":True}
        ), patch("agent_runtime.cli.list_codebuddy_models", return_value=[]), patch(
            "agent_runtime.cli.list_qoder_models", return_value=[]
        ):
            doctor=_doctor_projection({"schema_supported":True, "integrity_ok":True})
        self.assertEqual(set(doctor["captain_tools"]["required"]), self.expected)
        self.assertEqual(set(doctor["captain_tools"]["declared"]), self.expected)

if __name__ == "__main__":
    unittest.main()
