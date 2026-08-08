from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime import server
from agent_runtime.backends.base import BackendActivity, BackendResult, BackendStartRequest
from agent_runtime.backends.errors import BackendProtocolError
from agent_runtime.backends.fake import FakeBackend
from agent_runtime.backends.qoder.acp_client import AcpRunResult
from agent_runtime.backends.qoder.backend import QoderBackend


class Callbacks:
    def __init__(self) -> None:
        self.accepted: list[str] = []
        self.activities: list[str] = []
        self.results: list[BackendResult] = []

    def on_dispatch_accepted(self, session_id: str) -> None:
        self.accepted.append(session_id)

    def on_activity(self, activity: BackendActivity) -> None:
        self.activities.append(activity.kind)

    def on_result(self, result: BackendResult) -> None:
        self.results.append(result)


class FakeAcpClient:
    def __init__(self, *, cwd, on_activity) -> None:
        self.cwd = cwd
        self.on_activity = on_activity
        self.process = type("P", (), {"poll": lambda self: 0})()
        self.closed = False

    def run(self, **kwargs):
        kwargs["on_dispatch_accepted"]("qoder-session")
        self.on_activity(BackendActivity(kind="stream_activity", timestamp=1.0))
        return AcpRunResult(
            session_id="qoder-session",
            stop_reason="end_turn",
            answer="qoder answer",
            observability={"route": "acp", "event_count": 1},
        )

    def cancel(self, session_id="") -> None:
        pass

    def close(self) -> None:
        self.closed = True


class QoderBackendTests(unittest.TestCase):
    def request(self, route: str = "acp_read_only") -> BackendStartRequest:
        return BackendStartRequest(
            task_id="qoder-task",
            attempt_id="at-qoder",
            runtime_session_id="rs-qoder",
            prompt="do work",
            cwd=str(Path.cwd()),
            metadata={"route": route},
        )

    def test_controlled_read_only_route_uses_read_only_factory(self) -> None:
        calls = []

        def factory(**kwargs):
            calls.append(kwargs)
            return FakeAcpClient(**kwargs)

        callbacks = Callbacks()
        result = QoderBackend(read_only_acp_client_factory=factory).start(
            self.request(), callbacks
        )
        self.assertEqual(result.answer, "qoder answer")
        self.assertEqual(result.observability["access_mode"], "read_only")
        self.assertEqual(callbacks.accepted, ["qoder-session"])
        self.assertEqual(len(calls), 1)

    def test_patch_route_passes_captain_policy_to_patch_factory(self) -> None:
        calls = []

        def factory(**kwargs):
            calls.append(dict(kwargs))
            return FakeAcpClient(cwd=kwargs["cwd"], on_activity=kwargs["on_activity"])

        base = self.request("acp_patch")
        request = BackendStartRequest(
            **{
                **base.__dict__,
                "metadata": {
                    "route": "acp_patch",
                    "patch_policy": {
                        "allowed_paths": ["src"],
                        "forbidden_paths": [".git"],
                        "commands": [{"id": "verify", "argv": ["python", "-V"]}],
                    },
                },
            }
        )
        result = QoderBackend(patch_acp_client_factory=factory).start(request, Callbacks())
        self.assertEqual(result.observability["access_mode"], "patch")
        self.assertEqual(calls[0]["allowed_paths"], ("src",))
        self.assertEqual(calls[0]["command_specs"][0].command_id, "verify")

    def test_uncontrolled_legacy_routes_are_rejected(self) -> None:
        backend = QoderBackend()
        for route in ("acp", "print", "yolo"):
            with self.subTest(route=route), self.assertRaises(BackendProtocolError):
                backend.start(self.request(route), Callbacks())


class QoderServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cwd = Path(self.tmp.name) / "project"
        self.cwd.mkdir()
        server.configure_runtime_database(Path(self.tmp.name) / "runtime.db")
        server.TASKS.clear()
        server.IDEMPOTENCY_TASKS.clear()

    def tearDown(self) -> None:
        server.TASKS.clear()
        server.IDEMPOTENCY_TASKS.clear()
        server.configure_runtime_database(None)
        self.tmp.cleanup()

    def wait(self, task_id: str) -> dict:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = server.subagent_status(task_id)
            if state.get("state") in {"completed", "failed", "cancelled"}:
                return state
            time.sleep(0.05)
        self.fail("qoder task did not finish")

    def test_captain_dispatch_uses_controlled_read_only_route(self) -> None:
        fake = FakeBackend(
            result=BackendResult(
                backend="qoder",
                stop_reason="end_turn",
                answer="captain read-only",
                result={"backend": "qoder", "stopReason": "end_turn"},
                backend_session_id="qoder-controlled-session",
            )
        )
        with patch("agent_runtime.server._create_qoder_backend", return_value=fake):
            started = server.task_dispatch(
                objective="inspect the module without changing files",
                crew="qoder",
                task_kind="research",
                cwd=str(self.cwd),
                timeout_seconds=10,
            )
            self.assertTrue(started["ok"], started)
            self.assertEqual(self.wait(started["task_id"])["state"], "completed")

        self.assertEqual(fake.starts[0].metadata["route"], "acp_read_only")
        self.assertEqual(server.task_result(started["task_id"])["answer"], "captain read-only")

    def test_generic_subagent_resume_uses_controlled_route(self) -> None:
        fake = FakeBackend(
            result=BackendResult(
                backend="qoder",
                stop_reason="end_turn",
                answer="qoder fake",
                result={"backend": "qoder", "stopReason": "end_turn"},
                backend_session_id="qoder-private-session",
            )
        )
        with patch("agent_runtime.server._create_qoder_backend", return_value=fake):
            first = server.subagent_start(
                prompt="new", runtime="qoder", route="acp_read_only", cwd=str(self.cwd)
            )
            self.assertTrue(first["ok"])
            self.assertEqual(self.wait(first["task_id"])["state"], "completed")
            second = server.subagent_start(
                prompt="continue", runtime="qoder", route="acp_read_only",
                resume_task_id=first["task_id"], cwd=str(self.cwd),
            )
            self.assertTrue(second["ok"])
            self.assertEqual(self.wait(second["task_id"])["state"], "completed")
        self.assertEqual(len(fake.starts), 1)
        self.assertEqual(len(fake.resumes), 1)


if __name__ == "__main__":
    unittest.main()
