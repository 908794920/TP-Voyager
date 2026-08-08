from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime import server
from agent_runtime.application.plan_execution_service import PlanStepMaterial


class _FakePlanExecutionService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def start(self, plan_id, *, cwd, step_materials, execution_id=None):
        self.calls.append(("start", (plan_id, cwd, step_materials, execution_id)))
        return {"ok": True, "execution_id": execution_id or "pex-api", "status": "running"}

    def pump(self, execution_id):
        self.calls.append(("pump", execution_id))
        return {"ok": True, "execution_id": execution_id, "status": "running"}

    def status(self, execution_id):
        self.calls.append(("status", execution_id))
        return {"ok": True, "execution_id": execution_id, "status": "running"}

    def resume(self, execution_id, *, cwd, step_materials):
        self.calls.append(("resume", (execution_id, cwd, step_materials)))
        return {"ok": True, "execution_id": execution_id, "status": "running"}

    def cancel(self, execution_id, *, cancel_current_task=False):
        self.calls.append(("cancel", (execution_id, cancel_current_task)))
        return {"ok": True, "execution_id": execution_id, "status": "cancelled"}

    def result(self, execution_id):
        self.calls.append(("result", execution_id))
        return {"ok": True, "execution_id": execution_id, "final": False}

    def history(self, execution_id, *, limit=100):
        self.calls.append(("history", (execution_id, limit)))
        return {"ok": True, "execution_id": execution_id, "events": []}


class PlanExecutionServerApiV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        server.configure_runtime_database(self.root / "runtime.db")
        self.fake = _FakePlanExecutionService()
        server._PLAN_EXECUTION_SERVICE = self.fake

    def tearDown(self) -> None:
        server._PLAN_EXECUTION_SERVICE = None
        server.configure_runtime_database(None)
        self.tmp.cleanup()

    @staticmethod
    def _material() -> dict[str, object]:
        return {
            "step_key": "analyze",
            "prompt": "private prompt",
            "runtime": "qoder",
            "route": "acp_read_only",
            "execution_mode": "background",
            "allowed_paths": ["src"],
            "verification_commands": ["python -m unittest tests.test_target"],
            "max_changed_files": 2,
            "verification_timeout_seconds": 120,
            "require_patch": True,
            "timeout_seconds": 300,
            "idle_timeout_seconds": 180,
            "max_task_duration_seconds": 600,
        }

    def test_all_plan_execution_public_tools_delegate_through_controller(self) -> None:
        started = server.plan_execution_start(
            "pln-api",
            str(self.root),
            [self._material()],
            execution_id="pex-api",
        )
        self.assertTrue(started["ok"], started)
        call = self.fake.calls[-1]
        self.assertEqual(call[0], "start")
        material = call[1][2][0]
        self.assertIsInstance(material, PlanStepMaterial)
        self.assertEqual(material.step_key, "analyze")
        self.assertTrue(material.require_patch)
        self.assertEqual(material.max_task_duration_seconds, 600)

        self.assertTrue(server.plan_execution_status("pex-api", refresh=True)["ok"])
        self.assertEqual(self.fake.calls[-1], ("pump", "pex-api"))
        self.assertTrue(server.plan_execution_status("pex-api", refresh=False)["ok"])
        self.assertEqual(self.fake.calls[-1], ("status", "pex-api"))

        resumed = server.plan_execution_resume(
            "pex-api", str(self.root), [self._material()]
        )
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(self.fake.calls[-1][0], "resume")

        cancelled = server.plan_execution_cancel(
            "pex-api", cancel_current_task=True
        )
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertEqual(self.fake.calls[-1], ("cancel", ("pex-api", True)))

        self.assertFalse(server.plan_execution_result("pex-api")["final"])
        self.assertEqual(self.fake.calls[-1], ("result", "pex-api"))
        self.assertEqual(server.plan_execution_history("pex-api", limit=7)["events"], [])
        self.assertEqual(self.fake.calls[-1], ("history", ("pex-api", 7)))

    def test_nested_material_types_fail_closed_instead_of_coercing(self) -> None:
        bad_cases = [
            {**self._material(), "require_patch": "false"},
            {**self._material(), "timeout_seconds": "300"},
            {**self._material(), "prompt": {"unexpected": "object"}},
            {**self._material(), "allowed_paths": "src"},
        ]
        before = len(self.fake.calls)
        for material in bad_cases:
            response = server.plan_execution_start(
                "pln-api", str(self.root), [material]
            )
            self.assertFalse(response["ok"], response)
        self.assertEqual(len(self.fake.calls), before)


if __name__ == "__main__":
    unittest.main()
