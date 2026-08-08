from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent_runtime.domain.attempt import Attempt
from agent_runtime.domain.enums import (
    BackendKind,
    EventType,
    TaskRoute,
    TaskStatus,
)
from agent_runtime.domain.event import TaskEvent
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.errors import TaskVersionConflictError
from agent_runtime.persistence.idempotency_repository import (
    ClaimOutcome,
    IdempotencyRepository,
)
from agent_runtime.application.task_service import (
    TaskService,
    build_session_metadata,
    parse_session_metadata,
)


def make_task(task_id: str = "wb-1", status: str = TaskStatus.QUEUED.value) -> Task:
    return Task(
        task_id=task_id,
        task_type="workbuddy",
        status=status,
        route=TaskRoute.GATEWAY.value,
        created_at=100.0,
        updated_at=100.0,
    )


def make_session(task_id: str = "wb-1") -> Session:
    return Session(
        session_id="rs-1",
        task_id=task_id,
        backend=BackendKind.WORKBUDDY.value,
        route=TaskRoute.GATEWAY.value,
        created_at=100.0,
        updated_at=100.0,
    )


class RepositoryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "runtime.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TaskRepositoryTests(RepositoryTestBase):
    def test_create_and_get_roundtrip(self) -> None:
        repo = TaskService(self.db)
        result = repo.create_task(
            task=make_task(),
            session=make_session(),
            metadata={"cwd": "/tmp", "model": "hy3"},
            idempotency_key="",
            request_fingerprint="fp",
            now=100.0,
        )
        self.assertEqual(result.outcome, "created")
        loaded = repo.get_task("wb-1")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, TaskStatus.QUEUED.value)
        self.assertEqual(loaded.version, 1)
        self.assertEqual(loaded.route, TaskRoute.GATEWAY.value)
        self.assertFalse(loaded.result_available)
        session = repo.get_session("wb-1")
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.metadata_json, build_session_metadata(
            {"cwd": "/tmp", "model": "hy3"}
        ))

    def test_unknown_task_returns_none(self) -> None:
        repo = TaskService(self.db)
        self.assertIsNone(repo.get_task("wb-missing"))

    def test_update_status_appends_event_and_bumps_version(self) -> None:
        repo = TaskService(self.db)
        repo.create_task(
            task=make_task(),
            session=make_session(),
            metadata={},
            idempotency_key="",
            request_fingerprint="fp",
            now=100.0,
        )
        repo.update_status(
            "wb-1",
            status=TaskStatus.RUNNING.value,
            event_type=EventType.TASK_STARTED.value,
            version=1,
            now=101.0,
            started_at=101.0,
        )
        loaded = repo.get_task("wb-1")
        assert loaded is not None
        self.assertEqual(loaded.status, TaskStatus.RUNNING.value)
        self.assertEqual(loaded.version, 2)
        self.assertEqual(loaded.started_at, 101.0)
        events = repo.get_events("wb-1")
        self.assertEqual(
            [event.event_type for event in events],
            [EventType.TASK_CREATED.value, EventType.TASK_STARTED.value],
        )

    def test_update_status_with_stale_version_conflicts(self) -> None:
        repo = TaskService(self.db)
        repo.create_task(
            task=make_task(),
            session=make_session(),
            metadata={},
            idempotency_key="",
            request_fingerprint="fp",
            now=100.0,
        )
        repo.update_status(
            "wb-1",
            status=TaskStatus.RUNNING.value,
            event_type=EventType.TASK_STARTED.value,
            version=1,
            now=101.0,
        )
        with self.assertRaises(TaskVersionConflictError):
            repo.update_status(
                "wb-1",
                status=TaskStatus.COMPLETED.value,
                event_type=EventType.TASK_COMPLETED.value,
                version=1,  # stale
                now=102.0,
            )
        loaded = repo.get_task("wb-1")
        assert loaded is not None
        self.assertEqual(loaded.status, TaskStatus.RUNNING.value)
        self.assertEqual(loaded.version, 2)

    def test_cancel_requested_is_idempotent(self) -> None:
        repo = TaskService(self.db)
        repo.create_task(
            task=make_task(),
            session=make_session(),
            metadata={},
            idempotency_key="",
            request_fingerprint="fp",
            now=100.0,
        )
        repo.mark_cancel_requested("wb-1", now=110.0)
        repo.mark_cancel_requested("wb-1", now=111.0)
        loaded = repo.get_task("wb-1")
        assert loaded is not None
        self.assertEqual(loaded.cancel_requested_at, 110.0)
        events = repo.get_events("wb-1")
        self.assertEqual(
            sum(1 for e in events if e.event_type == EventType.CANCEL_REQUESTED.value),
            1,
        )

    def test_cancel_confirmed_records_status_and_event(self) -> None:
        repo = TaskService(self.db)
        repo.create_task(
            task=make_task(),
            session=make_session(),
            metadata={},
            idempotency_key="",
            request_fingerprint="fp",
            now=100.0,
        )
        repo.mark_cancel_confirmed(
            "wb-1",
            status=TaskStatus.CANCELLED.value,
            version=1,
            now=120.0,
        )
        loaded = repo.get_task("wb-1")
        assert loaded is not None
        self.assertEqual(loaded.status, TaskStatus.CANCELLED.value)
        # PR3.3: timestamps use the database clock at write time (the ``now``
        # argument is accepted for compatibility but never used), so the
        # confirmed moment is at/after the requested instant and shared by
        # both the confirmation and the finished timestamps.
        self.assertIsNotNone(loaded.cancel_confirmed_at)
        self.assertGreaterEqual(loaded.cancel_confirmed_at, 120.0)
        self.assertEqual(loaded.finished_at, loaded.cancel_confirmed_at)
        types = [e.event_type for e in repo.get_events("wb-1")]
        self.assertIn(EventType.CANCEL_CONFIRMED.value, types)

    def test_save_result_persists_payload(self) -> None:
        repo = TaskService(self.db)
        repo.create_task(
            task=make_task(),
            session=make_session(),
            metadata={},
            idempotency_key="",
            request_fingerprint="fp",
            now=100.0,
        )
        payload = {
            "backend": "gateway_runs",
            "stopReason": "end_turn",
            "answer": "delegated material",
        }
        repo.save_result(
            "wb-1",
            result=payload,
            status=TaskStatus.COMPLETED.value,
            version=1,
            now=130.0,
        )
        loaded = repo.get_task("wb-1")
        assert loaded is not None
        self.assertTrue(loaded.result_available)
        self.assertEqual(json.loads(loaded.result_json or "{}"), payload)
        self.assertEqual(loaded.status, TaskStatus.COMPLETED.value)
        self.assertEqual(loaded.version, 3)
        types = [e.event_type for e in repo.get_events("wb-1")]
        self.assertIn(EventType.RESULT_AVAILABLE.value, types)

    def test_attempt_row_created_and_updated(self) -> None:
        repo = TaskService(self.db)
        repo.create_task(
            task=make_task(),
            session=make_session(),
            metadata={},
            idempotency_key="",
            request_fingerprint="fp",
            now=100.0,
        )
        attempts = repo.tasks.get_attempts("wb-1")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].attempt_no, 1)
        self.assertEqual(attempts[0].status, TaskStatus.QUEUED.value)
        with self.db.transaction() as connection:
            repo.tasks.update_attempt_status(
                connection,
                "wb-1",
                attempt_no=1,
                status=TaskStatus.RUNNING.value,
                started_at=101.0,
            )
        attempts = repo.tasks.get_attempts("wb-1")
        self.assertEqual(attempts[0].status, TaskStatus.RUNNING.value)


class IdempotencyRepositoryTests(RepositoryTestBase):
    def _seed_task(self, task_id: str) -> None:
        """Idempotency rows carry a task FK; create the referenced row first."""
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at)"
                " VALUES (?, 'workbuddy', 'queued', 'gateway', 100.0, 100.0)",
                (task_id,),
            )

    def test_claim_creates_then_replays(self) -> None:
        repo = IdempotencyRepository(self.db)
        self._seed_task("wb-1")
        self._seed_task("wb-2")
        with self.db.transaction() as connection:
            first = repo.claim(
                connection,
                idempotency_key="key-1",
                request_fingerprint="fp-a",
                task_id="wb-1",
                created_at=100.0,
            )
            self.assertEqual(first.outcome, ClaimOutcome.CREATED)
        with self.db.transaction() as connection:
            second = repo.claim(
                connection,
                idempotency_key="key-1",
                request_fingerprint="fp-a",
                task_id="wb-2",
                created_at=101.0,
            )
        self.assertEqual(second.outcome, ClaimOutcome.REPLAY)
        self.assertEqual(second.task_id, "wb-1")

    def test_claim_conflicts_on_different_fingerprint(self) -> None:
        repo = IdempotencyRepository(self.db)
        self._seed_task("wb-1")
        self._seed_task("wb-2")
        with self.db.transaction() as connection:
            repo.claim(
                connection,
                idempotency_key="key-1",
                request_fingerprint="fp-a",
                task_id="wb-1",
                created_at=100.0,
            )
        with self.db.transaction() as connection:
            conflict = repo.claim(
                connection,
                idempotency_key="key-1",
                request_fingerprint="fp-b",
                task_id="wb-2",
                created_at=101.0,
            )
        self.assertEqual(conflict.outcome, ClaimOutcome.CONFLICT)
        self.assertEqual(conflict.task_id, "wb-1")

    def test_empty_key_is_no_key(self) -> None:
        repo = IdempotencyRepository(self.db)
        self._seed_task("wb-1")
        with self.db.transaction() as connection:
            result = repo.claim(
                connection,
                idempotency_key="   ",
                request_fingerprint="fp",
                task_id="wb-1",
                created_at=100.0,
            )
        self.assertEqual(result.outcome, ClaimOutcome.NO_KEY)

    def test_get_by_key_roundtrip(self) -> None:
        repo = IdempotencyRepository(self.db)
        self._seed_task("wb-1")
        with self.db.transaction() as connection:
            repo.claim(
                connection,
                idempotency_key="key-1",
                request_fingerprint="fp-a",
                task_id="wb-1",
                created_at=100.0,
            )
        self.assertEqual(repo.get_by_key("key-1"), ("fp-a", "wb-1"))
        self.assertIsNone(repo.get_by_key("missing"))

    def test_concurrent_claims_create_exactly_one_task(self) -> None:
        """Database atomicity, not process locks, must prevent duplicates."""
        repo = IdempotencyRepository(self.db)
        outcomes: list[ClaimOutcome] = []
        outcomes_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def claim_once(index: int) -> None:
            self._seed_task(f"wb-race-{index}")
            barrier.wait()
            with self.db.transaction() as connection:
                result = repo.claim(
                    connection,
                    idempotency_key="race-key",
                    request_fingerprint="fp-race",
                    task_id=f"wb-race-{index}",
                    created_at=time.time(),
                )
            with outcomes_lock:
                outcomes.append(result.outcome)

        threads = [
            threading.Thread(target=claim_once, args=(index,)) for index in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(outcomes.count(ClaimOutcome.CREATED), 1)
        self.assertEqual(outcomes.count(ClaimOutcome.REPLAY), 7)
        self.assertEqual(outcomes.count(ClaimOutcome.CONFLICT), 0)


class TaskServiceCreateTransactionTests(RepositoryTestBase):
    def test_creation_transaction_rolls_back_on_event_failure(self) -> None:
        """A failure inside the creation transaction must not leave rows behind."""
        service = TaskService(self.db)
        duplicate_event_id = "ev-duplicate"

        original_append = service.events.append

        def failing_append(connection, event) -> int:
            original_append(connection, event)
            # Force a second identical insert to violate UNIQUE(event_id).
            return original_append(connection, event)

        service.events.append = failing_append  # type: ignore[method-assign]
        with self.assertRaises(Exception):
            service.create_task(
                task=make_task(task_id="wb-tx"),
                session=make_session(task_id="wb-tx"),
                metadata={},
                idempotency_key="tx-key",
                request_fingerprint="fp",
                now=100.0,
            )
        # Nothing persisted: task, session, events, idempotency all absent.
        self.assertIsNone(service.get_task("wb-tx"))
        with self.db.connect() as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("tasks", "sessions", "events", "attempts", "idempotency")
            }
        self.assertEqual(counts, {
            "tasks": 0, "sessions": 0, "events": 0, "attempts": 0, "idempotency": 0,
        })

    def test_metadata_is_allow_listed(self) -> None:
        metadata = {
            "cwd": "/tmp",
            "model": "hy3",
            "prompt": "SECRET PROMPT",
            "answer": "SECRET ANSWER",
            "secret": "shh",
            "identity": "S1",
        }
        serialized = build_session_metadata(metadata)
        self.assertNotIn("SECRET PROMPT", serialized)
        self.assertNotIn("SECRET ANSWER", serialized)
        self.assertNotIn("shh", serialized)
        parsed = parse_session_metadata(serialized)
        self.assertEqual(parsed, {"cwd": "/tmp", "model": "hy3", "identity": "S1"})

    def test_replayed_or_conflict_do_not_persist(self) -> None:
        service = TaskService(self.db)
        service.create_task(
            task=make_task(task_id="wb-1"),
            session=make_session(task_id="wb-1"),
            metadata={},
            idempotency_key="key-1",
            request_fingerprint="fp-a",
            now=100.0,
        )
        replayed = service.create_task(
            task=make_task(task_id="wb-2"),
            session=make_session(task_id="wb-2"),
            metadata={},
            idempotency_key="key-1",
            request_fingerprint="fp-a",
            now=101.0,
        )
        self.assertEqual(replayed.outcome, "replayed")
        self.assertEqual(replayed.task_id, "wb-1")
        self.assertIsNone(service.get_task("wb-2"))


if __name__ == "__main__":
    unittest.main()
