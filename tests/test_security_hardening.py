from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.application.task_service import TaskService
from agent_runtime.backends.codebuddy.sdk_client import CodeBuddySdkClient
from agent_runtime.backends.qoder.acp_client import QoderAcpClient
from agent_runtime.domain.enums import BackendKind, EventType, TaskRoute, TaskStatus
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.errors import LeaseLostError
from agent_runtime.runtime.lease import LeaseService
from agent_runtime.verification.service import _matches_prefix


class PathPolicyHardeningTests(unittest.TestCase):
    def test_dot_prefixed_siblings_do_not_match_component_prefixes(self) -> None:
        cases = [
            ("src/a.py", "src", True),
            ("src", "src", True),
            (".src/a.py", "src", False),
            ("..src/a.py", "src", False),
            (".env", "env", False),
            (".github/workflows/ci.yml", "github", False),
            ("...foo/bar", "foo", False),
        ]
        for path, prefix, expected in cases:
            with self.subTest(path=path, prefix=prefix):
                self.assertEqual(CodeBuddySdkClient._matches(path, (prefix,)), expected)
                self.assertEqual(QoderAcpClient._matches(path, (prefix,)), expected)
                self.assertEqual(_matches_prefix(path, prefix), expected)


class DispatchFenceHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "runtime.db")
        self.db.initialize()
        self.service = TaskService(self.db)
        self.task_id = "task-dispatch-fence"
        self.service.create_task(
            task=Task(
                task_id=self.task_id,
                task_type="test",
                status=TaskStatus.QUEUED.value,
                route=TaskRoute.GATEWAY.value,
                created_at=1.0,
                updated_at=1.0,
            ),
            session=Session(
                session_id="session-runtime",
                task_id=self.task_id,
                backend=BackendKind.WORKBUDDY.value,
                route=TaskRoute.GATEWAY.value,
                created_at=1.0,
                updated_at=1.0,
            ),
            metadata={},
            idempotency_key="",
            request_fingerprint="fp",
            now=1.0,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_backend_dispatch_acceptance_requires_live_lease_and_version(self) -> None:
        old_owner = LeaseService(self.db, instance_id="old", lease_duration_seconds=60)
        new_owner = LeaseService(self.db, instance_id="new", lease_duration_seconds=60)
        old_lease = old_owner.acquire(self.task_id)
        self.assertIsNotNone(old_lease)
        assert old_lease is not None

        # Release and reacquire to bump the durable generation; the stale token
        # must never be able to cross the real-provider dispatch gate.
        self.assertTrue(old_owner.release(self.task_id, old_lease))
        new_lease = new_owner.acquire(self.task_id)
        self.assertIsNotNone(new_lease)

        with self.assertRaises(LeaseLostError):
            self.service.accept_backend_dispatch(
                self.task_id,
                backend_session_id="stale-provider-session",
                version=1,
                lease=old_lease,
            )

        session = self.service.get_session(self.task_id)
        assert session is not None
        self.assertFalse(session.backend_session_id)
        events = self.service.get_events(self.task_id)
        self.assertFalse(any(e.event_type == EventType.BACKEND_DISPATCH_ACCEPTED.value for e in events))

    def test_backend_dispatch_acceptance_is_atomic_and_idempotent_for_live_owner(self) -> None:
        owner = LeaseService(self.db, instance_id="worker", lease_duration_seconds=60)
        lease = owner.acquire(self.task_id)
        self.assertIsNotNone(lease)
        assert lease is not None

        self.service.accept_backend_dispatch(
            self.task_id,
            backend_session_id="provider-session",
            version=1,
            lease=lease,
        )
        self.service.accept_backend_dispatch(
            self.task_id,
            backend_session_id="provider-session",
            version=1,
            lease=lease,
        )

        session = self.service.get_session(self.task_id)
        assert session is not None
        self.assertEqual(session.backend_session_id, "provider-session")
        events = self.service.get_events(self.task_id)
        self.assertEqual(sum(e.event_type == EventType.SESSION_CREATED.value for e in events), 1)
        self.assertEqual(sum(e.event_type == EventType.BACKEND_DISPATCH_ACCEPTED.value for e in events), 1)

class InstallationParityTests(unittest.TestCase):
    def test_requirements_include_qoder_sdk_declared_by_pyproject(self) -> None:
        root = Path(__file__).resolve().parents[1]
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("qoder-agent-sdk>=1.0.11,<2", pyproject)
        self.assertIn("qoder-agent-sdk>=1.0.11,<2", requirements)
