from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.backends.base import BackendActivity, BackendResult

from agent_runtime.application.dispatch.workspace import (
    PatchWorkspaceCleanupError,
    PatchWorkspaceError,
    PatchWorkspaceService,
)
from agent_runtime.domain.dispatch import CommandSpec, PatchPolicy
from agent_runtime.verification.artifacts.capture import ArtifactCaptureBatch, WorkspaceBaseline
from agent_runtime.verification.service import VerificationPlan, VerificationService


class PatchPolicyTests(unittest.TestCase):
    def test_policy_rejects_unsafe_paths_duplicate_commands_and_unknown_verification(self) -> None:
        with self.assertRaises(ValueError):
            PatchPolicy.from_dict({"allowed_paths": ["../outside"]})
        with self.assertRaises(ValueError):
            PatchPolicy.from_dict(
                {
                    "allowed_paths": ["src"],
                    "commands": [
                        {"id": "test", "argv": ["python", "-V"]},
                        {"id": "test", "argv": ["python", "-V"]},
                    ],
                }
            )
        with self.assertRaises(ValueError):
            PatchPolicy.from_dict(
                {
                    "allowed_paths": ["src"],
                    "verification_command_ids": ["missing"],
                }
            )

    def test_policy_keeps_mandatory_vendor_and_git_paths_forbidden(self) -> None:
        policy = PatchPolicy.from_dict({"allowed_paths": ["src"], "forbidden_paths": ["secrets"]})
        self.assertEqual(policy.allowed_paths, ("src",))
        self.assertIn(".git", policy.forbidden_paths)
        self.assertIn(".codebuddy", policy.forbidden_paths)
        self.assertIn(".qoder", policy.forbidden_paths)
        self.assertIn("secrets", policy.forbidden_paths)


class PatchWorkspaceTests(unittest.TestCase):
    @staticmethod
    def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        self.assertEqual(self._git(repo, "init").returncode, 0)
        self._git(repo, "config", "user.email", "tests@example.invalid")
        self._git(repo, "config", "user.name", "TP Voyager Tests")
        (repo / "src").mkdir()
        (repo / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.assertEqual(self._git(repo, "add", ".").returncode, 0)
        self.assertEqual(self._git(repo, "commit", "-m", "base").returncode, 0)
        return repo

    def test_prepare_creates_detached_isolated_worktree_and_cleanup_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            service = PatchWorkspaceService(root / "runtime-workspaces")
            workspace = service.prepare(repo, idempotency_key="task-1")
            source_before = (repo / "src" / "a.py").read_text(encoding="utf-8")
            worktree = Path(workspace.worktree_root)
            self.assertNotEqual(worktree, repo.resolve())
            self.assertTrue((worktree / "src" / "a.py").is_file())
            (worktree / "src" / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
            self.assertEqual((repo / "src" / "a.py").read_text(encoding="utf-8"), source_before)
            self.assertNotEqual(self._git(worktree, "status", "--porcelain=v1").stdout.strip(), "")
            service.cleanup(workspace)
            self.assertFalse(worktree.exists())
            self.assertTrue(repo.exists())

    def test_prepare_rejects_dirty_source_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / "src" / "a.py").write_text("DIRTY = True\n", encoding="utf-8")
            service = PatchWorkspaceService(root / "runtime-workspaces")
            with self.assertRaisesRegex(PatchWorkspaceError, "clean source"):
                service.prepare(repo)


class VerificationCommandSpecTests(unittest.TestCase):
    @staticmethod
    def _git(cwd: Path, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr.decode("utf-8", "replace"))

    def _repo_with_worker_patch(self, root: Path) -> tuple[Path, WorkspaceBaseline, ArtifactCaptureBatch]:
        repo = root / "repo"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "tests@example.invalid")
        self._git(repo, "config", "user.name", "TP Voyager Tests")
        (repo / "src").mkdir()
        file = repo / "src" / "a.py"
        file.write_text("VALUE = 1\n", encoding="utf-8")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "base")
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(repo), text=True).strip()
        baseline = WorkspaceBaseline(git_root=str(repo), head=head, dirty=False)
        file.write_text("VALUE = 2\n", encoding="utf-8")
        capture = ArtifactCaptureBatch(
            changed_files=["src/a.py"],
            patch_available=True,
            patch_line_count=2,
        )
        return repo, baseline, capture

    def test_argv_verification_runs_without_shell_and_records_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, baseline, capture = self._repo_with_worker_patch(Path(tmp))
            spec = CommandSpec("version", (sys.executable, "-c", "print('ok')"))
            plan = VerificationPlan(
                allowed_paths=("src",),
                command_specs=(spec,),
                max_changed_files=2,
                max_diff_lines=10,
                require_patch=True,
                command_timeout_seconds=10,
            )
            report = VerificationService().verify(
                task_id="task", attempt_id="attempt", cwd=repo, plan=plan, capture=capture, baseline=baseline
            )
            self.assertEqual(report.status, "PASSED")
            self.assertEqual(report.tests[0]["command_id"], "version")
            self.assertEqual(report.tests[0]["exit_code"], 0)
            stability = next(item for item in report.checks if item["name"] == "verification_workspace_stability")
            self.assertEqual(stability["status"], "passed")

    def test_verification_command_workspace_mutation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, baseline, capture = self._repo_with_worker_patch(Path(tmp))
            script = "from pathlib import Path; Path('src/a.py').write_text('VALUE = 999\\n')"
            spec = CommandSpec("mutator", (sys.executable, "-c", script))
            plan = VerificationPlan(
                allowed_paths=("src",),
                command_specs=(spec,),
                max_changed_files=2,
                max_diff_lines=10,
                require_patch=True,
                command_timeout_seconds=10,
            )
            report = VerificationService().verify(
                task_id="task", attempt_id="attempt", cwd=repo, plan=plan, capture=capture, baseline=baseline
            )
            self.assertEqual(report.status, "FAILED")
            stability = next(item for item in report.checks if item["name"] == "verification_workspace_stability")
            self.assertEqual(stability["status"], "failed")

    def test_diff_line_limit_fails_scope_without_running_extra_logic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, baseline, capture = self._repo_with_worker_patch(Path(tmp))
            capture.patch_line_count = 101
            plan = VerificationPlan(
                allowed_paths=("src",), max_changed_files=2, max_diff_lines=100, require_patch=True
            )
            report = VerificationService().verify(
                task_id="task", attempt_id="attempt", cwd=repo, plan=plan, capture=capture, baseline=baseline
            )
            self.assertEqual(report.status, "FAILED")
            scope = next(item for item in report.checks if item["name"] == "scope")
            self.assertEqual(scope["status"], "failed")


class PatchDurableIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._runtime_home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._environment = patch.dict(
            "os.environ", {"TP_VOYAGER_HOME": str(Path(self._runtime_home.name) / "home")}, clear=False
        )
        self._environment.start()

    def tearDown(self) -> None:
        self._environment.stop()
        self._runtime_home.cleanup()

    class _PatchBackend:
        def __init__(self) -> None:
            self.starts = []

        def start(self, request, callbacks):
            self.starts.append(request)
            callbacks.on_dispatch_accepted("patch-session")
            callbacks.on_activity(BackendActivity(kind="prompt_accepted", timestamp=0.0))
            target = Path(request.cwd) / "src" / "a.py"
            target.write_text("VALUE = 2\n", encoding="utf-8")
            result = BackendResult(
                backend="qoder",
                stop_reason="end_turn",
                answer="bounded patch complete",
                result={"backend": "qoder", "stopReason": "end_turn"},
                backend_session_id="patch-session",
            )
            callbacks.on_result(result)
            return result

        def resume(self, request, callbacks):
            raise AssertionError("resume not expected")

        def cancel(self, request):
            raise AssertionError("cancel not expected")

        def reconcile(self, request):
            raise AssertionError("reconcile not expected")

    def test_captain_patch_reuses_durable_core_captures_patch_verifies_and_cleans_worktree(self) -> None:
        # Import lazily because the real package depends on FastMCP; automated
        # CI/Windows has it, while local source-only runs may use the test stub.
        from agent_runtime import server

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            PatchWorkspaceTests._git(repo, "init")
            PatchWorkspaceTests._git(repo, "config", "user.email", "tests@example.invalid")
            PatchWorkspaceTests._git(repo, "config", "user.name", "TP Voyager Tests")
            (repo / "src").mkdir()
            source = repo / "src" / "a.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            PatchWorkspaceTests._git(repo, "add", ".")
            PatchWorkspaceTests._git(repo, "commit", "-m", "base")

            db_path = root / "runtime" / "runtime.db"
            db_path.parent.mkdir()
            server.configure_runtime_database(db_path)
            server.TASKS.clear()
            server.IDEMPOTENCY_TASKS.clear()
            backend = self._PatchBackend()
            try:
                with patch("agent_runtime.server._create_qoder_backend", return_value=backend):
                    started = server.task_dispatch(
                        objective="change VALUE to 2",
                        crew="qoder",
                        task_kind="small_patch",
                        model="lite",
                        cwd=str(repo),
                        access_mode="patch",
                        timeout_seconds=30,
                        patch_policy={
                            "allowed_paths": ["src"],
                            "commands": [
                                {
                                    "id": "verify",
                                    "argv": [
                                        sys.executable,
                                        "-c",
                                        "from pathlib import Path; assert 'VALUE = 2' in Path('src/a.py').read_text()",
                                    ],
                                }
                            ],
                            "verification_command_ids": ["verify"],
                            "max_changed_files": 1,
                            "max_diff_lines": 20,
                        },
                    )
                    self.assertTrue(started["ok"], started)
                    deadline = __import__("time").monotonic() + 10
                    while __import__("time").monotonic() < deadline:
                        state = server.subagent_status(started["task_id"])
                        if state.get("state") in {"completed", "failed", "cancelled"}:
                            break
                        __import__("time").sleep(0.05)
                    else:
                        self.fail("patch task did not reach terminal state")

                self.assertEqual(state["state"], "completed", state)
                result = server.task_result(started["task_id"])
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["verification"]["status"], "PASSED")
                self.assertEqual(result["execution_budget"]["max_task_duration_seconds"], 30.0)
                self.assertIsNotNone(result["execution_budget"]["elapsed_seconds"])
                self.assertIn("src/a.py", result["changed_files"])
                self.assertTrue(any(item.get("kind") == "patch" for item in result["artifacts"]))
                self.assertEqual(source.read_text(encoding="utf-8"), "VALUE = 1\n")
                self.assertEqual(list((db_path.parent / "workspaces").glob("patch-*")), [])
                self.assertEqual(len(backend.starts), 1)
                self.assertEqual(backend.starts[0].metadata["route"], "acp_patch")
            finally:
                server.TASKS.clear()
                server.IDEMPOTENCY_TASKS.clear()
                server.configure_runtime_database(None)
    def test_completed_is_not_visible_until_patch_workspace_cleanup_finishes(self) -> None:
        from agent_runtime import server

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            PatchWorkspaceTests._git(repo, "init")
            PatchWorkspaceTests._git(repo, "config", "user.email", "tests@example.invalid")
            PatchWorkspaceTests._git(repo, "config", "user.name", "TP Voyager Tests")
            (repo / "src").mkdir()
            (repo / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
            PatchWorkspaceTests._git(repo, "add", ".")
            PatchWorkspaceTests._git(repo, "commit", "-m", "base")

            db_path = root / "runtime" / "runtime.db"
            db_path.parent.mkdir()
            server.configure_runtime_database(db_path)
            server.TASKS.clear()
            server.IDEMPOTENCY_TASKS.clear()
            backend = self._PatchBackend()
            cleanup_entered = threading.Event()
            allow_cleanup = threading.Event()
            original_cleanup = PatchWorkspaceService.cleanup

            def gated_cleanup(service, workspace, *, source_root=None):
                cleanup_entered.set()
                if not allow_cleanup.wait(timeout=5):
                    raise AssertionError("cleanup gate timed out")
                return original_cleanup(service, workspace, source_root=source_root)

            try:
                with patch("agent_runtime.server._create_qoder_backend", return_value=backend), patch.object(
                    PatchWorkspaceService, "cleanup", gated_cleanup
                ):
                    started = server.task_dispatch(
                        objective="change VALUE to 2",
                        crew="qoder",
                        task_kind="small_patch",
                        model="lite",
                        cwd=str(repo),
                        access_mode="patch",
                        timeout_seconds=30,
                        patch_policy={
                            "allowed_paths": ["src"],
                            "commands": [{"id": "verify", "argv": [sys.executable, "-c", "from pathlib import Path; assert 'VALUE = 2' in Path('src/a.py').read_text()"]}],
                            "verification_command_ids": ["verify"],
                        },
                    )
                    self.assertTrue(started["ok"], started)
                    self.assertTrue(cleanup_entered.wait(timeout=5), "terminal cleanup was not reached")
                    during_cleanup = server.subagent_status(started["task_id"])
                    self.assertNotEqual(during_cleanup.get("state"), "completed", during_cleanup)
                    allow_cleanup.set()
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        state = server.subagent_status(started["task_id"])
                        if state.get("state") in {"completed", "failed", "cancelled"}:
                            break
                        time.sleep(0.05)
                    else:
                        self.fail("patch task did not reach terminal state")
                self.assertEqual(state["state"], "completed", state)
                self.assertEqual(list((db_path.parent / "workspaces").glob("patch-*")), [])
            finally:
                allow_cleanup.set()
                server.TASKS.clear()
                server.IDEMPOTENCY_TASKS.clear()
                server.configure_runtime_database(None)

    def test_patch_cleanup_failure_is_terminal_failure_not_success(self) -> None:
        from agent_runtime import server

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            PatchWorkspaceTests._git(repo, "init")
            PatchWorkspaceTests._git(repo, "config", "user.email", "tests@example.invalid")
            PatchWorkspaceTests._git(repo, "config", "user.name", "TP Voyager Tests")
            (repo / "src").mkdir()
            (repo / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
            PatchWorkspaceTests._git(repo, "add", ".")
            PatchWorkspaceTests._git(repo, "commit", "-m", "base")

            db_path = root / "runtime" / "runtime.db"
            db_path.parent.mkdir()
            server.configure_runtime_database(db_path)
            server.TASKS.clear()
            server.IDEMPOTENCY_TASKS.clear()
            backend = self._PatchBackend()
            original_cleanup = PatchWorkspaceService.cleanup
            calls = {"count": 0}

            def fail_once(service, workspace, *, source_root=None):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise PatchWorkspaceCleanupError("injected cleanup failure")
                return original_cleanup(service, workspace, source_root=source_root)

            try:
                with patch("agent_runtime.server._create_qoder_backend", return_value=backend), patch.object(
                    PatchWorkspaceService, "cleanup", fail_once
                ):
                    started = server.task_dispatch(
                        objective="change VALUE to 2",
                        crew="qoder",
                        task_kind="small_patch",
                        model="lite",
                        cwd=str(repo),
                        access_mode="patch",
                        timeout_seconds=30,
                        patch_policy={
                            "allowed_paths": ["src"],
                            "commands": [{"id": "verify", "argv": [sys.executable, "-V"]}],
                            "verification_command_ids": ["verify"],
                        },
                    )
                    self.assertTrue(started["ok"], started)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        state = server.subagent_status(started["task_id"])
                        if state.get("state") in {"completed", "failed", "cancelled"}:
                            break
                        time.sleep(0.05)
                    else:
                        self.fail("patch task did not reach terminal state")
                self.assertEqual(state["state"], "failed", state)
                self.assertEqual(state["terminal_reason"], "PatchWorkspaceCleanupError")
                self.assertFalse(server.task_result(started["task_id"])["ok"])
                self.assertEqual(list((db_path.parent / "workspaces").glob("patch-*")), [])
            finally:
                server.TASKS.clear()
                server.IDEMPOTENCY_TASKS.clear()
                server.configure_runtime_database(None)


if __name__ == "__main__":
    unittest.main()
