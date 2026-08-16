from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
import time
import unittest
import subprocess
import hashlib
import json
import threading

from agent_runtime.application.context_service import ProjectContextService
from agent_runtime.application.task_service import TaskService
from agent_runtime.backends.codebuddy.sdk_client import _normalize_codebuddy_usage
from agent_runtime.backends.base import BackendUsage
from agent_runtime.domain.crew_outcome import parse_crew_outcome
from agent_runtime.domain.dispatch import ReadScope, ApplyReceipt, TrustedInstructionRef
from agent_runtime.domain.run_control import RunControlSpec
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database
from agent_runtime.domain.artifact import Artifact
from agent_runtime.persistence.artifact_repository import ArtifactRepository
from agent_runtime.verification.subject import VerificationSubjectService, VerificationSubjectError, repository_identity, worktree_tree_hash, git_status_digest
from agent_runtime.application.dispatch.workspace import PatchWorkspaceService
from agent_runtime.application.dispatch.repository_research import RepositoryResearchService, RepositoryResearchError
from agent_runtime.application.dispatch.profiles import resolve_trusted_instruction_refs, TrustedTextError


class V105FlowControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()
        self.tasks = TaskService(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create(self, task_id: str, *, step: str, spec: RunControlSpec, key: str | None = None, max_runtime: float = 30.0):
        now = time.time()
        task = Task(
            task_id=task_id, task_type="codebuddy", status="queued", route="sdk_context_read_only",
            created_at=now, updated_at=now, run_id=spec.run_id, step_key=step,
        )
        session = Session(
            session_id=f"sess-{task_id}", task_id=task_id, backend="codebuddy",
            route="sdk_context_read_only", created_at=now, updated_at=now,
        )
        return self.tasks.create_task(
            task=task, session=session,
            metadata={"max_task_duration_seconds": max_runtime},
            idempotency_key=key or f"key-{task_id}", request_fingerprint=f"fp-{task_id}",
            run_control=spec, requested_runtime_seconds=max_runtime, now=now,
        )

    def test_run_budget_is_durable_and_dispatch_limited(self) -> None:
        spec = RunControlSpec("run-a", 1, 120.0)
        first = self._create("wb-one", step="research-01", spec=spec)
        second = self._create("wb-two", step="research-02", spec=spec)
        self.assertEqual(first.outcome, "created")
        self.assertEqual(second.outcome, "budget_rejected")
        self.assertEqual(second.reason_code, "RUN_DISPATCH_BUDGET_EXCEEDED")
        snapshot = self.tasks.get_run_control("run-a")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.dispatches_reserved, 1)

    def test_run_step_is_unique_and_lookupable(self) -> None:
        spec = RunControlSpec("run-b", 3, 120.0)
        first = self._create("wb-step-one", step="implementation-01", spec=spec)
        self.assertEqual(first.outcome, "created")
        clash = self._create("wb-step-two", step="implementation-01", spec=spec)
        self.assertEqual(clash.outcome, "step_conflict")
        found = self.tasks.get_task_by_run_step("run-b", "implementation-01")
        self.assertIsNotNone(found)
        self.assertEqual(found.task_id, "wb-step-one")

    def test_idempotency_replay_does_not_charge_again(self) -> None:
        spec = RunControlSpec("run-c", 2, 120.0)
        first = self._create("wb-replay", step="analysis-01", spec=spec, key="same-key")
        self.assertEqual(first.outcome, "created")
        # Replay must use the original task identity/fingerprint in the public service.
        durable = self.tasks.get_task("wb-replay")
        session = self.tasks.get_session("wb-replay")
        replay = self.tasks.create_task(
            task=Task(**{**durable.__dict__}), session=Session(**{**session.__dict__, "metadata_json": "{}"}),
            metadata={"max_task_duration_seconds": 30.0}, idempotency_key="same-key",
            request_fingerprint="fp-wb-replay", run_control=spec, requested_runtime_seconds=30.0,
        )
        self.assertEqual(replay.outcome, "replayed")
        self.assertEqual(self.tasks.get_run_control("run-c").dispatches_reserved, 1)

    def test_run_budget_cannot_be_widened(self) -> None:
        base = RunControlSpec("run-d", 2, 120.0)
        self.assertEqual(self._create("wb-base", step="s1", spec=base).outcome, "created")
        widened = RunControlSpec("run-d", 3, 120.0)
        rejected = self._create("wb-wide", step="s2", spec=widened)
        self.assertEqual(rejected.outcome, "budget_rejected")
        self.assertEqual(rejected.reason_code, "RUN_BUDGET_RELAXATION_REJECTED")

    def test_strict_unobservable_usage_budget_is_rejected(self) -> None:
        strict = RunControlSpec("run-e", 3, 120.0, max_credits=10.0, require_strict_usage_budget=True)
        result = self._create("wb-strict", step="s1", spec=strict)
        self.assertEqual(result.outcome, "budget_rejected")
        self.assertEqual(result.reason_code, "BUDGET_NOT_ENFORCEABLE")

    def test_concurrent_dispatches_cannot_pierce_run_budget(self) -> None:
        spec = RunControlSpec("run-race", 1, 120.0)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()
        def worker(index: int) -> None:
            barrier.wait()
            result = self._create(f"wb-race-{index}", step=f"step-{index}", spec=spec)
            with lock:
                outcomes.append(result.outcome)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(outcomes), ["budget_rejected", "created"])
        self.assertEqual(self.tasks.get_run_control("run-race").dispatches_reserved, 1)

    def test_run_usage_comes_from_usage_evidence(self) -> None:
        spec = RunControlSpec("run-usage", 2, 120.0, max_input_tokens=1000, max_output_tokens=1000)
        created = self._create("wb-usage", step="s1", spec=spec)
        self.assertEqual(created.outcome, "created")
        self.tasks.append_usage_evidence(
            "wb-usage",
            usage=BackendUsage(provider="qoder", model="lite", source="qoder_acp_usage_update", input_tokens=123, output_tokens=45).to_dict(),
        )
        snapshot = self.tasks.get_run_control("run-usage")
        self.assertEqual(snapshot.input_tokens_consumed, 123)
        self.assertEqual(snapshot.output_tokens_consumed, 45)
        self.assertTrue(snapshot.usage_complete)

    def test_trusted_instruction_refs_are_hash_pinned_and_root_bounded(self) -> None:
        trusted_root = self.root / "trusted"
        trusted_root.mkdir()
        instruction = trusted_root / "review.md"
        instruction.write_bytes(b"Review only the requested scope.\n")
        digest = hashlib.sha256(instruction.read_bytes()).hexdigest()
        ref = TrustedInstructionRef("ai-work", "review.md", digest, 4096)
        resolved = resolve_trusted_instruction_refs((ref,), {"ai-work": trusted_root})
        self.assertEqual(resolved, ("Review only the requested scope.\n",))
        with self.assertRaises(TrustedTextError):
            resolve_trusted_instruction_refs((TrustedInstructionRef("missing", "review.md", digest, 4096),), {"ai-work": trusted_root})
        with self.assertRaises(TrustedTextError):
            resolve_trusted_instruction_refs((TrustedInstructionRef("ai-work", "review.md", "0" * 64, 4096),), {"ai-work": trusted_root})
        outside = self.root / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        outside_digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        with self.assertRaises(TrustedTextError):
            resolve_trusted_instruction_refs((TrustedInstructionRef("ai-work", "../outside.md", outside_digest, 4096),), {"ai-work": trusted_root})

    def test_terminal_task_releases_unused_runtime_reservation(self) -> None:
        spec = RunControlSpec("run-terminal", 2, 100.0)
        created = self._create("wb-terminal", step="s1", spec=spec, max_runtime=80.0)
        self.assertEqual(created.outcome, "created")
        started = time.time() - 2.0
        finished = started + 1.0
        with self.db.immediate_transaction() as connection:
            connection.execute(
                "UPDATE tasks SET status='completed', started_at=?, finished_at=?, updated_at=? WHERE task_id=?",
                (started, finished, finished, "wb-terminal"),
            )
        snapshot = self.tasks.get_run_control("run-terminal")
        self.assertAlmostEqual(snapshot.runtime_reserved_seconds, 0.0, places=6)
        self.assertGreaterEqual(snapshot.runtime_consumed_seconds, 0.9)
        self.assertLess(snapshot.runtime_consumed_seconds, 2.0)

    def test_observed_usage_budget_blocks_future_dispatch(self) -> None:
        spec = RunControlSpec("run-token-cap", 3, 300.0, max_input_tokens=1000)
        first = self._create("wb-token-one", step="s1", spec=spec, max_runtime=30.0)
        self.assertEqual(first.outcome, "created")
        self.tasks.append_usage_evidence(
            "wb-token-one",
            usage=BackendUsage(provider="qoder", model="lite", source="qoder_acp_usage_update", input_tokens=1000).to_dict(),
        )
        second = self._create("wb-token-two", step="s2", spec=spec, max_runtime=30.0)
        self.assertEqual(second.outcome, "budget_rejected")
        self.assertEqual(second.reason_code, "RUN_TOKEN_BUDGET_EXCEEDED")

    def test_crew_outcome_requires_explicit_structured_marker(self) -> None:
        unavailable = parse_crew_outcome("I think more context may be useful")
        self.assertFalse(unavailable.get("available"))
        parsed = parse_crew_outcome(
            'answer\nTP_VOYAGER_CREW_OUTCOME_JSON={"schema":"tp-voyager.crew_outcome/v1","status":"NEEDS_CONTEXT","summary":"need pom","requested_files":["pom.xml"],"requested_commands":[],"findings":[],"evidence_refs":[]}'
        )
        self.assertTrue(parsed.get("available"))
        self.assertEqual(parsed.get("status"), "NEEDS_CONTEXT")

    def test_codebuddy_typed_usage_is_normalized(self) -> None:
        @dataclass
        class Usage:
            input_tokens: int = 120
            output_tokens: int = 45
            cache_read_input_tokens: int = 7
            secret: str = "never"
        normalized = _normalize_codebuddy_usage(Usage())
        self.assertEqual(normalized["input_tokens"], 120)
        self.assertEqual(normalized["output_tokens"], 45)
        self.assertNotIn("secret", normalized)

    def test_large_scope_manifest_partitions_deterministically(self) -> None:
        workspace = self.root / "repo"
        workspace.mkdir()
        for index in range(300):
            path = workspace / f"f{index:03d}.txt"
            path.write_text("x" * 32, encoding="utf-8")
        contexts = ProjectContextService(self.db)
        files = contexts.resolve_scope_manifest(str(workspace), ReadScope(globs=("*.txt",), max_files=256, max_bytes=8*1024*1024))
        self.assertEqual(len(files), 300)
        full = contexts.register_scope_manifest(str(workspace), files).manifest
        segments = contexts.scope_segments(full["context_id"], max_files=128, max_bytes=4096)
        self.assertEqual([len(item) for item in segments], [128, 128, 44])
        first = contexts.register_scope_segment(str(workspace), full["context_id"], index=0, max_files=128, max_bytes=4096)
        replay = contexts.register_scope_segment(str(workspace), full["context_id"], index=0, max_files=128, max_bytes=4096)
        self.assertEqual(first.manifest["context_id"], replay.manifest["context_id"])
        self.assertEqual(first.manifest["root_hash"], replay.manifest["root_hash"])

    def test_repository_snapshot_reuse_does_not_overwrite_existing_report(self) -> None:
        root = self.root / "research"
        source = root / "source"
        reports = root / "reports"
        source.mkdir(parents=True)
        reports.mkdir()
        subprocess.run(["git", "init"], cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.name", "TP Test"], cwd=source, check=True)
        (source / "a.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=source, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
        (reports / "segment-02.md").write_text("existing", encoding="utf-8")
        service = RepositoryResearchService()
        with self.assertRaises(RepositoryResearchError):
            service.reuse(
                root=str(root), expected_url="https://github.com/example/repo", commit=commit,
                report_path="reports/segment-02.md", max_size_bytes=1024 * 1024,
            )

    def test_apply_receipt_requires_exact_schema_and_captain_host_owner(self) -> None:
        base = {
            "schema": "tp-voyager.apply_receipt/v1",
            "repository_identity": "repo", "base_commit": "abc", "base_tree_hash": "tree",
            "patch_artifact_id": "art", "patch_sha256": "1" * 64, "result_tree_hash": "result",
            "changed_files": ["a.txt"], "applied_by": "captain_host",
            "applied_at": "2026-08-09T23:59:00+08:00", "git_status_digest": "2" * 64,
            "conflicts": [], "receipt_sha256": "3" * 64,
        }
        with self.assertRaises(ValueError):
            ApplyReceipt.from_dict({**base, "schema": "other/v1"})
        with self.assertRaises(ValueError):
            ApplyReceipt.from_dict({**base, "applied_by": "crew"})
        with self.assertRaises(ValueError):
            ApplyReceipt.from_dict({**base, "changed_files": ["../outside.txt"]})

    def test_apply_receipt_binds_exact_verification_subject(self) -> None:
        repo = self.root / "passenger"
        repo.mkdir()
        def git(*args: str, cwd: Path = repo) -> str:
            cp = subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return cp.stdout.strip()
        git("init")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "TP Test")
        (repo / "a.txt").write_text("base\n", encoding="utf-8")
        git("add", "a.txt")
        git("commit", "-m", "base")
        base = git("rev-parse", "HEAD")
        base_tree = git("rev-parse", f"{base}^{{tree}}")

        patch_worktree = self.root / "patch-worktree"
        git("worktree", "add", "--detach", str(patch_worktree), base)
        (patch_worktree / "a.txt").write_text("base\naccepted\n", encoding="utf-8")
        patch_bytes = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--", "a.txt"], cwd=patch_worktree, check=True, stdout=subprocess.PIPE
        ).stdout
        patch_sha = hashlib.sha256(patch_bytes).hexdigest()

        now = time.time()
        source_task = Task(
            task_id="wb-patch-source", task_type="codebuddy", status="queued", route="sdk_patch",
            created_at=now, updated_at=now,
        )
        source_session = Session(
            session_id="sess-patch-source", task_id=source_task.task_id, backend="codebuddy",
            route="sdk_patch", created_at=now, updated_at=now,
        )
        created = self.tasks.create_task(
            task=source_task, session=source_session,
            metadata={"source_cwd": str(repo), "cwd": str(patch_worktree), "workspace_base_revision": base},
            idempotency_key="patch-source", request_fingerprint="patch-source-fp",
        )
        self.assertEqual(created.outcome, "created")
        artifact_id = "art-patch-v105"
        storage_key = "patches/art-patch-v105.patch"
        blob = self.db.path.parent / "artifacts" / storage_key
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(patch_bytes)
        artifact = Artifact(
            artifact_id=artifact_id, task_id=source_task.task_id, attempt_id=created.attempt_id,
            origin="runtime", kind="patch", name="workspace.patch", capture_state="captured",
            declared_at=now, created_at=now, updated_at=now, captured_at=now,
            workspace_relpath="workspace.patch", storage_key=storage_key, sha256=patch_sha, size_bytes=len(patch_bytes),
            metadata_json=json.dumps({"baseline_head": base}),
        )
        with self.db.immediate_transaction() as connection:
            ArtifactRepository(self.db).insert_many(connection, [artifact])

        subprocess.run(["git", "apply", "--binary", "-"], cwd=repo, input=patch_bytes, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result_tree = worktree_tree_hash(repo)
        status_digest, changed_files = git_status_digest(repo)
        body = {
            "schema": "tp-voyager.apply_receipt/v1",
            "repository_identity": repository_identity(repo), "base_commit": base, "base_tree_hash": base_tree,
            "patch_artifact_id": artifact_id, "patch_sha256": patch_sha, "result_tree_hash": result_tree,
            "changed_files": list(changed_files), "applied_by": "captain_host", "applied_at": "2026-08-09T23:59:00+08:00",
            "git_status_digest": status_digest, "conflicts": [],
        }
        receipt_sha = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        receipt = ApplyReceipt.from_dict({**body, "receipt_sha256": receipt_sha})
        workspace_root = self.root / "verification-workspaces"
        verifier = VerificationSubjectService(self.db, workspace_root)
        prepared = verifier.prepare(receipt, repo)
        self.assertEqual(worktree_tree_hash(prepared.worktree_root), result_tree)
        PatchWorkspaceService(workspace_root).cleanup(prepared)

        (repo / "a.txt").write_text("base\naccepted\ndrift\n", encoding="utf-8")
        with self.assertRaises(VerificationSubjectError) as caught:
            verifier.prepare(receipt, repo)
        self.assertEqual(caught.exception.code, "APPLY_RECEIPT_SUBJECT_MISMATCH")
        git("worktree", "remove", "--force", str(patch_worktree))


if __name__ == "__main__":
    unittest.main()
