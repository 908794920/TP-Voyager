from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from agent_runtime.application.context_service import ContextError, ProjectContextService
from agent_runtime.application.dispatch.profiles import WorkerProfileError, WorkerProfileResolver
from agent_runtime.application.task_service import TaskService
from agent_runtime.backends.base import BackendStartRequest, BackendUsage
from agent_runtime.backends.errors import BackendTimeoutError
from agent_runtime.backends.qoder.backend import QoderBackend
from agent_runtime.domain.dispatch import ModelPolicy, ReadScope, WorkerProfileRef
from agent_runtime.domain.enums import EvidenceType, TaskRoute
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database


class CaptainPolicyEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_usage_evidence_records_only_provider_reported_values(self) -> None:
        usage = BackendUsage(
            provider="qoder",
            model="Lite",
            source="qoder_acp_usage_update",
            input_tokens=120,
            output_tokens=30,
            provider_usage={"inputTokens": 120, "outputTokens": 30},
        ).to_dict()
        self.assertIsNone(usage["usage"]["credits_used"])
        self.assertIsNone(usage["usage"]["reported_cost"])

        service = TaskService(self.db)
        task = Task(
            task_id="wb-usage", task_type="qoder", status="queued", route=TaskRoute.ACP.value,
            created_at=1.0, updated_at=1.0,
        )
        session = Session(
            session_id="rs-usage", task_id=task.task_id, backend="qoder", route=TaskRoute.ACP.value,
            created_at=1.0, updated_at=1.0,
        )
        created = service.create_task(
            task=task, session=session, metadata={}, idempotency_key="", request_fingerprint="fp", now=1.0,
        )
        self.assertTrue(service.append_usage_evidence(task.task_id, usage=usage))
        self.assertFalse(service.append_usage_evidence(task.task_id, usage=usage))
        self.assertEqual(service.latest_usage_evidence(task.task_id), usage)
        evidence = service.list_evidence(task.task_id, created.attempt_id or "")
        self.assertEqual([item.evidence_type for item in evidence], [EvidenceType.USAGE.value])


    def test_qoder_timeout_preserves_usage_already_reported_by_provider(self) -> None:
        observed = []

        class Client:
            def run(self, **kwargs):
                raise BackendTimeoutError("timed out", timeout_reason="idle_timeout")
            def usage_snapshot(self):
                return {"inputTokens": 77, "outputTokens": 9}
            def close(self):
                pass
            def cancel(self, session_id=""):
                pass

        class Callbacks:
            def on_dispatch_accepted(self, backend_session_id):
                pass
            def on_activity(self, activity):
                pass
            def on_result(self, result):
                pass
            def on_usage(self, usage):
                observed.append(usage.to_dict())

        backend = QoderBackend(read_only_acp_client_factory=lambda **kwargs: Client())
        request = BackendStartRequest(
            task_id="wb-timeout", attempt_id="at-timeout", runtime_session_id="rs-timeout",
            prompt="inspect", cwd=str(self.root), model="Lite",
            metadata={"route": "acp_read_only"},
        )
        with self.assertRaises(BackendTimeoutError):
            backend.start(request, Callbacks())
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["usage"]["input_tokens"], 77)
        self.assertEqual(observed[0]["usage"]["output_tokens"], 9)

    def test_model_policy_is_allow_list_not_selector(self) -> None:
        policy = ModelPolicy.from_dict({"allowed_models": ["Lite", "Pro", "Lite"]})
        self.assertEqual(policy.allowed_models, ("Lite", "Pro"))
        self.assertNotIn("selected_model", policy.to_dict())

    def test_read_scope_expands_to_same_bounded_concrete_files(self) -> None:
        workspace = self.root / "workspace"
        (workspace / "src").mkdir(parents=True)
        (workspace / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
        (workspace / "README.md").write_text("x\n", encoding="utf-8")
        scope = ReadScope.from_dict({"files": ["README.md"], "globs": ["src/*.py"]})
        files = ProjectContextService(self.db).resolve_read_scope(str(workspace), scope)
        self.assertEqual(files, ["README.md", "src/a.py"])
        with self.assertRaises(ContextError):
            ProjectContextService(self.db).resolve_read_scope(
                str(workspace), ReadScope.from_dict({"files": [".git/config"]})
            )

    def test_worker_profile_ref_requires_exact_hash_and_keeps_content_transient(self) -> None:
        store = self.root / "profiles"
        profile = store / "java-review" / "1.0.md"
        profile.parent.mkdir(parents=True)
        content = "Review Java changes only.\n"
        profile.write_text(content, encoding="utf-8")
        digest = hashlib.sha256(profile.read_bytes()).hexdigest()
        ref = WorkerProfileRef.from_dict({"name": "java-review", "version": "1.0", "sha256": digest})
        resolved = WorkerProfileResolver(store).resolve(ref)
        self.assertEqual(resolved.content, content)
        bad = WorkerProfileRef.from_dict({"name": "java-review", "version": "1.0", "sha256": "0" * 64})
        with self.assertRaises(WorkerProfileError):
            WorkerProfileResolver(store).resolve(bad)


if __name__ == "__main__":
    unittest.main()
