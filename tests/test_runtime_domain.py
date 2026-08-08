from __future__ import annotations

import unittest

from agent_runtime.domain.attempt import Attempt
from agent_runtime.domain.enums import (
    EventType,
    EventVisibility,
    TaskRoute,
    TaskStatus,
    TERMINAL_STATUSES,
)
from agent_runtime.domain.event import TaskEvent
from agent_runtime.domain.ids import (
    new_attempt_id,
    new_event_id,
    new_runtime_session_id,
    new_task_id,
)
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.domain.timeutil import now_epoch


class EnumStabilityTests(unittest.TestCase):
    """One persisted enum contract test replaces five field-by-field duplicates."""

    def test_persisted_enum_contracts_are_stable(self) -> None:
        self.assertEqual(
            {status.value for status in TaskStatus},
            {
                "queued", "connecting", "running", "observing", "cancelling",
                "completed", "failed", "cancelled", "lost", "orphaned",
            },
        )
        self.assertEqual(
            TERMINAL_STATUSES,
            {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.LOST,
                TaskStatus.ORPHANED,
            },
        )
        self.assertEqual(
            {event.value for event in EventType},
            {
                "task_created", "task_started", "task_status_changed", "session_created",
                "backend_dispatch_requested", "backend_dispatch_accepted", "activity_observed",
                "cancel_requested", "cancel_confirmed", "task_completed", "task_failed",
                "result_available", "task_lost", "task_orphaned", "task_child_linked",
            },
        )
        self.assertEqual(TaskRoute.GATEWAY.value, "gateway")
        self.assertEqual(TaskRoute.ACP_RESUME.value, "acp_resume")
        self.assertEqual(EventVisibility.PUBLIC.value, "public")
        self.assertEqual(EventVisibility.INTERNAL.value, "internal")


class IdGenerationTests(unittest.TestCase):
    def test_ids_keep_stable_prefixes_shape_and_uniqueness(self) -> None:
        generators = (
            (new_task_id, "wb-"),
            (new_runtime_session_id, "rs-"),
            (new_attempt_id, "at-"),
            (new_event_id, "ev-"),
        )
        for generator, prefix in generators:
            with self.subTest(prefix=prefix):
                values = {generator() for _ in range(200)}
                self.assertEqual(len(values), 200)
                self.assertTrue(all(value.startswith(prefix) for value in values))
        self.assertRegex(new_task_id(), r"^wb-[0-9a-f]{12}$")


class TimeUtilTests(unittest.TestCase):
    def test_now_epoch_is_positive_float(self) -> None:
        value = now_epoch()
        self.assertIsInstance(value, float)
        self.assertGreater(value, 1_700_000_000.0)


class DurableModelTests(unittest.TestCase):
    def test_task_session_attempt_and_event_defaults(self) -> None:
        task = Task(
            task_id="wb-1",
            task_type="workbuddy",
            status=TaskStatus.QUEUED.value,
            route=TaskRoute.GATEWAY.value,
            created_at=1.0,
            updated_at=1.0,
        )
        self.assertFalse(task.result_available)
        self.assertEqual(task.version, 1)
        self.assertIsNone(task.result_json)
        self.assertFalse(task.has_result())

        completed = Task(
            task_id="wb-2",
            task_type="workbuddy",
            status=TaskStatus.COMPLETED.value,
            route=TaskRoute.GATEWAY.value,
            created_at=1.0,
            updated_at=2.0,
            result_available=True,
            result_json='{"answer": "ok"}',
        )
        self.assertTrue(completed.has_result())

        cloned = task.clone_with_version(7)
        self.assertEqual(cloned.version, 7)
        self.assertEqual(cloned.task_id, task.task_id)
        self.assertEqual(cloned.status, task.status)

        session = Session(
            session_id="rs-1",
            task_id="wb-1",
            backend="workbuddy",
            route=TaskRoute.GATEWAY.value,
            created_at=1.0,
            updated_at=1.0,
        )
        self.assertIsNone(session.backend_session_id)
        self.assertEqual(session.metadata_json, "{}")

        attempt = Attempt(
            attempt_id="at-1",
            task_id="wb-1",
            attempt_no=1,
            backend="workbuddy",
            route=TaskRoute.GATEWAY.value,
            status=TaskStatus.QUEUED.value,
            created_at=1.0,
        )
        self.assertEqual(attempt.attempt_no, 1)
        self.assertIsNone(attempt.started_at)
        self.assertIsNone(attempt.finished_at)

        event = TaskEvent(
            event_id="ev-1",
            task_id="wb-1",
            event_type=EventType.TASK_CREATED.value,
            event_time=1.0,
        )
        self.assertEqual(event.payload_json, "{}")
        self.assertEqual(event.visibility, EventVisibility.PUBLIC.value)
        self.assertIsNone(event.seq)


if __name__ == "__main__":
    unittest.main()
