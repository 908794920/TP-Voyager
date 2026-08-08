from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime import server


class PlannerServerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        server.configure_runtime_database(self.root / "runtime.db")

    def tearDown(self) -> None:
        server.configure_runtime_database(None)
        self.tmp.cleanup()

    def test_create_validate_prepare_status_list_history(self) -> None:
        created = server.planner_create(
            name="API plan",
            requirement="Analyze and implement one bounded change",
            acceptance_criteria=["verified"],
            runtime="qoder",
            agent_profile="S2",
        )
        self.assertTrue(created["ok"], created)
        plan_id = created["plan"]["plan_id"]
        self.assertFalse(created["dispatch_performed"])
        self.assertTrue(server.planner_validate(plan_id)["ok"])
        prepared = server.planner_prepare(plan_id)
        self.assertTrue(prepared["ok"], prepared)
        self.assertFalse(prepared["execution_spec"]["selection_performed"])
        self.assertEqual(server.planner_status(plan_id)["plan"]["status"], "prepared")
        self.assertEqual(server.planner_list(status="prepared")["plans"][0]["plan_id"], plan_id)
        self.assertEqual(len(server.planner_history(plan_id)["events"]), 3)

    def test_invalid_inputs_are_returned_as_explicit_errors(self) -> None:
        bad = server.planner_create(
            name="bad",
            requirement="x",
            task_kind="unknown",
        )
        self.assertFalse(bad["ok"])
        self.assertFalse(server.planner_prepare("pln-missing")["ok"])
        strict = server.planner_create(
            name="strict",
            requirement="x",
            require_approval="false",  # type: ignore[arg-type]
        )
        self.assertFalse(strict["ok"])


if __name__ == "__main__":
    unittest.main()
