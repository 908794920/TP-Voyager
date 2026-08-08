from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.application.context_service import ProjectContextService
from agent_runtime.application.knowledge_service import KnowledgeRuntimeService
from agent_runtime.application.plan_execution_service import (
    PlanExecutionService,
    PlanExecutionStateError,
    PlanStepMaterial,
)
from agent_runtime.application.planner_service import PlannerService
from agent_runtime.application.task_launch_service import TaskLaunchService
from agent_runtime.application.task_service import TaskService
from agent_runtime.domain.enums import EventType
from agent_runtime.domain.ids import new_runtime_session_id, new_task_id
from agent_runtime.domain.lineage import TaskLineage
from agent_runtime.domain.session import Session
from agent_runtime.domain.structured_result import RESULT_SCHEMA, StructuredResult
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database


class PlanExecutionControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()
        self.tasks = TaskService(self.db)
        self.planner = PlannerService(self.db)
        self.launch_count = 0
        self.launched_prompts: list[str] = []
        self.launcher = TaskLaunchService({"qoder": self._launch_qoder})
        self.cancelled_tasks: list[str] = []
        self.service = PlanExecutionService(
            self.db, self.launcher, task_canceller=self._cancel_task
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _launch_qoder(self, **kwargs):
        self.launch_count += 1
        self.launched_prompts.append(str(kwargs.get("prompt") or ""))
        key = str(kwargs["idempotency_key"])
        existing = self.tasks.resolve_idempotent(key)
        if existing is not None:
            return {"ok": True, "task_id": existing[1], "replayed": True}

        task_id = new_task_id()
        now = 1.0 + self.launch_count
        prompt = str(kwargs.get("prompt") or "")
        route = str(kwargs.get("route") or "acp")
        # A concrete launcher may persist safe cwd/model metadata, but never prompt.
        result = self.tasks.create_task(
            task=Task(
                task_id=task_id,
                task_type="qoder",
                status="queued",
                route=route,
                created_at=now,
                updated_at=now,
            ),
            session=Session(
                session_id=new_runtime_session_id(),
                task_id=task_id,
                backend="qoder",
                route=route,
                created_at=now,
                updated_at=now,
            ),
            metadata={
                "cwd": kwargs.get("cwd"),
                "model": kwargs.get("model"),
                "runtime": "qoder",
                "agent_profile": kwargs.get("agent_profile"),
                "context_id": kwargs.get("context_id"),
                "execution_mode": kwargs.get("execution_mode"),
                "verification_plan": {
                    "commands": kwargs.get("verification_commands") or [],
                },
            },
            idempotency_key=key,
            request_fingerprint=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            lineage=TaskLineage(
                child_task_id=task_id,
                parent_task_id=None,
                root_task_id=task_id,
                context_id=str(kwargs.get("context_id") or "") or None,
                agent_profile=str(kwargs.get("agent_profile") or "") or None,
                execution_mode=str(kwargs.get("execution_mode") or "background"),
                created_at=now,
            ),
            now=now,
        )
        if result.outcome == "conflict":
            return {"ok": False, "error": result.error or "idempotency conflict"}
        return {
            "ok": True,
            "task_id": result.task_id,
            "replayed": result.outcome == "replayed",
        }

    def _cancel_task(self, task_id: str):
        self.cancelled_tasks.append(task_id)
        return {"ok": True, "task_id": task_id, "cancel_transport_requested": True}

    def _prepared_plan(self, *, plan_id: str = "pln-v2-controller") -> str:
        self.planner.create(
            plan_id=plan_id,
            name="Controller plan",
            requirement="Analyze, implement and verify one bounded change.",
            task_kind="implementation",
            complexity="medium",
            acceptance_criteria=["verification passes"],
            runtime="qoder",
            agent_profile="coder",
        )
        self.planner.validate(plan_id)
        self.planner.prepare(plan_id)
        return plan_id

    @staticmethod
    def _materials(prefix: str = "private") -> list[PlanStepMaterial]:
        return [
            PlanStepMaterial(
                step_key="analyze",
                prompt=f"{prefix} analyze prompt",
                runtime="qoder",
                route="acp_read_only",
                agent_profile="coder",
                timeout_seconds=300,
                idle_timeout_seconds=180,
            ),
            PlanStepMaterial(
                step_key="implement",
                prompt=f"{prefix} implement prompt",
                runtime="qoder",
                route="acp_read_only",
                agent_profile="coder",
                timeout_seconds=300,
                idle_timeout_seconds=180,
            ),
            PlanStepMaterial(
                step_key="verify",
                prompt=f"{prefix} verify prompt",
                runtime="qoder",
                route="acp_read_only",
                agent_profile="coder",
                verification_commands=("python -m unittest tests.test_target",),
                timeout_seconds=300,
                idle_timeout_seconds=180,
            ),
        ]

    def _complete(self, task_id: str, verification_status: str) -> None:
        task = self.tasks.get_task(task_id)
        assert task is not None
        self.tasks.save_result(
            task_id,
            structured_result=StructuredResult(
                schema=RESULT_SCHEMA,
                attempt_id=task.current_attempt_id or "",
                answer="done",
                backend="qoder",
                stop_reason="end_turn",
                verification={"status": verification_status},
            ),
            status="completed",
            version=task.version,
            terminal_reason="end_turn",
        )

    def test_start_creates_execution_workflow_and_only_first_task(self) -> None:
        plan_id = self._prepared_plan()
        result = self.service.start(
            plan_id,
            cwd=str(self.workspace),
            step_materials=self._materials(),
            execution_id="pex-controller",
        )
        self.assertEqual(result["execution_id"], "pex-controller")
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["current_stage"]["stage_key"], "analyze")
        self.assertEqual(self.launch_count, 1)
        self.assertIsNotNone(result["current_stage"]["task_id"])
        self.assertFalse(result["raw_prompt_stored"])
        with self.db.connect() as connection:
            task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            wf_count = connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
            dumped = "\n".join(
                str(value)
                for table in ("plan_executions", "plan_execution_steps", "plan_execution_events")
                for row in connection.execute(f"SELECT * FROM {table}").fetchall()
                for value in row
            )
            metadata = connection.execute(
                "SELECT metadata_json FROM sessions LIMIT 1"
            ).fetchone()[0]
        self.assertEqual(task_count, 1)
        self.assertEqual(wf_count, 1)
        self.assertNotIn("private analyze prompt", dumped)
        self.assertNotIn("private implement prompt", dumped)
        self.assertNotIn("private verify prompt", dumped)
        self.assertNotIn("private analyze prompt", metadata)
        self.assertEqual(json.loads(metadata)["cwd"], str(self.workspace))

    def test_pump_advances_one_task_at_a_time_to_completion(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        analyze_task = current["current_stage"]["task_id"]
        self._complete(analyze_task, "NOT_REQUESTED")
        current = self.service.pump(current["execution_id"])
        self.assertEqual(current["current_stage"]["stage_key"], "implement")
        self.assertEqual(self.launch_count, 2)

        implement_task = current["current_stage"]["task_id"]
        self._complete(implement_task, "NOT_REQUESTED")
        current = self.service.pump(current["execution_id"])
        self.assertEqual(current["current_stage"]["stage_key"], "verify")
        self.assertEqual(self.launch_count, 3)

        verify_task = current["current_stage"]["task_id"]
        self._complete(verify_task, "PASSED")
        final = self.service.pump(current["execution_id"])
        self.assertEqual(final["status"], "completed")
        self.assertIsNone(final["current_stage"])
        self.assertEqual(self.launch_count, 3)
        plan_result = self.service.result(final["execution_id"])
        self.assertEqual(plan_result["schema"], "agent-runtime.plan_result/v2")
        self.assertTrue(plan_result["final"])
        self.assertEqual(plan_result["status"], "completed")
        self.assertEqual(len(plan_result["task_refs"]), 3)
        self.assertEqual(plan_result["verification_summary"]["passed"], 1)
        self.assertEqual(plan_result["verification_summary"]["not_requested"], 2)
        self.assertEqual(plan_result["final_engineering_summary"], "done")
        self.assertFalse(plan_result["generated_by_model"])
        restarted = PlanExecutionService(self.db, self.launcher)
        self.assertEqual(restarted.result(final["execution_id"]), plan_result)

    def test_verification_failure_stops_execution(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        self._complete(current["current_stage"]["task_id"], "NOT_REQUESTED")
        current = self.service.pump(current["execution_id"])
        self._complete(current["current_stage"]["task_id"], "NOT_REQUESTED")
        current = self.service.pump(current["execution_id"])
        self._complete(current["current_stage"]["task_id"], "FAILED")
        final = self.service.pump(current["execution_id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(self.launch_count, 3)
        plan_result = self.service.result(final["execution_id"])
        self.assertTrue(plan_result["final"])
        self.assertEqual(plan_result["verification_summary"]["failed"], 1)
        self.assertEqual(plan_result["failure"]["reason_code"], "verification_failed")

    def test_same_start_is_idempotent_and_changed_manifest_conflicts(self) -> None:
        plan_id = self._prepared_plan()
        first = self.service.start(
            plan_id,
            cwd=str(self.workspace),
            step_materials=self._materials(),
            execution_id="pex-idempotent",
        )
        second = self.service.start(
            plan_id,
            cwd=str(self.workspace),
            step_materials=self._materials(),
            execution_id="pex-other-request-id",
        )
        self.assertEqual(second["execution_id"], first["execution_id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.launch_count, 1)
        with self.assertRaises(PlanExecutionStateError):
            self.service.start(
                plan_id,
                cwd=str(self.workspace),
                step_materials=self._materials("changed"),
            )

    def test_restart_after_terminal_task_does_not_rerun_completed_stage(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        first_task = current["current_stage"]["task_id"]
        self._complete(first_task, "NOT_REQUESTED")

        # New controller process: no prompt/query material cache survives.
        restarted = PlanExecutionService(self.db, self.launcher)
        recovered = restarted.pump(current["execution_id"])
        self.assertEqual(recovered["status"], "needs_review")
        self.assertEqual(
            recovered["reason_code"], "execution_input_required_after_restart"
        )
        self.assertEqual(self.launch_count, 1)
        snapshot = restarted.result(current["execution_id"])
        self.assertFalse(snapshot["final"])
        self.assertEqual(snapshot["status"], "needs_review")
        self.assertEqual(
            snapshot["needs_review"]["reason_code"],
            "execution_input_required_after_restart",
        )
        with self.db.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1
            )

    def test_resume_rebinds_only_undispatched_material_after_restart(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        self._complete(current["current_stage"]["task_id"], "NOT_REQUESTED")
        restarted = PlanExecutionService(self.db, self.launcher)
        blocked = restarted.pump(current["execution_id"])
        self.assertEqual(blocked["status"], "needs_review")

        resumed = restarted.resume(
            current["execution_id"],
            cwd=str(self.workspace),
            step_materials=self._materials()[1:],
        )
        self.assertEqual(resumed["status"], "running")
        self.assertEqual(resumed["current_stage"]["stage_key"], "implement")
        self.assertEqual(self.launch_count, 2)

    def test_resume_rejects_wrong_prompt_hash_without_dispatch(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        self._complete(current["current_stage"]["task_id"], "NOT_REQUESTED")
        restarted = PlanExecutionService(self.db, self.launcher)
        restarted.pump(current["execution_id"])
        changed = self._materials("changed")[1:]
        with self.assertRaises(PlanExecutionStateError):
            restarted.resume(
                current["execution_id"],
                cwd=str(self.workspace),
                step_materials=changed,
            )
        self.assertEqual(self.launch_count, 1)
        self.assertEqual(
            restarted.status(current["execution_id"])["status"], "needs_review"
        )

    def test_startup_reconcile_marks_future_input_required_after_task_truth_refresh(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        self._complete(current["current_stage"]["task_id"], "NOT_REQUESTED")
        restarted = PlanExecutionService(self.db, self.launcher)
        reports = restarted.reconcile_all()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["status"], "needs_review")
        self.assertEqual(
            reports[0]["reason_code"], "execution_input_required_after_restart"
        )
        self.assertEqual(self.launch_count, 1)

    def test_approval_blocks_then_explicit_approval_advances(self) -> None:
        plan_id = "pln-v2-approval"
        self.planner.create(
            plan_id=plan_id,
            name="Approval plan",
            requirement="Implement with an operator checkpoint.",
            task_kind="implementation",
            complexity="medium",
            acceptance_criteria=["verification passes"],
            runtime="qoder",
            agent_profile="coder",
            require_approval=True,
        )
        self.planner.validate(plan_id)
        self.planner.prepare(plan_id)
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        self._complete(current["current_stage"]["task_id"], "NOT_REQUESTED")
        current = self.service.pump(current["execution_id"])
        self.assertEqual(current["current_stage"]["stage_key"], "implement")
        self._complete(current["current_stage"]["task_id"], "NOT_REQUESTED")
        blocked = self.service.pump(current["execution_id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["reason_code"], "approval_required")
        self.assertEqual(blocked["current_stage"]["status"], "waiting_approval")
        self.service.workflows.record_approval(
            blocked["workflow_id"], "implement", decision="approved"
        )
        advanced = self.service.pump(blocked["execution_id"])
        self.assertEqual(advanced["status"], "running")
        self.assertEqual(advanced["current_stage"]["stage_key"], "verify")
        self.assertEqual(self.launch_count, 3)

    def test_lost_task_requires_review_without_retry(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        task = self.tasks.get_task(current["current_stage"]["task_id"])
        assert task is not None
        self.tasks.update_status(
            task.task_id,
            status="lost",
            event_type=EventType.TASK_LOST.value,
            version=task.version,
            finished_at=3.0,
            terminal_reason="reconcile_unknown",
        )
        result = self.service.pump(current["execution_id"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["reason_code"], "task_lost")
        self.assertEqual(self.launch_count, 1)

    def test_orphaned_task_requires_review_without_retry(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        task = self.tasks.get_task(current["current_stage"]["task_id"])
        assert task is not None
        self.tasks.update_status(
            task.task_id,
            status="orphaned",
            event_type=EventType.TASK_ORPHANED.value,
            version=task.version,
            finished_at=3.0,
            terminal_reason="reconcile_unbound_live_host",
        )
        result = self.service.pump(current["execution_id"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["reason_code"], "task_orphaned")
        self.assertEqual(self.launch_count, 1)

    def test_plan_cancel_does_not_cancel_current_task_by_default(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        task_id = current["current_stage"]["task_id"]
        cancelled = self.service.cancel(current["execution_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["current_task_id"], task_id)
        self.assertFalse(cancelled["cancel_current_task"])
        self.assertEqual(self.cancelled_tasks, [])
        durable = self.tasks.get_task(task_id)
        assert durable is not None
        self.assertEqual(durable.status, "queued")
        self.assertTrue(self.service.result(current["execution_id"])["final"])

    def test_plan_cancel_can_explicitly_request_current_task_cancel(self) -> None:
        plan_id = self._prepared_plan()
        current = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        task_id = current["current_stage"]["task_id"]
        cancelled = self.service.cancel(
            current["execution_id"], cancel_current_task=True
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.cancelled_tasks, [task_id])
        self.assertTrue(cancelled["current_task_cancel_result"]["ok"])

    def test_explicit_context_and_knowledge_are_verified_and_injected_only_in_memory(self) -> None:
        source = self.workspace / "architecture.md"
        source.write_text(
            "SQLite durable rows are the source of truth.\nController must not retry silently.\n",
            encoding="utf-8",
        )
        knowledge = KnowledgeRuntimeService(self.db).register(
            str(self.workspace), ["architecture.md"], knowledge_id="knw-v2"
        ).collection
        plan_id = "pln-v2-context"
        self.planner.create(
            plan_id=plan_id,
            name="Context plan",
            requirement="Use explicit project knowledge.",
            task_kind="implementation",
            complexity="medium",
            acceptance_criteria=["verification passes"],
            runtime="qoder",
            agent_profile="coder",
            context_id=knowledge["context_id"],
            knowledge_id=knowledge["knowledge_id"],
        )
        self.planner.validate(plan_id)
        self.planner.prepare(plan_id)
        materials = [
            PlanStepMaterial(
                **{
                    **item.__dict__,
                    "knowledge_query": "SQLite source of truth",
                }
            )
            for item in self._materials()
        ]
        result = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=materials
        )
        self.assertEqual(result["status"], "running")
        launched = self.launched_prompts[-1]
        self.assertIn("Runtime-bound Project Context", launched)
        self.assertIn("Runtime-bound Knowledge Bundle", launched)
        self.assertIn("SQLite durable rows are the source of truth", launched)
        history = self.service.history(result["execution_id"])["events"]
        ready_events = [event for event in history if event["event_type"] == "step_ready"]
        self.assertTrue(ready_events)
        self.assertTrue(ready_events[0]["payload"]["context_injected"] )
        self.assertTrue(ready_events[0]["payload"]["knowledge_injected"] )
        self.assertIn("knowledge_resolution_id", ready_events[0]["payload"] )
        with self.db.connect() as connection:
            dumped = "\n".join(
                str(value)
                for table in (
                    "plan_executions",
                    "plan_execution_steps",
                    "plan_execution_events",
                    "knowledge_resolutions",
                )
                for row in connection.execute(f"SELECT * FROM {table}").fetchall()
                for value in row
            )
        self.assertNotIn("SQLite source of truth", dumped)
        self.assertNotIn("Controller must not retry silently", dumped)

    def test_context_drift_blocks_before_backend_dispatch(self) -> None:
        source = self.workspace / "architecture.md"
        source.write_text("original context\n", encoding="utf-8")
        context = ProjectContextService(self.db).register(
            str(self.workspace), ["architecture.md"], context_id="ctx-v2-drift"
        ).manifest
        plan_id = "pln-v2-drift"
        self.planner.create(
            plan_id=plan_id,
            name="Drift plan",
            requirement="Use fixed context.",
            task_kind="implementation",
            acceptance_criteria=["verification passes"],
            runtime="qoder",
            agent_profile="coder",
            context_id=context["context_id"],
        )
        self.planner.validate(plan_id)
        self.planner.prepare(plan_id)
        source.write_text("changed context\n", encoding="utf-8")
        result = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["reason_code"], "context_or_knowledge_drift")
        self.assertEqual(self.launch_count, 0)

    def test_workspace_changes_execution_manifest_without_persisting_it_in_plan_rows(self) -> None:
        plan_id = self._prepared_plan()
        first = self.service.start(
            plan_id, cwd=str(self.workspace), step_materials=self._materials()
        )
        self.assertRegex(first["input_manifest_sha256"], r"^[0-9a-f]{64}$")
        other = self.root / "other-project"
        other.mkdir()
        with self.assertRaises(PlanExecutionStateError):
            self.service.start(
                plan_id, cwd=str(other), step_materials=self._materials()
            )
        with self.db.connect() as connection:
            dumped = "\n".join(
                str(value)
                for table in ("plan_executions", "plan_execution_steps", "plan_execution_events")
                for row in connection.execute(f"SELECT * FROM {table}").fetchall()
                for value in row
            )
        self.assertNotIn(str(self.workspace), dumped)


if __name__ == "__main__":
    unittest.main()
