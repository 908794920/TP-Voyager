from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_runtime.persistence.database import Database
from agent_runtime.application.context_service import ProjectContextService
from agent_runtime.application.knowledge_service import KnowledgeRuntimeService
from agent_runtime.application.planner_service import (
    PlannerConflictError,
    PlannerPolicyError,
    PlannerService,
)


class PlannerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "architecture.md").write_text(
            "# Architecture\nSQLite is the source of truth.\n", encoding="utf-8"
        )
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()
        self.service = PlannerService(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create(self, **overrides):
        values = {
            "name": "Bounded feature",
            "requirement": "Implement one bounded feature without hidden fallback.",
            "task_kind": "implementation",
            "complexity": "medium",
            "acceptance_criteria": ["tests pass", "result is reported"],
        }
        values.update(overrides)
        return self.service.create(**values)

    def test_create_is_draft_linear_content_free_and_does_not_dispatch(self) -> None:
        secret = "Implement private requirement token-123"
        result = self.create(requirement=secret, runtime="qoder", agent_profile="S2")
        self.assertEqual(result["schema"], "workbuddy.planner_plan/v1")
        self.assertEqual(result["plan"]["status"], "draft")
        self.assertEqual([x["step_key"] for x in result["steps"]], ["analyze", "implement", "verify"])
        self.assertEqual(result["steps"][0]["depends_on_step_ids"], [])
        self.assertEqual(len(result["steps"][1]["depends_on_step_ids"]), 1)
        self.assertFalse(result["backend_selected"])
        self.assertFalse(result["workflow_created"])
        self.assertFalse(result["task_created"])
        self.assertFalse(result["dispatch_performed"])
        with self.db.connect() as connection:
            dump = "\n".join(
                str(value)
                for table in ("planner_plans", "planner_steps", "planner_dependencies", "planner_events")
                for row in connection.execute(f"SELECT * FROM {table}").fetchall()
                for value in row
            )
            columns = {
                str(row[1])
                for table in ("planner_plans", "planner_steps", "planner_events")
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            workflow_count = connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
        self.assertNotIn(secret, dump)
        self.assertFalse({"requirement", "acceptance", "prompt", "content"} & columns)
        self.assertEqual(task_count, 0)
        self.assertEqual(workflow_count, 0)

    def test_lifecycle_is_explicit_and_replay_safe(self) -> None:
        created = self.create(plan_id="pln-fixed")
        plan_id = created["plan"]["plan_id"]
        with self.assertRaises(PlannerPolicyError):
            self.service.prepare(plan_id)
        validated = self.service.validate(plan_id)
        self.assertEqual(validated["plan"]["status"], "validated")
        self.assertFalse(validated["replayed"])
        self.assertTrue(self.service.validate(plan_id)["replayed"])
        prepared = self.service.prepare(plan_id)
        self.assertEqual(prepared["plan"]["status"], "prepared")
        self.assertEqual(prepared["execution_spec"]["schema"], "workbuddy.execution_spec/v1")
        self.assertTrue(prepared["execution_spec"]["caller_must_create_workflow"])
        self.assertTrue(prepared["execution_spec"]["caller_must_create_and_bind_tasks"])
        self.assertFalse(prepared["execution_spec"]["selection_performed"])
        self.assertFalse(prepared["execution_spec"]["dispatch_performed"])
        self.assertTrue(self.service.prepare(plan_id)["replayed"])
        history = self.service.history(plan_id=plan_id)["events"]
        self.assertEqual(
            [item["event_type"] for item in reversed(history)],
            ["plan_created", "plan_validated", "plan_prepared"],
        )

    def test_idempotent_create_replays_and_changed_intent_conflicts(self) -> None:
        first = self.create(plan_id="pln-idem")
        second = self.create(plan_id="pln-idem")
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        with self.assertRaises(PlannerConflictError):
            self.create(plan_id="pln-idem", requirement="different")
        with self.assertRaises(PlannerConflictError):
            self.create(plan_id="pln-idem", require_approval=True)

    def test_high_complexity_adds_review_and_risk_is_high(self) -> None:
        result = self.create(complexity="high")
        self.assertEqual(result["plan"]["risk_level"], "high")
        self.assertEqual(
            [x["step_key"] for x in result["steps"]],
            ["analyze", "implement", "verify", "review"],
        )

    def test_verification_can_be_explicitly_disabled(self) -> None:
        result = self.create(verification_required=False)
        self.assertNotIn("verify", [x["step_key"] for x in result["steps"]])
        self.assertTrue(all(not x["verification_required"] for x in result["steps"]))

    def test_approval_is_explicit_and_bounded_to_impactful_step(self) -> None:
        default = self.create()
        self.assertFalse(any(x["approval_required"] for x in default["steps"]))
        gated = self.create(plan_id="pln-gated", require_approval=True)
        approvals = [x["step_key"] for x in gated["steps"] if x["approval_required"]]
        self.assertEqual(approvals, ["implement"])

    def test_knowledge_and_context_references_are_explicitly_validated(self) -> None:
        context = ProjectContextService(self.db).register(
            str(self.project), ["architecture.md"], context_id="ctx-plan"
        ).manifest
        knowledge = KnowledgeRuntimeService(self.db).register(
            str(self.project), ["architecture.md"], knowledge_id="knw-plan"
        ).collection
        result = self.create(
            plan_id="pln-refs",
            context_id=knowledge["context_id"],
            knowledge_id=knowledge["knowledge_id"],
        )
        self.assertEqual(result["plan"]["knowledge_id"], "knw-plan")
        self.service.validate("pln-refs")
        context_only = self.create(
            plan_id="pln-context-only", context_id=context["context_id"]
        )
        self.assertEqual(context_only["plan"]["context_id"], "ctx-plan")
        with self.assertRaises(PlannerPolicyError):
            self.create(plan_id="pln-missing", knowledge_id="knw-missing")

    def test_strict_linear_validation_rejects_tampering(self) -> None:
        created = self.create(plan_id="pln-tampered")
        second = created["steps"][1]["step_id"]
        with self.db.transaction() as connection:
            connection.execute(
                "DELETE FROM planner_dependencies WHERE plan_id=? AND step_id=?",
                ("pln-tampered", second),
            )
        with self.assertRaises(PlannerPolicyError):
            self.service.validate("pln-tampered")

    def test_input_limits_and_strict_boolean_contract_fail_closed(self) -> None:
        with self.assertRaises(PlannerPolicyError):
            self.create(require_approval="false")  # type: ignore[arg-type]
        with self.assertRaises(PlannerPolicyError):
            self.create(acceptance_criteria="not-a-list")  # type: ignore[arg-type]
        with self.assertRaises(PlannerPolicyError):
            self.create(requirement="x" * 20_001)

    def test_list_status_and_history_are_content_free(self) -> None:
        self.create(plan_id="pln-list", requirement="private list requirement")
        listed = self.service.list(status="draft")
        status = self.service.status("pln-list")
        history = self.service.history(plan_id="pln-list")
        encoded = json.dumps([listed, status, history], ensure_ascii=False)
        self.assertNotIn("private list requirement", encoded)
        self.assertFalse(listed["backend_selected"])
        self.assertEqual(history["events"][0]["reason_codes"], ["deterministic_policy_applied"])


if __name__ == "__main__":
    unittest.main()
