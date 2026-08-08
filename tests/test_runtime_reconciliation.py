"""Current TP-Voyager restart reconciliation contract tests."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from agent_runtime.application.reconciliation_service import ReconciliationService
from agent_runtime.application.task_service import TaskService
from agent_runtime.backends.base import BackendReconcileResult
from agent_runtime.backends.errors import BackendUnavailableError
from agent_runtime.backends.fake import FakeBackend
from agent_runtime.domain.ids import new_runtime_session_id, new_task_id
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database
from agent_runtime.runtime.lease import LeaseService


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()
        self.service = TaskService(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _task(
        self,
        *,
        backend: str = "qoder",
        route: str = "acp_read_only",
        backend_session_id: str | None = "session-1",
    ) -> str:
        task_id = new_task_id()
        session_id = new_runtime_session_id()
        now = time.time()
        self.service.create_task(
            task=Task(
                task_id=task_id,
                task_type=backend,
                status="queued",
                route=route,
                created_at=now,
                updated_at=now,
                session_id=session_id,
            ),
            session=Session(
                session_id=session_id,
                task_id=task_id,
                backend=backend,
                route=route,
                created_at=now,
                updated_at=now,
                backend_session_id=backend_session_id,
            ),
            metadata={"cwd": str(self.root), "model": ""},
            idempotency_key="",
            request_fingerprint=f"fp-{task_id}",
            now=now,
        )
        return task_id

    def test_never_dispatched_is_explicit_failure_without_dispatch(self) -> None:
        task_id = self._task(backend_session_id=None)
        backend = FakeBackend()
        report = ReconciliationService(self.db).reconcile_all(backend)[0]
        self.assertEqual(report.outcome, "failed")
        durable = self.service.get_task(task_id)
        self.assertEqual(durable.status, "failed")
        self.assertEqual(durable.error_code, "NeverDispatched")
        self.assertEqual(backend.starts, [])
        self.assertEqual(backend.resumes, [])

    def test_unknown_and_orphaned_preserve_distinct_durable_truth(self) -> None:
        for outcome, expected in (("unknown", "lost"), ("orphaned", "orphaned")):
            with self.subTest(outcome=outcome):
                task_id = self._task(backend_session_id=f"sess-{outcome}")
                backend = FakeBackend(
                    reconcile_result=BackendReconcileResult(outcome=outcome)
                )
                report = ReconciliationService(self.db).reconcile_all(backend)[0]
                self.assertEqual(report.outcome, expected)
                self.assertEqual(self.service.get_task(task_id).status, expected)
                self.assertEqual(backend.starts, [])
                self.assertEqual(backend.resumes, [])

    def test_historical_removed_backend_reconciles_to_lost_without_substitution(self) -> None:
        task_id = self._task(
            backend="workbuddy",
            route="gateway",
            backend_session_id="historical-session",
        )
        resolver_calls: list[str] = []

        def resolver(name: str):
            resolver_calls.append(name)
            raise BackendUnavailableError(f"unsupported historical backend: {name}")

        report = ReconciliationService(self.db).reconcile_all(resolver)[0]
        self.assertEqual(resolver_calls, ["workbuddy"])
        self.assertEqual(report.outcome, "lost")
        durable = self.service.get_task(task_id)
        self.assertEqual(durable.status, "lost")
        self.assertIn("backend unavailable", durable.error_message or "")

    def test_live_owner_is_skipped_before_backend_classification(self) -> None:
        task_id = self._task()
        holder = LeaseService(self.db, instance_id="live-worker")
        self.assertIsNotNone(holder.acquire(task_id))
        backend = FakeBackend(
            reconcile_result=BackendReconcileResult(outcome="unknown")
        )
        report = ReconciliationService(self.db).reconcile_all(backend)[0]
        self.assertEqual(report.outcome, "skipped")
        self.assertEqual(self.service.get_task(task_id).status, "queued")
        self.assertEqual(backend.reconcile_calls, [])


if __name__ == "__main__":
    unittest.main()
