from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_runtime.application.planner_service import PlannerService
from agent_runtime.application.task_service import TaskService
from agent_runtime.application.workflow_service import WorkflowService
from agent_runtime.domain.enums import WorkflowStageStatus
from agent_runtime.domain.lineage import TaskLineage
from agent_runtime.domain.plan_execution import (
    PLAN_RESULT_SCHEMA,
    PlanExecution,
    PlanExecutionEvent,
    PlanExecutionStep,
    PlanResult,
)
from agent_runtime.domain.session import Session
from agent_runtime.domain.structured_result import RESULT_SCHEMA, StructuredResult
from agent_runtime.domain.task import Task
from agent_runtime.domain.workflow import WorkflowStageSpec
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.migrations import _MIGRATIONS
from agent_runtime.persistence.plan_execution_repository import PlanExecutionRepository


class WorkflowV2VerificationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self.tmp.name) / "runtime.db")
        self.db.initialize()
        self.tasks = TaskService(self.db)
        self.workflows = WorkflowService(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create_task(self, task_id: str) -> str:
        created = self.tasks.create_task(
            task=Task(
                task_id=task_id,
                task_type="qoder",
                status="queued",
                route="acp_read_only",
                created_at=1.0,
                updated_at=1.0,
            ),
            session=Session(
                session_id=f"rs-{task_id}",
                task_id=task_id,
                backend="qoder",
                route="acp_read_only",
                created_at=1.0,
                updated_at=1.0,
            ),
            metadata={},
            idempotency_key=f"key-{task_id}",
            request_fingerprint=f"fp-{task_id}",
            lineage=TaskLineage(
                child_task_id=task_id,
                parent_task_id=None,
                root_task_id=task_id,
                agent_profile="coder",
                created_at=1.0,
            ),
            now=1.0,
        )
        self.assertEqual(created.outcome, "created")
        return task_id

    def complete(self, task_id: str, verification_status: str) -> None:
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

    def workflow(self, *, verification_required: bool = True):
        return self.workflows.create_workflow(
            name="V2 execution flow",
            stages=[
                WorkflowStageSpec(
                    "implement",
                    "Implement",
                    runtime="qoder",
                    agent_profile="coder",
                    verification_required=verification_required,
                    completion_policy="plan_execution_v2",
                ),
                WorkflowStageSpec(
                    "review",
                    "Review",
                    completion_policy="plan_execution_v2",
                ),
            ],
        )

    def test_passed_verification_advances_v2_stage(self) -> None:
        workflow = self.workflow()
        task_id = self.create_task("wb-v2-pass")
        self.workflows.bind_task(workflow["workflow_id"], "implement", task_id)
        self.complete(task_id, "PASSED")
        result = self.workflows.refresh_workflow(workflow["workflow_id"])
        self.assertEqual([x["status"] for x in result["stages"]], ["completed", "ready"])
        self.assertIsNone(result["stages"][0]["block_reason"])

    def test_failed_verification_fails_v2_workflow(self) -> None:
        workflow = self.workflow()
        task_id = self.create_task("wb-v2-fail")
        self.workflows.bind_task(workflow["workflow_id"], "implement", task_id)
        self.complete(task_id, "FAILED")
        result = self.workflows.refresh_workflow(workflow["workflow_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stages"][0]["status"], "failed")
        self.assertEqual(result["stages"][0]["block_reason"], "verification_failed")
        self.assertEqual(result["stages"][1]["status"], "pending")

    def test_needs_review_verification_blocks_without_advancing(self) -> None:
        workflow = self.workflow()
        task_id = self.create_task("wb-v2-review")
        self.workflows.bind_task(workflow["workflow_id"], "implement", task_id)
        self.complete(task_id, "NEEDS_REVIEW")
        result = self.workflows.refresh_workflow(workflow["workflow_id"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stages"][0]["status"], WorkflowStageStatus.NEEDS_REVIEW.value)
        self.assertEqual(result["stages"][0]["block_reason"], "verification_needs_review")
        self.assertEqual(result["stages"][1]["status"], "pending")

    def test_required_verification_not_requested_fails_closed_to_review(self) -> None:
        workflow = self.workflow()
        task_id = self.create_task("wb-v2-required")
        self.workflows.bind_task(workflow["workflow_id"], "implement", task_id)
        self.complete(task_id, "NOT_REQUESTED")
        result = self.workflows.refresh_workflow(workflow["workflow_id"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stages"][0]["status"], "needs_review")
        self.assertEqual(
            result["stages"][0]["block_reason"],
            "verification_required_but_not_passed",
        )

    def test_optional_verification_allows_not_requested_but_not_negative_signal(self) -> None:
        workflow = self.workflow(verification_required=False)
        task_id = self.create_task("wb-v2-optional")
        self.workflows.bind_task(workflow["workflow_id"], "implement", task_id)
        self.complete(task_id, "NOT_REQUESTED")
        result = self.workflows.refresh_workflow(workflow["workflow_id"])
        self.assertEqual(result["stages"][0]["status"], "completed")
        self.assertEqual(result["stages"][1]["status"], "ready")

    def test_completed_task_without_result_blocks_v2_workflow(self) -> None:
        workflow = self.workflow(verification_required=False)
        task_id = self.create_task("wb-v2-missing-result")
        with self.db.connect() as connection:
            with connection:
                connection.execute(
                    "UPDATE tasks SET status='completed', finished_at=3.0, version=version+1 "
                    "WHERE task_id=?",
                    (task_id,),
                )
        self.workflows.bind_task(workflow["workflow_id"], "implement", task_id)
        result = self.workflows.refresh_workflow(workflow["workflow_id"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stages"][0]["status"], "needs_review")
        self.assertEqual(result["stages"][0]["block_reason"], "result_unavailable")


class PlanExecutionPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self.tmp.name) / "runtime.db")
        self.db.initialize()
        self.planner = PlannerService(self.db)
        self.workflows = WorkflowService(self.db)
        self.repo = PlanExecutionRepository(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def prepared_plan(self):
        created = self.planner.create(
            name="V2 plan",
            requirement="Implement the requested change",
            complexity="low",
            verification_required=True,
        )
        plan_id = created["plan"]["plan_id"]
        self.planner.validate(plan_id)
        return self.planner.prepare(plan_id)

    def test_execution_rows_bind_plan_workflow_and_store_hashes_not_prompt_content(self) -> None:
        plan = self.prepared_plan()
        step = plan["steps"][0]
        workflow = self.workflows.create_workflow(
            name="V2 plan workflow",
            stages=[
                WorkflowStageSpec(
                    item["step_key"],
                    item["title"],
                    verification_required=item["verification_required"],
                    completion_policy="plan_execution_v2",
                    runtime="qoder",
                )
                for item in plan["steps"]
            ],
        )
        stage = workflow["stages"][0]
        prompt = "private future step prompt"
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        execution = PlanExecution(
            execution_id="pex-test",
            plan_id=plan["plan"]["plan_id"],
            workflow_id=workflow["workflow_id"],
            status="ready",
            input_manifest_sha256="a" * 64,
            created_at=1.0,
            updated_at=1.0,
        )
        step_row = PlanExecutionStep(
            execution_id=execution.execution_id,
            step_id=step["step_id"],
            stage_id=stage["stage_id"],
            runtime="qoder",
            route="acp_read_only",
            prompt_sha256=prompt_hash,
            verification_required=bool(step["verification_required"]),
            verification_plan_json="{}",
            binding_json="{}",
            created_at=1.0,
            updated_at=1.0,
        )
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            self.repo.create_execution(connection, execution, [step_row])
            self.repo.append_event(
                connection,
                PlanExecutionEvent(
                    event_id="pee-test",
                    execution_id=execution.execution_id,
                    event_type="execution_created",
                    event_time=db_now,
                    status="ready",
                    payload_json="{}",
                ),
            )
        loaded = self.repo.get_execution("pex-test")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.plan_id, plan["plan"]["plan_id"])
        steps = self.repo.list_steps("pex-test")
        self.assertEqual(steps[0].prompt_sha256, prompt_hash)

        with self.db.connect() as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(plan_execution_steps)")
            }
            serialized = json.dumps(
                [dict(row) for row in connection.execute("SELECT * FROM plan_execution_steps")],
                ensure_ascii=False,
            )
        self.assertFalse({"prompt", "prompt_text", "knowledge_query", "content"} & columns)
        self.assertNotIn(prompt, serialized)

    def test_terminal_plan_result_is_separate_from_execution_status_row(self) -> None:
        plan = self.prepared_plan()
        workflow = self.workflows.create_workflow(
            name="one",
            stages=[WorkflowStageSpec("one", "One")],
        )
        execution = PlanExecution(
            execution_id="pex-result",
            plan_id=plan["plan"]["plan_id"],
            workflow_id=workflow["workflow_id"],
            status="completed",
            input_manifest_sha256="b" * 64,
            created_at=1.0,
            updated_at=2.0,
            finished_at=2.0,
        )
        payload = {"schema": PLAN_RESULT_SCHEMA, "status": "completed", "final": True}
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            self.repo.create_execution(connection, execution, [])
            self.repo.save_result(
                connection,
                PlanResult(
                    execution_id=execution.execution_id,
                    schema=PLAN_RESULT_SCHEMA,
                    result_json=json.dumps(payload),
                    created_at=db_now,
                ),
            )
        loaded = self.repo.get_result(execution.execution_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(json.loads(loaded.result_json)["final"], True)


class V10ToV11MigrationTests(unittest.TestCase):
    def test_v10_workflow_rows_survive_rebuild_with_legacy_policy(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "v10.db"
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys = ON")
            for version in sorted(_MIGRATIONS):
                with connection:
                    for statement in _MIGRATIONS[version]:
                        try:
                            connection.execute(statement)
                        except sqlite3.OperationalError as exc:
                            if "duplicate column" not in str(exc).lower():
                                raise
                    connection.execute(f"PRAGMA user_version = {version}")
            with connection:
                connection.execute(
                    "INSERT INTO workflows(workflow_id,name,status,created_at,updated_at,version) "
                    "VALUES('wf-old','Old','active',1,1,1)"
                )
                connection.execute(
                    "INSERT INTO workflow_stages(stage_id,workflow_id,stage_key,title,position,status,approval_required,created_at,updated_at) "
                    "VALUES('stage-old','wf-old','one','One',1,'waiting_approval',1,1,2)"
                )
                connection.execute(
                    "INSERT INTO workflow_approvals(approval_id,workflow_id,stage_id,decision,actor,decided_at) "
                    "VALUES('approval-old','wf-old','stage-old','approved','operator',3)"
                )
                connection.execute(
                    "INSERT INTO workflow_events(event_id,workflow_id,stage_id,event_type,event_time,payload_json,visibility) "
                    "VALUES('event-old','wf-old','stage-old','stage_status_changed',2,'{}','public')"
                )
            connection.close()

            db = Database(path)
            db.initialize()
            self.assertEqual(db.schema_version(), 11)
            with db.connect() as connection:
                stage = connection.execute(
                    "SELECT status, verification_required, completion_policy FROM workflow_stages WHERE stage_id='stage-old'"
                ).fetchone()
                approval = connection.execute(
                    "SELECT approval_id FROM workflow_approvals WHERE approval_id='approval-old'"
                ).fetchone()
                event = connection.execute(
                    "SELECT event_id FROM workflow_events WHERE event_id='event-old'"
                ).fetchone()
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(stage["status"], "waiting_approval")
            self.assertEqual(stage["verification_required"], 0)
            self.assertEqual(stage["completion_policy"], "legacy")
            self.assertIsNotNone(approval)
            self.assertIsNotNone(event)
            self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
