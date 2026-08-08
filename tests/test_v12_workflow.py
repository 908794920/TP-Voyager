from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.domain.enums import (
    TERMINAL_STATUS_VALUES,
    EventType,
    TaskStatus,
    WorkflowStageStatus,
)
from agent_runtime.domain.lineage import TaskLineage
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.domain.workflow import WorkflowStageSpec
from agent_runtime.persistence.database import Database
from agent_runtime.application.replay_service import ReplayService
from agent_runtime.application.task_service import TaskService
from agent_runtime.application.workflow_service import (
    WorkflowService,
    WorkflowStateError,
)


class WorkflowV12Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self.tmp.name) / "runtime.db")
        self.db.initialize()
        self.tasks = TaskService(self.db)
        self.workflows = WorkflowService(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create_task(
        self,
        task_id: str,
        *,
        context_id: str | None = None,
        task_type: str = "qoder",
        agent_profile: str | None = "analyst",
    ) -> str:
        result = self.tasks.create_task(
            task=Task(
                task_id=task_id,
                task_type=task_type,
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
                context_id=context_id,
                agent_profile=agent_profile,
                created_at=1.0,
            ),
            now=1.0,
        )
        self.assertEqual(result.outcome, "created")
        return task_id

    def set_status(self, task_id: str, status: str) -> None:
        task = self.tasks.get_task(task_id)
        assert task is not None
        if status == "completed":
            self.tasks.save_result(
                task_id,
                result={"answer": "ok"},
                status="completed",
                version=task.version,
                terminal_reason="end_turn",
            )
        else:
            event = {
                "running": EventType.TASK_STARTED.value,
                "failed": EventType.TASK_FAILED.value,
                "cancelled": EventType.CANCEL_CONFIRMED.value,
            }[status]
            self.tasks.update_status(
                task_id,
                status=status,
                event_type=event,
                version=task.version,
                started_at=2.0 if status == "running" else None,
                finished_at=3.0 if status in {"failed", "cancelled"} else None,
                terminal_reason=status if status != "running" else None,
            )

    def create_workflow(self, *, approval: bool = False, context_id: str | None = None):
        return self.workflows.create_workflow(
            name="Implementation flow",
            context_id=context_id,
            stages=[
                WorkflowStageSpec(
                    "analysis", "Analysis", approval_required=approval,
                    runtime="qoder", agent_profile="analyst",
                ),
                WorkflowStageSpec("implementation", "Implementation"),
            ],
        )

    def test_create_is_linear_and_replayable(self) -> None:
        workflow = self.create_workflow()
        self.assertEqual(workflow["status"], "active")
        self.assertEqual(
            [stage["status"] for stage in workflow["stages"]],
            ["ready", "pending"],
        )
        replay = ReplayService(self.db).replay_workflow(workflow["workflow_id"])
        self.assertTrue(replay["integrity_ok"], replay["anomalies"])

    def test_duplicate_stage_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.workflows.create_workflow(
                name="bad",
                stages=[
                    WorkflowStageSpec("same", "A"),
                    WorkflowStageSpec("same", "B"),
                ],
            )

    def test_running_task_binding_and_idempotent_replay(self) -> None:
        workflow = self.create_workflow()
        task_id = self.create_task("wb-run")
        self.set_status(task_id, "running")
        first = self.workflows.bind_task(
            workflow["workflow_id"], "analysis", task_id,
        )
        self.assertFalse(first.replayed)
        self.assertEqual(first.workflow["stages"][0]["status"], "running")
        replay = self.workflows.bind_task(
            workflow["workflow_id"], "analysis", task_id,
        )
        self.assertTrue(replay.replayed)

    def test_completed_stage_advances_next_stage(self) -> None:
        workflow = self.create_workflow()
        task_id = self.create_task("wb-done")
        self.workflows.bind_task(workflow["workflow_id"], "analysis", task_id)
        self.set_status(task_id, "completed")
        refreshed = self.workflows.refresh_workflow(workflow["workflow_id"])
        self.assertEqual(
            [stage["status"] for stage in refreshed["stages"]],
            ["completed", "ready"],
        )
        self.assertEqual(refreshed["status"], "active")

    def test_optional_operator_checkpoint_blocks_then_approves(self) -> None:
        workflow = self.create_workflow(approval=True)
        task_id = self.create_task("wb-approval")
        self.workflows.bind_task(workflow["workflow_id"], "analysis", task_id)
        self.set_status(task_id, "completed")
        blocked = self.workflows.refresh_workflow(workflow["workflow_id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["stages"][0]["status"], "waiting_approval")
        approved = self.workflows.record_approval(
            workflow["workflow_id"], "analysis", reason_code="verified",
        )
        self.assertFalse(approved.replayed)
        self.assertEqual(
            [stage["status"] for stage in approved.workflow["stages"]],
            ["completed", "ready"],
        )
        replay = self.workflows.record_approval(
            workflow["workflow_id"], "analysis", reason_code="verified",
        )
        self.assertTrue(replay.replayed)

    def test_rejection_is_terminal_for_workflow(self) -> None:
        workflow = self.create_workflow(approval=True)
        task_id = self.create_task("wb-reject")
        self.workflows.bind_task(workflow["workflow_id"], "analysis", task_id)
        self.set_status(task_id, "completed")
        self.workflows.refresh_workflow(workflow["workflow_id"])
        rejected = self.workflows.record_approval(
            workflow["workflow_id"], "analysis",
            decision="rejected", reason_code="needs_changes",
        )
        self.assertEqual(rejected.workflow["status"], "failed")
        self.assertEqual(rejected.workflow["stages"][0]["status"], "failed")

    def test_failed_task_fails_workflow_without_touching_task_state(self) -> None:
        workflow = self.create_workflow()
        task_id = self.create_task("wb-fail")
        self.workflows.bind_task(workflow["workflow_id"], "analysis", task_id)
        self.set_status(task_id, "failed")
        refreshed = self.workflows.refresh_workflow(workflow["workflow_id"])
        self.assertEqual(refreshed["status"], "failed")
        self.assertEqual(self.tasks.get_task(task_id).status, "failed")

    def test_stage_runtime_and_agent_profile_are_enforced(self) -> None:
        workflow = self.workflows.create_workflow(
            name="typed",
            stages=[
                WorkflowStageSpec(
                    "one", "One", runtime="qoder", agent_profile="reviewer"
                )
            ],
        )
        wrong_runtime = self.create_task(
            "task-runtime-mismatch", task_type="codebuddy", agent_profile="reviewer"
        )
        self.set_status(wrong_runtime, "running")
        with self.assertRaises(WorkflowStateError):
            self.workflows.bind_task(
                workflow["workflow_id"], "one", wrong_runtime
            )

        qoder = self.create_task(
            "wb-profile-mismatch", task_type="qoder", agent_profile="coder"
        )
        self.set_status(qoder, "running")
        with self.assertRaises(WorkflowStateError):
            self.workflows.bind_task(workflow["workflow_id"], "one", qoder)

        matching = self.create_task(
            "wb-profile-match", task_type="qoder", agent_profile="reviewer"
        )
        self.set_status(matching, "running")
        bound = self.workflows.bind_task(
            workflow["workflow_id"], "one", matching
        )
        self.assertEqual(bound.workflow["stages"][0]["status"], "running")

    def test_context_mismatch_is_rejected(self) -> None:
        workflow = self.create_workflow(context_id="ctx-a")
        task_id = self.create_task("wb-context", context_id="ctx-b")
        with self.assertRaises(WorkflowStateError):
            self.workflows.bind_task(
                workflow["workflow_id"], "analysis", task_id,
            )

    def test_task_cannot_bind_to_two_stages(self) -> None:
        first = self.create_workflow()
        second = self.create_workflow()
        task_id = self.create_task("wb-once")
        self.workflows.bind_task(first["workflow_id"], "analysis", task_id)
        with self.assertRaises(WorkflowStateError):
            self.workflows.bind_task(second["workflow_id"], "analysis", task_id)

    def test_task_event_replay_matches_new_status_payloads(self) -> None:
        task_id = self.create_task("wb-replay")
        self.set_status(task_id, "running")
        self.set_status(task_id, "completed")
        replay = ReplayService(self.db).replay_task(task_id)
        self.assertTrue(replay["status_match"], replay["anomalies"])
        self.assertTrue(replay["result_available_match"])
        self.assertTrue(replay["integrity_ok"], replay["anomalies"])
        self.assertEqual(replay["projected"]["status"], TaskStatus.COMPLETED.value)

    def test_stage_status_mapping_is_exhaustive_for_terminal_task_states(self) -> None:
        expected = {
            TaskStatus.COMPLETED.value: WorkflowStageStatus.COMPLETED.value,
            TaskStatus.CANCELLED.value: WorkflowStageStatus.CANCELLED.value,
            TaskStatus.FAILED.value: WorkflowStageStatus.FAILED.value,
            TaskStatus.LOST.value: WorkflowStageStatus.FAILED.value,
            TaskStatus.ORPHANED.value: WorkflowStageStatus.FAILED.value,
        }
        self.assertEqual(set(expected), set(TERMINAL_STATUS_VALUES))
        for task_status, stage_status in expected.items():
            with self.subTest(task_status=task_status):
                self.assertEqual(
                    self.workflows._stage_status_for_task(
                        task_status, False, None
                    ),
                    stage_status,
                )

    def test_all_nonterminal_task_states_map_to_running_stage(self) -> None:
        for task_status in TaskStatus:
            if task_status.value in TERMINAL_STATUS_VALUES:
                continue
            with self.subTest(task_status=task_status.value):
                self.assertEqual(
                    self.workflows._stage_status_for_task(
                        task_status.value, False, None
                    ),
                    WorkflowStageStatus.RUNNING.value,
                )

    def test_workflow_replay_detects_no_drift_after_approval(self) -> None:
        workflow = self.create_workflow(approval=True)
        task_id = self.create_task("wb-replay-workflow")
        self.workflows.bind_task(workflow["workflow_id"], "analysis", task_id)
        self.set_status(task_id, "completed")
        self.workflows.refresh_workflow(workflow["workflow_id"])
        self.workflows.record_approval(workflow["workflow_id"], "analysis")
        replay = ReplayService(self.db).replay_workflow(workflow["workflow_id"])
        self.assertTrue(replay["integrity_ok"], replay["anomalies"])


if __name__ == "__main__":
    unittest.main()
