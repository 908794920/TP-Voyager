from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_runtime.api import mcp_server as server
from agent_runtime.application.task_service import TaskService
from agent_runtime.domain.run_control import RunControlSpec
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task


class V105ServerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "runtime.db"
        database = server.configure_runtime_database(self.db_path)
        assert database is not None
        self.tasks = TaskService(database)
        server.TASKS.clear()
        server.IDEMPOTENCY_TASKS.clear()

    def tearDown(self) -> None:
        server.TASKS.clear()
        server.IDEMPOTENCY_TASKS.clear()
        server.configure_runtime_database(None)
        self.tmp.cleanup()

    def _dispatch_qoder(self, *, run_id: str, step_key: str, max_dispatches: int) -> dict:
        return server.task_dispatch(
            objective="Inspect the bounded fixture without modifying it",
            crew="qoder",
            task_kind="research",
            cwd=self.tmp.name,
            model="Lite",
            idempotency_key=f"{run_id}-{step_key}",
            run_control={
                "run_id": run_id,
                "max_dispatches": max_dispatches,
                "max_runtime_seconds": 120,
            },
            step_key=step_key,
            timeout_seconds=30,
        )

    def test_task_dispatch_projects_run_dispatch_budget_reason_code(self) -> None:
        with patch.object(server, "_start_worker_thread", return_value=None):
            first = self._dispatch_qoder(run_id="run-mcp-limit", step_key="s1", max_dispatches=1)
            rejected = self._dispatch_qoder(run_id="run-mcp-limit", step_key="s2", max_dispatches=1)
        self.assertTrue(first.get("ok"), first)
        self.assertFalse(rejected.get("ok"), rejected)
        self.assertEqual(rejected.get("reason_code"), "RUN_DISPATCH_BUDGET_EXCEEDED")
        self.assertEqual(rejected.get("run_id"), "run-mcp-limit")
        self.assertEqual(rejected.get("step_key"), "s2")

    def test_task_dispatch_projects_budget_relaxation_reason_code(self) -> None:
        with patch.object(server, "_start_worker_thread", return_value=None):
            first = self._dispatch_qoder(run_id="run-mcp-widen", step_key="s1", max_dispatches=1)
            rejected = self._dispatch_qoder(run_id="run-mcp-widen", step_key="s2", max_dispatches=2)
        self.assertTrue(first.get("ok"), first)
        self.assertFalse(rejected.get("ok"), rejected)
        self.assertEqual(rejected.get("reason_code"), "RUN_BUDGET_RELAXATION_REJECTED")
        self.assertEqual(rejected.get("run_id"), "run-mcp-widen")
        self.assertEqual(rejected.get("step_key"), "s2")

    def test_qoder_file_access_evidence_keeps_only_content_free_metadata(self) -> None:
        task = server.TaskState(task_id="wb-file-evidence", prompt="secret prompt", cwd=self.tmp.name)
        evidence = server._observability_evidence(task, "at-file-evidence", {
            "file_access_events": [{
                "path": "fixture.txt",
                "operation": "read_scope_grant",
                "allowed": True,
                "reason": "captain_read_scope",
                "sha256": "a" * 64,
                "timestamp": 1.0,
                "content": "secret file content",
                "prompt": "secret prompt",
                "thinking": "secret thought",
                "credential": "secret credential",
            }],
        })
        self.assertEqual(len(evidence), 1)
        detail = json.loads(evidence[0].detail_json)
        self.assertEqual(detail, {
            "allowed": True,
            "operation": "read_scope_grant",
            "path": "fixture.txt",
            "reason": "captain_read_scope",
            "sha256": "a" * 64,
            "timestamp": 1.0,
        })
        serialized = json.dumps(detail, sort_keys=True)
        for secret in ("secret file content", "secret prompt", "secret thought", "secret credential"):
            self.assertNotIn(secret, serialized)

    def test_task_result_can_recover_by_run_id_and_step_key(self) -> None:
        now = time.time()
        task = Task(
            task_id="wb-recover-v105", task_type="codebuddy", status="queued",
            route="sdk_context_read_only", created_at=now, updated_at=now,
            run_id="run-recover-v105", step_key="research-01",
        )
        session = Session(
            session_id="sess-recover-v105", task_id=task.task_id, backend="codebuddy",
            route=task.route, created_at=now, updated_at=now,
        )
        result = self.tasks.create_task(
            task=task, session=session,
            metadata={"max_task_duration_seconds": 30.0},
            idempotency_key="recover-v105", request_fingerprint="recover-v105-fp",
            run_control=RunControlSpec("run-recover-v105", 3, 120.0),
            requested_runtime_seconds=30.0, now=now,
        )
        self.assertEqual(result.outcome, "created")
        recovered = server.task_result(run_id="run-recover-v105", step_key="research-01")
        self.assertEqual(recovered.get("task_id"), "wb-recover-v105")
        self.assertEqual(recovered.get("provenance", {}).get("run_id"), "run-recover-v105")
        self.assertEqual(recovered.get("provenance", {}).get("step_key"), "research-01")
        missing = server.task_result(run_id="run-recover-v105", step_key="missing")
        self.assertFalse(missing.get("ok"))

    def test_task_result_rejects_ambiguous_lookup(self) -> None:
        result = server.task_result(task_id="wb-x", run_id="run-x", step_key="s1")
        self.assertFalse(result.get("ok"))
        self.assertIn("not both", str(result.get("error")))


if __name__ == "__main__":
    unittest.main()
