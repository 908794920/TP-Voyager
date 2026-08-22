from __future__ import annotations

import inspect
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.api import mcp_server
from agent_runtime.api.mcp_server import _mcp_surface
from agent_runtime.api.schemas import CAPTAIN_TOOL_NAMES
from agent_runtime.cli import _doctor_projection
from agent_runtime.configuration.user_config import VoyagerUserConfig


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

    def test_overview_can_return_runtime_profile_without_adding_a_captain_tool(self) -> None:
        self.assertEqual(CAPTAIN_TOOL_NAMES, self.expected)
        self.assertIn("include_profile", inspect.signature(mcp_server.voyager_overview).parameters)
        projector = getattr(mcp_server, "_runtime_profile_projection", None)
        self.assertTrue(callable(projector))

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".tp-voyager"
            home.mkdir()
            (home / "config.json").write_text(
                json.dumps({
                    "schema": "tp-voyager.config/v2",
                    "crew": {
                        "qoder": {
                            "enabled": True,
                            "cli_path": str(home / "bin" / "qodercli.exe"),
                            "max_concurrent_tasks": 2,
                        },
                        "codebuddy": {
                            "enabled": True,
                            "cli_path": str(home / "bin" / "codebuddy.exe"),
                            "internet_environment": "internal",
                            "max_concurrent_tasks": 2,
                        },
                    },
                    "dispatch": {
                        "allowed_models": ["qoder:qmodel_38max", "codebuddy:deepseek-v4-flash"],
                        "preferred_models": ["qoder:qmodel_38max"],
                        "task_kind_allowed_models": {},
                    },
                    "trusted_roots": {"model_evidence": {}, "instructions": {}},
                    "resources": {"worker_profiles_root": "", "worker_skills_root": ""},
                }),
                encoding="utf-8",
            )
            config = VoyagerUserConfig.load(home)
            catalog = {
                "crew": [{
                    "backend": "qoder",
                    "display_name": "Qoder CLI",
                    "dispatch_ready": True,
                    "health": {
                        "availability": "available",
                        "version": "1.1.17",
                        "auth_status": "not_probed",
                        "last_successful_model": "qmodel_38max",
                    },
                    "model_catalog": {"status": "complete", "source": "official_dynamic_sdk"},
                    "models": [{
                        "model_id": "qmodel_38max",
                        "display_name": "Qwen3.8-Max",
                        "available": True,
                        "routable": True,
                        "routability_status": "confirmed",
                        "reference_multiplier": 0.5,
                        "metadata": {
                            "isFree": True,
                            "billing": {"price_factor": 0.5},
                        },
                        "reasoning": {"supported_efforts": ["low", "medium"]},
                        "context_window_tokens": 1000000,
                    }],
                }],
                "updated_at": 123.0,
            }

            class Registry:
                def __init__(self):
                    self.calls = []

                def catalog(self, *, probe: bool, include_models: bool):
                    self.calls.append((probe, include_models))
                    return catalog

            registry = Registry()
            with patch.object(mcp_server.VoyagerUserConfig, "load", return_value=config), patch.object(
                mcp_server, "_crew_registry_service", return_value=registry
            ), patch.object(
                mcp_server,
                "collect_qoder_account_snapshot",
                return_value={
                    "status": "observed",
                    "auth_status": "verified",
                    "user_type": "personal_professional_trial",
                    "is_quota_exceeded": False,
                    "user_quota": {
                        "total": 300.0,
                        "used": 272.0,
                        "remaining": 28.0,
                        "unit": "credits",
                    },
                },
            ):
                result = mcp_server.voyager_overview(include_profile=True)
                refreshed = mcp_server.voyager_overview(
                    include_profile=True, refresh_profile=True
                )

        self.assertTrue(result["ok"])
        profile = result["runtime_profile"]
        self.assertEqual(profile["schema"], "tp-voyager.runtime_profile/v1")
        self.assertEqual(profile["config"]["home"], "~/.tp-voyager")
        self.assertEqual(profile["config"]["crew"]["qoder"]["cli_path"], "~/.tp-voyager/bin/qodercli.exe")
        self.assertEqual(profile["models"], [])
        self.assertEqual(profile["accounts"][0]["quota_status"], "not_observed")
        self.assertEqual(registry.calls, [(False, False), (True, True)])
        refreshed_profile = refreshed["runtime_profile"]
        self.assertEqual(refreshed_profile["models"][0]["model_id"], "qmodel_38max")
        self.assertTrue(refreshed_profile["models"][0]["routable"])
        self.assertEqual(refreshed_profile["models"][0]["reference_multiplier"], 0.5)
        account = refreshed_profile["accounts"][0]
        self.assertEqual(account["auth_status"], "verified")
        self.assertEqual(account["quota_status"], "observed")
        self.assertEqual(account["quota_summary"], "总 300 · 已用 272 · 剩余 28 credits")

if __name__ == "__main__":
    unittest.main()
