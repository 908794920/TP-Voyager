from __future__ import annotations

import unittest

from agent_runtime.application.task_launch_service import TaskLaunchRequest, TaskLaunchService


class TaskLaunchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []

        def qoder(**kwargs):
            self.calls.append(("qoder", kwargs))
            return {"ok": True, "task_id": "qd-1", "runtime": "qoder"}

        def codebuddy(**kwargs):
            self.calls.append(("codebuddy", kwargs))
            return {"ok": True, "task_id": "cb-1", "runtime": "codebuddy"}

        self.service = TaskLaunchService({"qoder": qoder, "codebuddy": codebuddy})

    def test_runtime_must_be_explicit(self) -> None:
        result = self.service.start(TaskLaunchRequest(prompt="x"))
        self.assertFalse(result["ok"])
        self.assertIn("runtime must be explicit", result["error"])
        self.assertEqual(self.calls, [])

    def test_qoder_routes_explicit_fields_and_rejects_legacy_review_fields(self) -> None:
        result = self.service.start(
            TaskLaunchRequest(
                prompt="review",
                runtime="qoder",
                route="acp_read_only",
                model="qoder-model",
                reasoning_effort="high",
                context_window_tokens=200000,
                agent_profile="review",
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.calls[0][0], "qoder")
        kwargs = self.calls[0][1]
        self.assertEqual(kwargs["route"], "acp_read_only")
        self.assertEqual(kwargs["model"], "qoder-model")
        self.assertEqual(kwargs["reasoning_effort"], "high")
        self.assertEqual(kwargs["context_window_tokens"], 200000)
        self.assertEqual(kwargs["agent_profile"], "review")

        self.calls.clear()
        invalid = self.service.start(
            TaskLaunchRequest(prompt="review", runtime="qoder", identity="legacy")
        )
        self.assertFalse(invalid["ok"])
        self.assertIn("legacy review/session fields", invalid["error"])
        self.assertEqual(self.calls, [])

    def test_codebuddy_defaults_to_acp_and_keeps_explicit_sdk_compatibility(self) -> None:
        result = self.service.start(
            TaskLaunchRequest(
                prompt="analyze bounded context",
                runtime="codebuddy",
                model="hy3",
                agent_profile="research",
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.calls[0][0], "codebuddy")
        kwargs = self.calls[0][1]
        self.assertEqual(kwargs["route"], "acp_read_only")
        self.assertEqual(kwargs["model"], "hy3")
        self.assertEqual(kwargs["agent_profile"], "research")

        self.calls.clear()
        sdk = self.service.start(
            TaskLaunchRequest(prompt="compat", runtime="codebuddy", route="sdk_context_read_only")
        )
        self.assertTrue(sdk["ok"], sdk)
        self.assertEqual(self.calls[0][1]["route"], "sdk_context_read_only")

        self.calls.clear()
        invalid = self.service.start(
            TaskLaunchRequest(prompt="x", runtime="codebuddy", route="headless")
        )
        self.assertFalse(invalid["ok"])
        self.assertEqual(self.calls, [])

    def test_unknown_runtime_fails_closed_without_calling_any_launcher(self) -> None:
        result = self.service.start(TaskLaunchRequest(prompt="x", runtime="future-backend"))
        self.assertFalse(result["ok"])
        self.assertIn("Unsupported sub-agent runtime", result["error"])
        self.assertEqual(self.calls, [])

    def test_registered_unknown_runtime_is_not_implicitly_supported(self) -> None:
        called = []
        service = TaskLaunchService({"future": lambda **kwargs: called.append(kwargs)})
        result = service.start(TaskLaunchRequest(prompt="x", runtime="future"))
        self.assertFalse(result["ok"])
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()


def test_mcp_server_codebuddy_route_contract_includes_native_acp_and_sdk_compatibility():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "agent_runtime" / "api" / "mcp_server.py").read_text(encoding="utf-8")
    assert 'route: str = "acp_read_only"' in source
    assert '"acp_read_only", "acp_patch", "acp_verify"' in source
    assert '"sdk_context_read_only", "sdk_patch", "sdk_verify"' in source
