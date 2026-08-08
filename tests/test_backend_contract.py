"""Current backend contract tests for TP-Voyager Crew adapters."""
from __future__ import annotations

import inspect
import unittest

from agent_runtime.backends.base import (
    BackendActivity,
    BackendCancelRequest,
    BackendCancelResult,
    BackendResumeRequest,
    BackendResult,
    BackendStartRequest,
)
from agent_runtime.backends.codebuddy.backend import CodeBuddyBackend
from agent_runtime.backends.errors import (
    BackendCancelledError,
    BackendDispatchError,
    BackendError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from agent_runtime.backends.fake import FakeBackend
from agent_runtime.backends.qoder.backend import QoderBackend


class _RecordingCallbacks:
    def __init__(self) -> None:
        self.dispatch_accepted: list[str] = []
        self.activities: list[BackendActivity] = []
        self.results: list[BackendResult] = []
        self.fail_dispatch = False

    def on_dispatch_accepted(self, backend_session_id: str) -> None:
        if self.fail_dispatch:
            raise BackendDispatchError("dispatch persistence failed")
        self.dispatch_accepted.append(backend_session_id)

    def on_activity(self, activity: BackendActivity) -> None:
        self.activities.append(activity)

    def on_result(self, result: BackendResult) -> None:
        self.results.append(result)


class ContractConformanceTests(unittest.TestCase):
    def test_current_backends_expose_shared_protocol_surface(self) -> None:
        for backend_cls in (FakeBackend, CodeBuddyBackend, QoderBackend):
            with self.subTest(backend=backend_cls.__name__):
                instance = backend_cls()
                for method in ("start", "resume", "cancel", "reconcile", "probe"):
                    self.assertTrue(callable(getattr(instance, method, None)))
                    inspect.signature(getattr(backend_cls, method))


class FakeBackendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.callbacks = _RecordingCallbacks()

    def test_start_resume_cancel_probe_contract(self) -> None:
        result = self.backend.start(
            BackendStartRequest(
                task_id="task-1", attempt_id="at-1", runtime_session_id="rs-1",
                prompt="test", cwd="/tmp",
            ),
            self.callbacks,
        )
        self.assertIsInstance(result, BackendResult)
        self.assertEqual(self.callbacks.dispatch_accepted, ["fake-session"])

        self.callbacks.dispatch_accepted.clear()
        resumed = self.backend.resume(
            BackendResumeRequest(
                task_id="task-1", attempt_id="at-1", runtime_session_id="rs-1",
                prompt="test", cwd="/tmp", resume_session_id="sess-1",
            ),
            self.callbacks,
        )
        self.assertIsInstance(resumed, BackendResult)
        self.assertEqual(self.callbacks.dispatch_accepted, ["fake-resume-session"])

        cancel = self.backend.cancel(
            BackendCancelRequest(task_id="task-1", attempt_id="at-1", cancel_scope="execution")
        )
        self.assertIsInstance(cancel, BackendCancelResult)
        self.assertTrue(cancel.ok)
        self.assertTrue(self.backend.probe()["connected"])

    def test_dispatch_persistence_failure_is_not_hidden(self) -> None:
        self.callbacks.fail_dispatch = True
        with self.assertRaises(BackendDispatchError):
            self.backend.start(
                BackendStartRequest(
                    task_id="task-1", attempt_id="at-1", runtime_session_id="rs-1",
                    prompt="test", cwd="/tmp",
                ),
                self.callbacks,
            )


class BackendModelAndErrorTests(unittest.TestCase):
    def test_backend_model_defaults(self) -> None:
        from agent_runtime.backends.base import BackendExecution

        req = BackendStartRequest(
            task_id="task-1", attempt_id="at-1", runtime_session_id="rs-1",
            prompt="test", cwd="/tmp",
        )
        self.assertEqual(req.model, "")
        self.assertEqual(req.reasoning_effort, "")
        self.assertEqual(req.idle_timeout_seconds, 180.0)
        self.assertEqual(req.max_task_duration_seconds, 1800.0)

        ex = BackendExecution()
        self.assertEqual(ex.backend_session_id, "")
        self.assertIsNone(ex.cancel)

        result = BackendResult()
        self.assertEqual((result.backend, result.stop_reason, result.answer), ("", "", ""))

    def test_backend_error_hierarchy_and_timeout_reason(self) -> None:
        errors = [
            BackendDispatchError("dispatch"),
            BackendTimeoutError("timeout", timeout_reason="idle"),
            BackendCancelledError("cancelled"),
            BackendUnavailableError("unavailable"),
        ]
        self.assertTrue(all(isinstance(err, BackendError) for err in errors))
        self.assertEqual(errors[1].timeout_reason, "idle")


if __name__ == "__main__":
    unittest.main()
