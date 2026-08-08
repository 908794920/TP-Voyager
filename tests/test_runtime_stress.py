"""TP-Voyager lease, cancel and reconciliation stress tests.

Covers every finding from the PR3.1 final review:
- an expired lease can never be renewed back to validity (fencing
  monotonicity);
- an expired same-owner re-acquire ALWAYS bumps the generation, so an old
  LeaseInfo can never become valid again;
- a persisted cancel request is fenced on the live handle's lease: a stale
  worker whose lease was taken over is refused with ok=false and no backend
  cancel is sent;
- reconciliation converges on non-terminal version conflicts with bounded
  retries (never stuck in cancelling);
- subagent_cancel respects a durable terminal outcome returned by the
  cancel transaction (never revives the handle, never cancels);
- heartbeat exceptions enter the unified lease-lost handling (on_error);
- deterministic race scenarios A/B/C run 50 consecutive rounds each with
  zero failures.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime import server
from agent_runtime.backends.base import (
    BackendActivity,
    BackendReconcileResult,
)
from agent_runtime.backends.fake import FakeBackend
from agent_runtime.domain.ids import new_runtime_session_id, new_task_id
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database
from agent_runtime.runtime.lease import LeaseHeartbeat, LeaseService
from agent_runtime.application.reconciliation_service import (
    ReconciliationService,
)
from agent_runtime.application.task_service import (
    CancelRequestResult,
    TaskService,
)

ROUNDS = 50


class RuntimeIsolationTests(unittest.TestCase):
    """Shared temp DB + handle cache reset for worker-thread tests."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cwd = Path(self._tmp.name) / "project"
        self.cwd.mkdir()
        self.db_path = Path(self._tmp.name) / "runtime.db"
        server.configure_runtime_database(self.db_path)
        server.TASKS.clear()
        server.IDEMPOTENCY_TASKS.clear()

    def tearDown(self) -> None:
        server.TASKS.clear()
        server.IDEMPOTENCY_TASKS.clear()
        server.configure_runtime_database(None)
        self._tmp.cleanup()

    def _create_task(
        self,
        service: TaskService,
        *,
        route: str = "acp",
        status: str = "queued",
        backend_session_id: str | None = None,
    ) -> str:
        task_id = new_task_id()
        rsid = new_runtime_session_id()
        now = time.time()
        service.create_task(
            task=Task(
                task_id=task_id, task_type="qoder", status=status,
                route=route, created_at=now, updated_at=now, session_id=rsid,
            ),
            session=Session(
                session_id=rsid, task_id=task_id, backend="qoder",
                route=route, created_at=now, updated_at=now,
                backend_session_id=backend_session_id,
            ),
            metadata={"cwd": str(self.cwd), "model": "hy3"},
            idempotency_key="", request_fingerprint="fp", now=now,
        )
        return task_id


# ---------------------------------------------------------------------------
# 1. Expired lease can never be renewed back to validity
# ---------------------------------------------------------------------------


class LeaseExpiryTests(RuntimeIsolationTests):
    """renew must refuse an expired lease; the old LeaseInfo stays dead."""

    def test_expired_lease_renew_refused_and_stays_invalid(self) -> None:
        db = Database(self.db_path)
        service = TaskService(db)
        for _round in range(ROUNDS):
            task_id = self._create_task(service)
            lease_svc = LeaseService(
                db, instance_id="worker", lease_duration_seconds=0.1,
            )
            lease = lease_svc.acquire(task_id)
            assert lease is not None
            time.sleep(0.15)  # lease expires without renewal
            self.assertFalse(lease_svc.ensure(task_id, lease), _round)
            self.assertFalse(lease_svc.renew(task_id, lease), _round)
            # Fencing monotonicity: once invalid, always invalid.
            self.assertFalse(lease_svc.ensure(task_id, lease), _round)


# ---------------------------------------------------------------------------
# 2. Expired same-owner re-acquire bumps the generation
# ---------------------------------------------------------------------------


class SameOwnerReacquireTests(RuntimeIsolationTests):
    """Re-acquiring an expired lease must invalidate the old LeaseInfo."""

    def test_expired_same_owner_reacquire_bumps_generation(self) -> None:
        db = Database(self.db_path)
        service = TaskService(db)
        for _round in range(20):
            task_id = self._create_task(service)
            # 1.0s lease with a 1.1x sleep: the post-acquire ensure() must
            # never race the deadline even under GC/scheduler pauses.
            lease_svc = LeaseService(
                db, instance_id="same-inst", lease_duration_seconds=1.0,
            )
            old_lease = lease_svc.acquire(task_id)
            assert old_lease is not None
            self.assertEqual(old_lease.generation, 1, _round)
            time.sleep(1.1)  # lease expires
            new_lease = lease_svc.acquire(task_id)
            assert new_lease is not None
            # Same instance, expired lease: generation MUST bump.
            self.assertEqual(new_lease.generation, 2, _round)
            # Verify the fresh lease FIRST (minimal gap after acquire), then
            # prove the old handle is permanently dead.
            self.assertTrue(lease_svc.ensure(task_id, new_lease), _round)
            self.assertFalse(lease_svc.ensure(task_id, old_lease), _round)
            self.assertFalse(lease_svc.renew(task_id, old_lease), _round)

    def test_live_same_owner_acquire_is_already_owned(self) -> None:
        """PR3.3: a live same-owner acquire returns None (AlreadyOwned)."""
        db = Database(self.db_path)
        service = TaskService(db)
        task_id = self._create_task(service)
        lease_svc = LeaseService(db, instance_id="same-inst")
        first = lease_svc.acquire(task_id)
        assert first is not None
        second = lease_svc.acquire(task_id)
        self.assertIsNone(second)  # AlreadyOwned: no second valid LeaseInfo
        self.assertTrue(lease_svc.ensure(task_id, first))
        self.assertEqual(first.generation, 1)


# ---------------------------------------------------------------------------
# 3. Deterministic race scenarios (50 rounds each)
# ---------------------------------------------------------------------------


class DeterministicRaceTests(RuntimeIsolationTests):
    """Scenario A/B/C from the PR3.1 review, run ROUNDS times each."""

    # ---------------------------------------------------------- scenario A

    def test_scenario_a_stale_cancel_rejected_after_takeover(self) -> None:
        """Reconciler owns the lease -> stale worker cancel is refused."""
        db = Database(self.db_path)
        service = TaskService(db)
        for _round in range(ROUNDS):
            task_id = self._create_task(service, backend_session_id="sess-a")
            # Old worker lease expires.
            old_worker = LeaseService(
                db, instance_id="old-worker", lease_duration_seconds=0.1,
            )
            old_lease = old_worker.acquire(task_id)
            assert old_lease is not None
            time.sleep(0.15)
            # Reconciler takes over and pauses right before classification.
            entered = threading.Event()
            release = threading.Event()

            def gated_reconcile(request):
                entered.set()
                release.wait(timeout=30)
                return BackendReconcileResult(outcome="unknown")

            backend = FakeBackend()
            backend.reconcile = gated_reconcile  # type: ignore[method-assign]
            svc = ReconciliationService(db, instance_id="reconciler")
            snapshot = service.get_task(task_id)
            assert snapshot is not None
            reports: list = []
            thread = threading.Thread(
                target=lambda: reports.append(svc._reconcile_one(snapshot, backend)),
                daemon=True,
            )
            thread.start()
            self.assertTrue(
                entered.wait(timeout=30),
                f"round {_round}: reconciler never reached classification",
            )
            # Stale worker attempts a user cancel: must be refused by the
            # lease fence, with no backend cancel and no durable write.
            handle = server.TaskState(
                task_id=task_id, prompt="p", cwd=str(self.cwd),
                runtime="qoder", route="acp_read_only", persisted=True, version=1,
            )
            handle.lease = old_lease
            server.TASKS[task_id] = handle
            prod_backend = FakeBackend()
            with patch(
                "agent_runtime.server._create_qoder_backend",
                return_value=prod_backend,
            ):
                response = server.subagent_cancel(task_id)
            self.assertFalse(response["ok"], f"round {_round}")
            self.assertIn("lease was lost", response["error"], f"round {_round}")
            self.assertEqual(prod_backend.cancels, [], f"round {_round}")
            durable = service.get_task(task_id)
            assert durable is not None
            self.assertEqual(durable.status, "queued", f"round {_round}")
            self.assertIsNone(durable.cancel_requested_at, f"round {_round}")
            # Reconciler completes its classification -> LOST.
            release.set()
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive(), f"round {_round}")
            self.assertEqual(reports[0].outcome, "lost", f"round {_round}")
            durable = service.get_task(task_id)
            assert durable is not None
            self.assertEqual(durable.status, "lost", f"round {_round}")
            self.assertIsNotNone(durable.lost_at, f"round {_round}")
            server.TASKS.pop(task_id, None)

    # ---------------------------------------------------------- scenario B

    def test_scenario_b_live_cancel_wins_and_reconcile_skips(self) -> None:
        """A live worker's cancel commits; reconciliation must skip it."""
        db = Database(self.db_path)
        service = TaskService(db)
        for _round in range(ROUNDS):
            task_id = self._create_task(service, backend_session_id="sess-b")
            worker = LeaseService(db, instance_id="live-worker")
            lease = worker.acquire(task_id)
            assert lease is not None
            handle = server.TaskState(
                task_id=task_id, prompt="p", cwd=str(self.cwd),
                runtime="qoder", route="acp_read_only", persisted=True, version=1,
            )
            handle.lease = lease
            server.TASKS[task_id] = handle
            prod_backend = FakeBackend()
            with patch(
                "agent_runtime.server._create_qoder_backend",
                return_value=prod_backend,
            ):
                response = server.subagent_cancel(task_id)
            self.assertTrue(response["ok"], f"round {_round}")
            durable = service.get_task(task_id)
            assert durable is not None
            self.assertEqual(durable.status, "cancelling", f"round {_round}")
            self.assertIsNotNone(durable.cancel_requested_at, f"round {_round}")
            # The live owner still holds the lease: reconciliation skips.
            backend = FakeBackend(
                reconcile_result=BackendReconcileResult(outcome="unknown"),
            )
            reports = ReconciliationService(db, instance_id="reconciler").reconcile_all(
                backend,
            )
            self.assertEqual(reports[0].outcome, "skipped", f"round {_round}")
            durable = service.get_task(task_id)
            assert durable is not None
            self.assertEqual(durable.status, "cancelling", f"round {_round}")
            server.TASKS.pop(task_id, None)

    # ---------------------------------------------------------- scenario C

    def test_scenario_c_conflict_bounded_retry_converges_lost(self) -> None:
        """Unknown classification + concurrent version bump -> LOST via retry."""
        db = Database(self.db_path)
        service = TaskService(db)
        for _round in range(ROUNDS):
            task_id = self._create_task(service, backend_session_id="sess-c")
            svc = ReconciliationService(db, instance_id="reconciler")
            backend = FakeBackend(
                reconcile_result=BackendReconcileResult(outcome="unknown"),
            )
            original_writer = svc._mark_lost_or_orphaned
            writer_calls = {"n": 0}
            cancel_issued = {"n": 0}

            def fenced_writer(task, lease, kind, error):
                writer_calls["n"] += 1
                if cancel_issued["n"] == 0:
                    cancel_issued["n"] += 1
                    # A concurrent cancel bumps the version mid-pass.
                    service.request_cancel(task_id, cancel_scope="test")
                return original_writer(task, lease, kind, error)

            snapshot = service.get_task(task_id)
            assert snapshot is not None
            with patch.object(svc, "_mark_lost_or_orphaned", fenced_writer):
                report = svc._reconcile_one(snapshot, backend)
            self.assertEqual(report.outcome, "lost", f"round {_round}")
            self.assertEqual(
                writer_calls["n"], 2,
                f"round {_round}: one conflict + one retry write",
            )
            self.assertEqual(cancel_issued["n"], 1, f"round {_round}")
            durable = service.get_task(task_id)
            assert durable is not None
            self.assertEqual(durable.status, "lost", f"round {_round}")
            self.assertIsNotNone(durable.lost_at, f"round {_round}")
            # The task must never be left in cancelling.
            self.assertNotEqual(durable.status, "cancelling", f"round {_round}")

    # ---------------------------------------------------- retry exhaustion

    def test_conflict_retry_exhausts_to_error_not_infinite(self) -> None:
        db = Database(self.db_path)
        service = TaskService(db)
        task_id = self._create_task(service, backend_session_id="sess-exh")
        svc = ReconciliationService(db, instance_id="reconciler")
        calls = {"n": 0}

        def always_conflict(_task, _lease, _kind, _error):
            calls["n"] += 1
            return "conflict"

        snapshot = service.get_task(task_id)
        assert snapshot is not None
        with patch.object(svc, "_mark_lost_or_orphaned", always_conflict):
            report = svc._reconcile_one(
                snapshot, FakeBackend(
                    reconcile_result=BackendReconcileResult(outcome="unknown"),
                ),
            )
        self.assertEqual(report.outcome, "error")
        self.assertIn("bounded retries", report.detail)
        self.assertEqual(calls["n"], 3)  # exactly the retry limit, no loop


# ---------------------------------------------------------------------------
# 4. Public cancel respects the durable terminal outcome (H1)
# ---------------------------------------------------------------------------


class PublicCancelTerminalRaceTests(RuntimeIsolationTests):
    """request_cancel returning a terminal status must stop the cancel."""

    def test_cancel_refuses_when_transaction_returns_terminal(self) -> None:
        db = Database(self.db_path)
        service = TaskService(db)
        task_id = self._create_task(service, backend_session_id="sess-h1")
        handle = server.TaskState(
            task_id=task_id, prompt="p", cwd=str(self.cwd),
            runtime="qoder", route="acp_read_only", persisted=True, version=1,
        )
        server.TASKS[task_id] = handle
        prod_backend = FakeBackend()
        with (
            patch.object(
                TaskService, "request_cancel",
                return_value=CancelRequestResult(
                    created=False, version=2, status="lost",
                ),
            ),
            patch(
                "agent_runtime.server._create_qoder_backend",
                return_value=prod_backend,
            ),
        ):
            response = server.subagent_cancel(task_id)
        self.assertFalse(response["ok"])
        self.assertEqual(response["state"], "lost")
        self.assertIn("already finished", response["error"])
        # Never revived the handle, never sent a backend cancel.
        self.assertEqual(handle.state, "queued")
        self.assertFalse(handle.cancel_requested)
        self.assertEqual(prod_backend.cancels, [])

    def test_cancel_with_stale_lease_never_touches_durable(self) -> None:
        db = Database(self.db_path)
        service = TaskService(db)
        task_id = self._create_task(service, backend_session_id="sess-h2")
        # Reconciler owns the lease (old worker lease is dead).
        worker = LeaseService(
            db, instance_id="old-worker", lease_duration_seconds=0.1,
        )
        old_lease = worker.acquire(task_id)
        assert old_lease is not None
        time.sleep(0.15)
        reconciler = LeaseService(db, instance_id="reconciler")
        assert reconciler.acquire(task_id) is not None
        handle = server.TaskState(
            task_id=task_id, prompt="p", cwd=str(self.cwd),
            runtime="qoder", route="acp_read_only", persisted=True, version=1,
        )
        handle.lease = old_lease
        server.TASKS[task_id] = handle
        prod_backend = FakeBackend()
        with patch(
            "agent_runtime.server._create_qoder_backend",
            return_value=prod_backend,
        ):
            response = server.subagent_cancel(task_id)
        self.assertFalse(response["ok"])
        self.assertIn("lease was lost", response["error"])
        self.assertEqual(prod_backend.cancels, [])
        durable = service.get_task(task_id)
        assert durable is not None
        self.assertEqual(durable.status, "queued")
        self.assertIsNone(durable.cancel_requested_at)


# ---------------------------------------------------------------------------
# 5. Heartbeat error enters the unified lease-lost handling (H2)
# ---------------------------------------------------------------------------


class HeartbeatErrorTests(RuntimeIsolationTests):
    """on_error records a safe diagnostic and runs the lost path."""

    def test_heartbeat_error_fires_on_error_with_safe_diagnostic(self) -> None:
        db = Database(self.db_path)
        service = TaskService(db)
        task_id = self._create_task(service, backend_session_id="sess-hb2")
        lease_svc = LeaseService(
            db, instance_id="worker", lease_duration_seconds=0.2,
        )
        lease = lease_svc.acquire(task_id)
        assert lease is not None
        lost_calls: list[str] = []
        error_calls: list[str] = []
        heartbeat = LeaseHeartbeat(
            lease_svc, task_id, lease, interval_seconds=0.05,
            on_lost=lambda: lost_calls.append("lost"),
            on_error=lambda err: error_calls.append(err),
        )
        with patch.object(
            lease_svc, "renew", side_effect=RuntimeError("boom"),
        ):
            heartbeat.start()
            time.sleep(0.25)
        heartbeat.stop()
        self.assertTrue(heartbeat.lost)
        self.assertEqual(error_calls, ["RuntimeError"])
        self.assertEqual(lost_calls, [])

    def test_server_heartbeat_on_error_marks_handle_and_cancels(self) -> None:
        class SlowFake(FakeBackend):
            """Fake backend that executes for 0.8s (heartbeat has time to fail)."""

            def start(self, request, callbacks):  # type: ignore[override]
                self.starts.append(request)
                callbacks.on_dispatch_accepted("fake-session")
                self.dispatch_accepted.append("fake-session")
                callbacks.on_activity(
                    BackendActivity(kind="prompt_accepted", timestamp=0.0)
                )
                time.sleep(0.8)
                if self._error:
                    raise self._error
                callbacks.on_result(self._result)
                return self._result

        db = Database(self.db_path)
        service = TaskService(db)
        task_id = self._create_task(service)
        handle = server.TaskState(
            task_id=task_id, prompt="p", cwd=str(self.cwd), model="hy3",
            runtime="qoder", route="acp_read_only", persisted=True, version=1,
        )
        server.TASKS[task_id] = handle
        # 0.3s lease -> heartbeat interval 0.1s: the first renew fails while
        # the slow backend is still executing.
        failing_lease = LeaseService(
            db, instance_id="worker", lease_duration_seconds=0.3,
        )
        prod_backend = SlowFake()
        with (
            patch.object(server, "_RUNTIME_LEASE", failing_lease),
            patch.object(failing_lease, "renew", side_effect=RuntimeError("boom")),
            patch(
                "agent_runtime.server._create_qoder_backend",
                return_value=prod_backend,
            ),
        ):
            thread = threading.Thread(
                target=server._run_qoder, args=(handle, 5.0), daemon=True,
            )
            thread.start()
            # Well past the first heartbeat cycle, before the backend ends:
            # the on_error path must have fired with a safe diagnostic and a
            # best-effort backend cancel.
            time.sleep(0.4)
            self.assertIn("lease heartbeat failed", handle.persist_error or "")
            self.assertNotIn("boom", handle.persist_error or "")
            self.assertGreaterEqual(len(prod_backend.cancels), 1)
            thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        # The expired lease also fences the late terminal write.
        self.assertIn("lease lost", handle.persist_error or "")


if __name__ == "__main__":
    unittest.main()
