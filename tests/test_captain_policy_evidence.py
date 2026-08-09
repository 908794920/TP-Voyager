from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_runtime.application.context_service import ContextError, ProjectContextService
from agent_runtime.application.dispatch.profiles import WorkerProfileError, WorkerProfileResolver
from agent_runtime.application.dispatch.repository_research import (
    RepositoryResearchError, RepositoryResearchService,
)
from agent_runtime.application.task_service import TaskService
from agent_runtime.backends.base import BackendStartRequest, BackendUsage
from agent_runtime.backends.errors import BackendTimeoutError
from agent_runtime.backends.qoder.backend import QoderBackend
from agent_runtime.domain.dispatch import (
    ModelPolicy, ReadScope, RepositoryResearchSpec, WorkerProfileRef,
)
from agent_runtime.domain.enums import EvidenceType, TaskRoute
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database
from agent_runtime.verification.artifacts.capture import (
    ArtifactCaptureService, capture_workspace_baseline,
)


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


    def test_read_scope_budget_and_nested_vendor_state_are_enforced(self) -> None:
        workspace = self.root / "budget-workspace"
        (workspace / "src" / ".git").mkdir(parents=True)
        (workspace / "src" / "a.py").write_text("a" * 16, encoding="utf-8")
        (workspace / "src" / "b.py").write_text("b" * 16, encoding="utf-8")
        (workspace / "src" / ".git" / "config").write_text("secret", encoding="utf-8")
        service = ProjectContextService(self.db)
        with self.assertRaises(ContextError):
            service.resolve_read_scope(
                str(workspace),
                ReadScope.from_dict({"directories": ["src"], "max_files": 1}),
            )
        with self.assertRaises(ContextError):
            service.resolve_read_scope(
                str(workspace),
                ReadScope.from_dict({"files": ["src/a.py"], "max_bytes": 8}),
            )
        resolved = service.resolve_read_scope(
            str(workspace),
            ReadScope.from_dict({"directories": ["src"], "max_files": 8, "max_bytes": 64}),
        )
        self.assertEqual(resolved, ["src/a.py", "src/b.py"])

    def test_read_only_artifact_capture_does_not_project_preexisting_dirty_diff(self) -> None:
        workspace = self.root / "dirty"
        workspace.mkdir()
        import subprocess
        subprocess.run(["git", "init"], cwd=workspace, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=workspace, check=True)
        subprocess.run(["git", "config", "user.name", "TP Test"], cwd=workspace, check=True)
        (workspace / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=workspace, check=True, stdout=subprocess.DEVNULL)
        (workspace / "unrelated.txt").write_text("pre-existing dirty\n", encoding="utf-8")
        baseline = capture_workspace_baseline(workspace)
        self.assertTrue(baseline.dirty)
        batch = ArtifactCaptureService(self.root / "artifacts").capture(
            task_id="wb-readonly", attempt_id="at-readonly", cwd=workspace, baseline=baseline, observe_git=False,
        )
        self.assertEqual(batch.changed_files, [])
        self.assertFalse(batch.patch_available)
        self.assertEqual(batch.public_artifacts(), [])

    def test_repository_research_prechecks_shallow_clone_and_removes_origin(self) -> None:
        target = self.root / "external-research"
        calls: list[list[str]] = []
        call_kwargs: list[tuple[list[str], dict]] = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            call_kwargs.append((list(argv), dict(kwargs)))
            if argv[:2] == ["git", "clone"]:
                source = Path(argv[-1])
                source.mkdir(parents=True)
                (source / ".git").mkdir()
                (source / "README.md").write_text("hello\n", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[:3] == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
            if argv[:3] == ["git", "remote", "remove"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        spec = RepositoryResearchSpec.from_dict({
            "url": "https://github.com/example/project",
            "target_directory": str(target),
            "max_size_bytes": 1024 * 1024,
            "report_path": "reports/review.md",
        })
        service = RepositoryResearchService(
            metadata_loader=lambda owner, repo: {"size": 1, "private": False},
            runner=runner,
        )
        workspace = service.prepare(spec)
        self.assertEqual(workspace.commit, "abc123")
        self.assertTrue((target / "source" / "README.md").is_file())
        self.assertTrue((target / "reports").is_dir())
        self.assertIn(["git", "remote", "remove", "origin"], calls)
        clone_kwargs = next(kwargs for argv, kwargs in call_kwargs if argv[:2] == ["git", "clone"])
        self.assertEqual(clone_kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(clone_kwargs["env"]["GIT_LFS_SKIP_SMUDGE"], "1")
        routing = workspace.routing_metadata()
        self.assertEqual(routing["max_size_bytes"], 1024 * 1024)
        self.assertFalse(routing["crew_source_network_tools_exposed"])
        self.assertTrue(routing["provider_transport_required"])
        self.assertEqual(
            service.prefix_read_scope(ReadScope.from_dict({"files": ["README.md"]})).files,
            ("source/README.md",),
        )

    def test_repository_research_rejects_non_github_existing_target_and_oversize(self) -> None:
        service = RepositoryResearchService(metadata_loader=lambda owner, repo: {"size": 10})
        with self.assertRaises(RepositoryResearchError):
            service.prepare(RepositoryResearchSpec.from_dict({
                "url": "https://example.com/a/b",
                "target_directory": str(self.root / "bad"),
                "max_size_bytes": 1024,
            }))
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaises(RepositoryResearchError):
            service.prepare(RepositoryResearchSpec.from_dict({
                "url": "https://github.com/a/b",
                "target_directory": str(existing),
                "max_size_bytes": 1024 * 1024,
            }))
        with self.assertRaises(RepositoryResearchError):
            service.prepare(RepositoryResearchSpec.from_dict({
                "url": "https://github.com/a/b",
                "target_directory": str(self.root / "too-big"),
                "max_size_bytes": 1024,
            }))

    def test_worker_profile_ref_requires_exact_hash_and_keeps_content_transient(self) -> None:
        store = self.root / "profiles"
        profile = store / "java-review" / "1.0.md"
        profile.parent.mkdir(parents=True)
        content = "Review Java changes only.\n"
        # Write with explicit newline="\n" so the file bytes match the source
        # string on Windows (text mode would otherwise translate to CRLF and
        # break the exact-hash assertion against read_bytes()).
        with profile.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        digest = hashlib.sha256(profile.read_bytes()).hexdigest()
        ref = WorkerProfileRef.from_dict({"name": "java-review", "version": "1.0", "sha256": digest})
        resolved = WorkerProfileResolver(store).resolve(ref)
        self.assertEqual(resolved.content, content)
        bad = WorkerProfileRef.from_dict({"name": "java-review", "version": "1.0", "sha256": "0" * 64})
        with self.assertRaises(WorkerProfileError):
            WorkerProfileResolver(store).resolve(bad)


if __name__ == "__main__":
    unittest.main()
