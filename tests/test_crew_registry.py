from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime.application.crew import CrewProvider, CrewRegistryService
from agent_runtime.domain.crew import CrewDescriptor, ModelDescriptor
from agent_runtime.domain.task import Task
from agent_runtime.backends.qoder.model_catalog import parse_list_models_output, parse_sdk_models_output, list_qoder_models
from agent_runtime.backends.codebuddy.model_catalog import parse_codebuddy_help_models
from agent_runtime.backends.qoder.capability import descriptor as qoder_crew_descriptor
from agent_runtime.backends.codebuddy.capability import descriptor as codebuddy_crew_descriptor


class _Tasks:
    def __init__(self, tasks, sessions=None, usage=None):
        self._tasks = tasks
        self._sessions = dict(sessions or {})
        self._usage = dict(usage or {})

    def list_tasks(self):
        return list(self._tasks)

    def get_session(self, task_id):
        return self._sessions.get(task_id)

    def latest_usage_evidence(self, task_id):
        return dict(self._usage.get(task_id) or {})


def _descriptor(name: str, *, ready: bool = True, caps=()) -> CrewDescriptor:
    return CrewDescriptor(
        backend=name,
        display_name=name.title(),
        maturity="official",
        official_sources=(f"https://example.invalid/{name}",),
        capabilities=tuple(caps),
        controlled_capabilities=tuple(caps) if ready else (),
        documented_routes=("sdk",),
        implemented_routes=("sdk",) if ready else (),
        dispatch_ready=ready,
    )


class CrewRegistryTests(unittest.TestCase):
    def test_catalog_is_content_free_and_does_not_select_or_dispatch(self) -> None:
        service = CrewRegistryService(
            {
                "codebuddy": CrewProvider(_descriptor("codebuddy", ready=False, caps=("read_files", "search_code"))),
                "qoder": CrewProvider(_descriptor("qoder", caps=("read_files", "search_code"))),
            }
        )
        result = service.catalog()
        self.assertEqual(result["schema"], "tp-voyager.crew_catalog/v1")
        self.assertFalse(result["selection_performed"])
        self.assertFalse(result["dispatch_performed"])
        self.assertEqual([item["backend"] for item in result["crew"]], ["codebuddy", "qoder"])

    def test_health_projects_existing_durable_task_history_without_new_state(self) -> None:
        tasks = [
            Task("a", "qoder", "completed", "acp", 1, 5, started_at=2, finished_at=5),
            Task("b", "qoder", "failed", "acp", 6, 10, started_at=7, finished_at=10),
            Task("c", "qoder", "cancelled", "acp", 11, 12, started_at=11, finished_at=12),
        ]
        service = CrewRegistryService(
            {"qoder": CrewProvider(_descriptor("qoder"), probe=lambda: {"installed": True, "version": "1.2.3"})},
            task_service=_Tasks(tasks),
        )
        health = service.health("qoder", probe=True)
        self.assertEqual(health.sample_count, 2)  # user-cancelled work does not penalize worker health
        self.assertEqual(health.success_rate, 0.5)
        self.assertEqual(health.failure_streak, 1)
        self.assertEqual(health.average_duration_seconds, 3.0)
        self.assertEqual(health.version, "1.2.3")
        self.assertEqual(health.availability, "available")

    def test_recommendation_is_advisory_and_filters_non_ready_crew(self) -> None:
        caps = ("analyze_context", "read_files", "search_code", "edit_files", "run_commands")
        service = CrewRegistryService(
            {
                "codebuddy": CrewProvider(_descriptor("codebuddy", ready=False, caps=caps)),
                "qoder": CrewProvider(_descriptor("qoder", ready=True, caps=caps)),
            }
        )
        result = service.recommend("small_patch")
        self.assertFalse(result["selection_performed"])
        self.assertFalse(result["dispatch_performed"])
        by_name = {item["backend"]: item for item in result["recommendations"]}
        self.assertTrue(by_name["qoder"]["compatible"])
        self.assertFalse(by_name["codebuddy"]["compatible"])

    def test_probe_errors_are_classified_without_leaking_message_content(self) -> None:
        def fail():
            raise RuntimeError("secret local path")

        service = CrewRegistryService({"qoder": CrewProvider(_descriptor("qoder"), probe=fail)})
        health = service.health("qoder", probe=True)
        self.assertEqual(health.availability, "unavailable")
        self.assertEqual(health.probe_error, "RuntimeError")
        self.assertNotIn("secret", str(health.to_dict()))


    def test_health_surfaces_auth_probe_state_and_last_successful_explicit_model(self) -> None:
        tasks = [
            Task("old", "codebuddy", "completed", "sdk_context_read_only", 1, 5, started_at=2, finished_at=5),
            Task("new", "codebuddy", "completed", "sdk_context_read_only", 6, 12, started_at=7, finished_at=12),
        ]
        sessions = {
            "old": SimpleNamespace(metadata_json=json.dumps({"model": "older-model"})),
            "new": SimpleNamespace(metadata_json=json.dumps({"model": "hy3"})),
        }
        service = CrewRegistryService(
            {
                "codebuddy": CrewProvider(
                    codebuddy_crew_descriptor(),
                    probe=lambda: {
                        "installed": True,
                        "version": "1.0",
                        "cli_installed": True,
                        "sdk_installed": True,
                        "auth_probe_performed": False,
                    },
                )
            },
            task_service=_Tasks(tasks, sessions),
        )
        health = service.health("codebuddy", probe=True)
        self.assertEqual(health.auth_status, "not_probed")
        self.assertEqual(health.last_successful_model, "hy3")
        self.assertEqual(health.last_successful_model_at, 12)
        self.assertEqual(health.last_successful_model_source, "runtime_observation")
        self.assertEqual(health.model_catalog_status, "cli_declared")
        self.assertTrue(health.detail["cli_installed"])
        self.assertTrue(health.detail["sdk_installed"])

    def test_models_are_optional_and_unknown_is_not_guessed(self) -> None:
        service = CrewRegistryService({"codebuddy": CrewProvider(_descriptor("codebuddy", ready=False))})
        self.assertEqual(service.models("codebuddy"), [])

    def test_qoder_official_list_models_table_parser_is_bounded_and_deduplicated(self) -> None:
        text = """Model  Display Name  Credit\nauto   Smart Routing  1.0x\nefficient  Efficient  0.3x\nauto   Duplicate  1.0x\n"""
        models = parse_list_models_output(text, observed_at=123.0)
        self.assertEqual([item.model_id for item in models], ["auto", "efficient"])
        self.assertTrue(all(item.source == "official_dynamic" for item in models))
        self.assertTrue(all(item.observed_at == 123.0 for item in models))


    def test_qoder_single_row_pipe_capture_is_explicitly_incomplete(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="MODEL\nQwen3.8-Max\n", stderr="")
        with patch("agent_runtime.backends.qoder.model_catalog.resolve_qoder_cli", return_value="qodercli"), patch(
            "agent_runtime.backends.qoder.model_catalog._list_qoder_models_via_sdk", return_value=[]
        ), patch("agent_runtime.backends.qoder.model_catalog.subprocess.run", return_value=completed):
            models = list_qoder_models()
        self.assertEqual([item.model_id for item in models], ["Qwen3.8-Max"])
        self.assertEqual(models[0].metadata["catalog_status"], "incomplete_suspected")

    def test_qoder_sdk_catalog_projects_live_entitlement_and_price_factor_without_cost_calculation(self) -> None:
        payload = [
            {
                "value": "deepseek-v4-flash",
                "displayName": "DeepSeek V4 Flash",
                "description": "Fast coding model",
                "isEnabled": True,
                "isFree": False,
                "priceFactor": 0.1,
                "context_config": {"200K": {"token_count": 200000, "is_default": True}},
                "promotion": {"active": True, "discount_factor": 0.5, "rule_id": "do-not-project"},
            }
        ]
        text = "TP_VOYAGER_QODER_MODELS=" + json.dumps(payload)
        models = parse_sdk_models_output(text, observed_at=9.0)
        self.assertEqual(len(models), 1)
        model = models[0]
        self.assertEqual(model.source, "official_dynamic_sdk")
        self.assertTrue(model.available)
        self.assertEqual(model.metadata["entitlement_status"], "enabled")
        self.assertEqual(model.metadata["billing"]["status"], "provider_live_reference")
        self.assertEqual(model.metadata["billing"]["price_factor"], 0.1)
        self.assertFalse(model.metadata["billing"]["calculation_allowed"])
        self.assertEqual(model.metadata["promotion"]["discount_factor"], 0.5)
        self.assertNotIn("rule_id", model.metadata["promotion"])

    def test_qoder_sdk_catalog_is_preferred_over_cli_fallback(self) -> None:
        sdk_model = ModelDescriptor(
            "qoder", "Lite", source="official_dynamic_sdk", available=True,
            metadata={"catalog_status": "complete", "billing": {"status": "provider_live_reference", "calculation_allowed": False}},
        )
        with patch("agent_runtime.backends.qoder.model_catalog.resolve_qoder_cli", return_value="qodercli"), patch(
            "agent_runtime.backends.qoder.model_catalog._list_qoder_models_via_sdk", return_value=[sdk_model]
        ), patch("agent_runtime.backends.qoder.model_catalog.subprocess.run") as cli_run:
            models = list_qoder_models()
        self.assertEqual([m.model_id for m in models], ["Lite"])
        self.assertEqual(models[0].source, "official_dynamic_sdk")
        self.assertEqual(models[0].metadata["billing"]["status"], "provider_live_reference")
        cli_run.assert_not_called()

    def test_qoder_tier_billing_is_reference_only_never_calculation_input(self) -> None:
        models = parse_list_models_output("MODEL\nLite\nUltimate\n", observed_at=1.0)
        by_id = {item.model_id: item for item in models}
        self.assertEqual(by_id["Lite"].metadata["billing"]["status"], "official_reference")
        self.assertFalse(by_id["Lite"].metadata["billing"]["calculation_allowed"])
        self.assertEqual(by_id["Lite"].metadata["billing"]["credit_rate"], "free")
        self.assertEqual(by_id["Ultimate"].metadata["capabilities"]["status"], "official_tier_intent")
        direct = parse_list_models_output("MODEL\nDeepSeek-V4-Flash\n", observed_at=1.0)[0]
        self.assertEqual(direct.metadata["capabilities"]["status"], "official_descriptive_tags")
        self.assertFalse(direct.metadata["capabilities"]["scored"])

    def test_research_requires_normalized_context_analysis_not_vendor_file_tools(self) -> None:
        self.assertEqual(
            CrewRegistryService.required_capabilities("research"),
            ("analyze_context",),
        )

    def test_codebuddy_cli_declared_model_parser_is_bounded_and_not_entitlement_claim(self) -> None:
        text = "--model <model> Model for session. Currently supported: (hy3, glm-5.2, deepseek-v4-flash)"
        models = parse_codebuddy_help_models(text, observed_at=55.0)
        self.assertEqual([item.model_id for item in models], ["hy3", "glm-5.2", "deepseek-v4-flash"])
        self.assertTrue(all(item.source == "cli_declared" for item in models))
        self.assertTrue(all(item.available is None for item in models))
        self.assertTrue(all(item.metadata["entitlement_status"] == "unknown" for item in models))

    def test_model_catalog_projects_history_and_usage_without_estimating_price(self) -> None:
        task = Task("m1", "qoder", "completed", "acp_read_only", 1, 5, started_at=2, finished_at=5)
        sessions = {"m1": SimpleNamespace(metadata_json=json.dumps({"model": "Lite"}))}
        usage = {
            "m1": {
                "schema": "tp-voyager.usage/v1", "provider": "qoder", "model": "Lite",
                "usage": {"input_tokens": 100, "output_tokens": 20, "credits_used": 0, "reported_cost": None, "currency": None},
            }
        }
        service = CrewRegistryService(
            {"qoder": CrewProvider(_descriptor("qoder"), models=lambda: [ModelDescriptor("qoder", "Lite", source="official_dynamic", metadata={"catalog_status": "complete"})])},
            task_service=_Tasks([task], sessions, usage),
        )
        snapshot = service.model_catalog("qoder")
        self.assertEqual(snapshot["catalog"]["status"], "complete")
        lite = snapshot["models"][0]
        self.assertEqual(lite["history"]["success_rate"], 1.0)
        self.assertEqual(lite["usage"]["average_input_tokens"], 100.0)
        self.assertEqual(lite["usage"]["average_credits_used"], 0.0)
        self.assertFalse(lite["usage"]["pricing_estimated"])

    def test_target_crew_readiness_reflects_controlled_not_marketing_capability(self) -> None:
        qoder = qoder_crew_descriptor()
        codebuddy = codebuddy_crew_descriptor()
        self.assertTrue(qoder.dispatch_ready)
        self.assertIn("analyze_context", qoder.controlled_capabilities)
        self.assertIn("read_files", qoder.controlled_capabilities)
        self.assertIn("edit_files", qoder.controlled_capabilities)
        self.assertIn("run_commands", qoder.controlled_capabilities)
        self.assertTrue(codebuddy.dispatch_ready)
        self.assertIn("analyze_context", codebuddy.controlled_capabilities)
        self.assertIn("edit_files", codebuddy.controlled_capabilities)
        self.assertIn("run_commands", codebuddy.controlled_capabilities)



if __name__ == "__main__":
    unittest.main()
