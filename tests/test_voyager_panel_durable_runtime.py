from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.application.task_service import TaskService
from agent_runtime.application.voyage.observability import AgentObservationStore, VoyageAgentProjection
from agent_runtime.domain.enums import EventType, TaskRoute
from agent_runtime.domain.session import Session
from agent_runtime.domain.structured_result import RESULT_SCHEMA, StructuredResult
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database


class VoyagerPanelDurableRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "runtime" / "tp_voyager.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.service = TaskService(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create(self, task_id: str, *, group_id: str = "pg-runtime") -> None:
        self.service.create_task(
            task=Task(
                task_id=task_id,
                task_type="qoder",
                status="queued",
                route=TaskRoute.ACP.value,
                created_at=1.0,
                updated_at=1.0,
            ),
            session=Session(
                session_id=f"rs-{task_id}",
                task_id=task_id,
                backend="qoder",
                route=TaskRoute.ACP.value,
                created_at=1.0,
                updated_at=1.0,
            ),
            metadata={"routing_metadata": {"presentation_group_id": group_id}},
            idempotency_key="",
            request_fingerprint=f"fp-{task_id}",
            now=1.0,
        )

    def _start(self, task_id: str) -> None:
        task = self.service.get_task(task_id)
        assert task is not None
        self.service.update_status(
            task_id,
            status="running",
            event_type=EventType.TASK_STARTED.value,
            version=task.version,
            started_at=2.0,
        )

    def _complete(self, task_id: str) -> None:
        task = self.service.get_task(task_id)
        assert task is not None
        self.service.save_result(
            task_id,
            structured_result=StructuredResult(
                schema=RESULT_SCHEMA,
                attempt_id=task.current_attempt_id or "",
                answer="done",
                backend="qoder",
                stop_reason="end_turn",
            ),
            status="completed",
            version=task.version,
            terminal_reason="end_turn",
        )
        self.service.append_activity(task_id, "final_response", details={"status": "completed"})
        self.service.append_activity(task_id, "agent_completed", details={"status": "completed"})

    def test_running_completed_and_reopened_projection_keep_same_durable_activity(self) -> None:
        self._create("durable-qoder")
        self._start("durable-qoder")
        self.service.append_activity(
            "durable-qoder",
            "tool_activity",
            details={"tool": "Read", "action": "read", "path": "agent_runtime/api/mcp_server.py", "status": "completed"},
        )
        self.service.append_activity(
            "durable-qoder",
            "status",
            details={"phase": "analysis", "status": "running", "summary": "Inspecting runtime"},
        )
        self.service.append_activity(
            "durable-qoder",
            "file_change",
            details={"action": "modify", "path": "agent_runtime/api/voyager_panel.py", "status": "completed"},
        )

        live_store = AgentObservationStore()
        live_store.append(
            "durable-qoder",
            {"kind": "tool_activity", "tool": "Read", "action": "read", "path": "README.md", "status": "completed"},
        )
        running = VoyageAgentProjection(self.service, live_store).detail("durable-qoder")
        running_kinds = [item["kind"] for item in running["timeline"]]
        self.assertIn("tool_activity", running_kinds)
        self.assertIn("status", running_kinds)
        self.assertIn("file_change", running_kinds)

        self._complete("durable-qoder")
        terminal_same_process = VoyageAgentProjection(self.service, live_store).detail("durable-qoder")
        same_kinds = [item["kind"] for item in terminal_same_process["timeline"]]
        self.assertIn("tool_activity", same_kinds)
        self.assertIn("status", same_kinds)
        self.assertIn("file_change", same_kinds)
        self.assertEqual(same_kinds[-2:], ["final_response", "agent_completed"])
        # Terminal render must ignore process-local-only live activity.
        self.assertNotIn("README.md", [item.get("path") for item in terminal_same_process["timeline"]])

        reopened_db = Database(self.db_path)
        reopened_db.initialize()
        reopened_service = TaskService(reopened_db)
        terminal_new_process_model = VoyageAgentProjection(reopened_service, AgentObservationStore()).detail("durable-qoder")
        reopened_kinds = [item["kind"] for item in terminal_new_process_model["timeline"]]
        self.assertEqual(reopened_kinds, same_kinds)
        self.assertEqual(reopened_kinds[-2:], ["final_response", "agent_completed"])

    def test_terminal_render_does_not_let_large_process_local_stream_evict_durable_activity(self) -> None:
        self._create("terminal-window")
        self._start("terminal-window")
        self.service.append_activity(
            "terminal-window", "tool_activity", now=3.0,
            details={"tool": "Read", "action": "read", "path": "src/important.py", "status": "completed"},
        )
        live_store = AgentObservationStore(max_events_per_task=1000)
        # Mirrors the reported Qoder case: hundreds of in-process stream events
        # coexist with only a small durable public activity set.  The previous
        # implementation merged these streams and sliced to limit=200, which
        # evicted the canonical tool activity at terminal render time.
        for index in range(413):
            live_store.append(
                "terminal-window",
                {
                    "kind": "status",
                    "timestamp": 10.0 + index,
                    "phase": "stream",
                    "status": "running",
                    "summary": f"event-{index}",
                },
            )
        self._complete("terminal-window")

        detail = VoyageAgentProjection(self.service, live_store).detail("terminal-window", limit=200)
        kinds = [item["kind"] for item in detail["timeline"]]
        self.assertIn("tool_activity", kinds)
        self.assertEqual(kinds[-2:], ["final_response", "agent_completed"])
        self.assertEqual(
            [item.get("path") for item in detail["timeline"] if item.get("kind") == "tool_activity"],
            ["src/important.py"],
        )

    def test_completed_terminal_markers_are_always_last_even_if_late_durable_activity_exists(self) -> None:
        self._create("terminal-order")
        self._start("terminal-order")
        self.service.append_activity(
            "terminal-order", "tool_activity", now=3.0,
            details={"tool": "Read", "action": "read", "path": "src/order.py", "status": "completed"},
        )
        self._complete("terminal-order")
        # A late safe durable observation may race with finalization. It must
        # remain visible but never appear after the terminal lifecycle markers.
        completed_task = self.service.get_task("terminal-order")
        assert completed_task is not None and completed_task.finished_at is not None
        self.service.append_activity(
            "terminal-order", "status", now=float(completed_task.finished_at) + 10.0,
            details={"phase": "cleanup", "status": "completed", "summary": "Cleanup observed"},
        )

        detail = VoyageAgentProjection(self.service, AgentObservationStore()).detail("terminal-order")
        kinds = [item["kind"] for item in detail["timeline"]]
        self.assertIn("tool_activity", kinds)
        self.assertIn("status", kinds)
        self.assertEqual(kinds[-2:], ["final_response", "agent_completed"])


    def test_detail_presentation_group_and_explicit_ids_share_durable_terminal_activity(self) -> None:
        for task_id in ("group-a", "group-b"):
            self._create(task_id)
            self._start(task_id)
            self.service.append_activity(
                task_id,
                "tool_activity",
                details={"tool": "Read", "action": "read", "path": f"src/{task_id}.py", "status": "completed"},
            )
            self._complete(task_id)

        reopened = TaskService(Database(self.db_path))
        reopened.db.initialize()
        projection = VoyageAgentProjection(reopened, AgentObservationStore())

        detail = projection.detail("group-a")
        presentation = projection.group(presentation_group_id="pg-runtime")
        explicit = projection.group(task_ids=["group-a", "group-b"])

        self.assertTrue(any(item.get("kind") == "tool_activity" for item in detail["timeline"]))
        for result in (presentation, explicit):
            self.assertEqual(result["task_ids"], ["group-a", "group-b"])
            for child in result["tasks"]:
                kinds = [item["kind"] for item in child["timeline"]]
                self.assertIn("tool_activity", kinds)
                self.assertEqual(kinds[-2:], ["final_response", "agent_completed"])


if __name__ == "__main__":
    unittest.main()
