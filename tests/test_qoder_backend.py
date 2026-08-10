from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime import server
from agent_runtime.backends.base import BackendActivity, BackendResult, BackendStartRequest
from agent_runtime.backends.errors import BackendProtocolError
from agent_runtime.backends.fake import FakeBackend
from agent_runtime.backends.qoder.acp_client import AcpRunResult
from agent_runtime.backends.qoder.backend import QoderBackend
from agent_runtime.application.dispatch.repository_research import RepositoryResearchService


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
                model="Lite",
                cwd=str(self.cwd),
                timeout_seconds=10,
            )
            self.assertTrue(started["ok"], started)
            self.assertEqual(self.wait(started["task_id"])["state"], "completed")

        self.assertEqual(fake.starts[0].metadata["route"], "acp_read_only")
        self.assertEqual(server.task_result(started["task_id"])["answer"], "captain read-only")


    def test_captain_read_only_does_not_capture_preexisting_dirty_workspace_diff(self) -> None:
        subprocess.run(["git", "init"], cwd=self.cwd, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.cwd, check=True)
        subprocess.run(["git", "config", "user.name", "TP Voyager Tests"], cwd=self.cwd, check=True)
        (self.cwd / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.cwd, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.cwd, check=True, stdout=subprocess.DEVNULL)
        (self.cwd / "unrelated.txt").write_text("dirty before dispatch\n", encoding="utf-8")
        fake = FakeBackend(
            result=BackendResult(
                backend="qoder", stop_reason="end_turn", answer="read only",
                result={
                    "backend": "qoder", "stopReason": "end_turn",
                    "changed_files": ["unrelated.txt"],
                    "artifacts": [{"path": "unrelated.txt", "kind": "file"}],
                },
                backend_session_id="qoder-readonly-dirty",
            )
        )
        with patch("agent_runtime.server._create_qoder_backend", return_value=fake):
            started = server.task_dispatch(
                objective="read README only", crew="qoder", task_kind="research", model="Lite",
                cwd=str(self.cwd), timeout_seconds=10,
                read_scope={"files": ["README.md"]},
            )
            self.assertTrue(started["ok"], started)
            self.assertEqual(self.wait(started["task_id"])["state"], "completed")
        result = server.task_result(started["task_id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["changed_files"], [])
        self.assertFalse(any(item.get("kind") == "patch" for item in result["artifacts"]))
        self.assertFalse(any(item.get("path") == "unrelated.txt" for item in result["artifacts"]))


    def test_repository_research_dispatch_produces_runtime_report_without_source_writes(self) -> None:
        target = Path(self.tmp.name) / "repo-research"
        calls: list[list[str]] = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            if argv[:2] == ["git", "clone"]:
                source = Path(argv[-1])
                source.mkdir(parents=True)
                (source / ".git").mkdir()
                (source / "README.md").write_text("upstream source\n", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[:3] == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(returncode=0, stdout="deadbeef\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        research_service = RepositoryResearchService(
            metadata_loader=lambda owner, repo: {"size": 1, "private": False},
            runner=runner,
        )
        fake = FakeBackend(
            result=BackendResult(
                backend="qoder", stop_reason="end_turn", answer="Static findings only.",
                result={"backend": "qoder", "stopReason": "end_turn"},
                backend_session_id="qoder-repository-research",
            )
        )
        with patch("agent_runtime.server.RepositoryResearchService", return_value=research_service), patch(
            "agent_runtime.server._create_qoder_backend", return_value=fake
        ):
            started = server.task_dispatch(
                objective="Study the repository and summarize architecture",
                crew="qoder", task_kind="repository_research", model="Lite",
                read_scope={"files": ["README.md"], "max_files": 10, "max_bytes": 1024},
                repository_research={
                    "url": "https://github.com/example/project",
                    "target_directory": str(target),
                    "max_size_bytes": 1024 * 1024,
                    "report_path": "reports/research.md",
                },
                idempotency_key="repo-research-idem",
                timeout_seconds=10,
            )
            self.assertTrue(started["ok"], started)
            self.assertEqual(self.wait(started["task_id"])["state"], "completed")
            replay = server.task_dispatch(
                objective="Study the repository and summarize architecture",
                crew="qoder", task_kind="repository_research", model="Lite",
                read_scope={"files": ["README.md"], "max_files": 10, "max_bytes": 1024},
                repository_research={
                    "url": "https://github.com/example/project",
                    "target_directory": str(target),
                    "max_size_bytes": 1024 * 1024,
                    "report_path": "reports/research.md",
                },
                idempotency_key="repo-research-idem", timeout_seconds=10,
            )
            self.assertTrue(replay["ok"], replay)
            self.assertTrue(replay["replayed"])
            self.assertFalse(replay["dispatch_performed"])
            self.assertEqual(replay["task_id"], started["task_id"])
            conflict = server.task_dispatch(
                objective="Different research objective",
                crew="qoder", task_kind="repository_research", model="Lite",
                read_scope={"files": ["README.md"], "max_files": 10, "max_bytes": 1024},
                repository_research={
                    "url": "https://github.com/example/project",
                    "target_directory": str(target),
                    "max_size_bytes": 1024 * 1024,
                    "report_path": "reports/research.md",
                },
                idempotency_key="repo-research-idem", timeout_seconds=10,
            )
            self.assertFalse(conflict["ok"])
            self.assertEqual(conflict["reason_code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(sum(1 for argv in calls if argv[:2] == ["git", "clone"]), 1)
        result = server.task_result(started["task_id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["changed_files"], [])
        report = next(item for item in result["artifacts"] if item.get("kind") == "report")
        self.assertEqual(report["name"], "research.md")
        report_text = (target / "reports" / "research.md").read_text(encoding="utf-8")
        self.assertIn("Static findings only.", report_text)
        self.assertIn("https://github.com/example/project", report_text)
        self.assertEqual((target / "source" / "README.md").read_text(encoding="utf-8"), "upstream source\n")
        self.assertIn(["git", "remote", "remove", "origin"], calls)
        self.assertEqual(fake.starts[0].metadata["route"], "acp_read_only")
        self.assertEqual(fake.starts[0].metadata["routing_metadata"]["repository_research"]["commit"], "deadbeef")


    def test_repository_research_reuses_snapshot_for_later_scope_segment(self) -> None:
        target = Path(self.tmp.name) / "segmented-research"
        calls: list[list[str]] = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            if argv[:2] == ["git", "clone"]:
                source = Path(argv[-1])
                source.mkdir(parents=True)
                (source / ".git").mkdir()
                for index in range(300):
                    (source / f"f{index:03d}.txt").write_text("x" * 32, encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[:3] == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(returncode=0, stdout="deadbeef\n", stderr="")
            if argv[:3] == ["git", "status", "--porcelain=v1"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        research_service = RepositoryResearchService(
            metadata_loader=lambda owner, repo: {"size": 1, "private": False}, runner=runner,
        )
        fake = FakeBackend(
            result=BackendResult(
                backend="qoder", stop_reason="end_turn", answer="Segment findings.",
                result={"backend": "qoder", "stopReason": "end_turn"},
                backend_session_id="qoder-segmented-research",
            )
        )
        with patch("agent_runtime.server.RepositoryResearchService", return_value=research_service), patch(
            "agent_runtime.server._create_qoder_backend", return_value=fake
        ):
            first = server.task_dispatch(
                objective="Study segment zero", crew="qoder", task_kind="repository_research", model="Lite",
                read_scope={"globs": ["*.txt"], "max_files": 128, "max_bytes": 4096},
                repository_research={
                    "url": "https://github.com/example/segmented",
                    "target_directory": str(target), "max_size_bytes": 1024 * 1024,
                    "report_path": "reports/seg0.md",
                },
                scope_segment={"index": 0}, idempotency_key="segment-0", timeout_seconds=10,
            )
            self.assertTrue(first["ok"], first)
            self.assertEqual(self.wait(first["task_id"])["state"], "completed")
            self.assertEqual(first["repository_research"]["scope_segment_index"], 0)
            self.assertGreater(first["repository_research"]["scope_segment_count"], 1)
            snapshot = first["repository_snapshot_ref"]

            second = server.task_dispatch(
                objective="Study segment one", crew="qoder", task_kind="repository_research", model="Lite",
                read_scope={"globs": ["*.txt"], "max_files": 128, "max_bytes": 4096},
                repository_research={
                    "url": "https://github.com/example/segmented",
                    "target_directory": str(target), "max_size_bytes": 1024 * 1024,
                    "report_path": "reports/seg1.md",
                },
                repository_snapshot_ref=snapshot, scope_segment={"index": 1},
                idempotency_key="segment-1", timeout_seconds=10,
            )
            self.assertTrue(second["ok"], second)
            self.assertEqual(self.wait(second["task_id"])["state"], "completed")
            self.assertEqual(second["repository_research"]["scope_segment_index"], 1)

        self.assertEqual(sum(1 for argv in calls if argv[:2] == ["git", "clone"]), 1)
        second_result = server.task_result(second["task_id"])
        self.assertEqual(second_result["repository_research"]["acquisition"], "runtime_snapshot_reuse")
        self.assertEqual(second_result["repository_research"]["snapshot_source_task_id"], first["task_id"])
        self.assertTrue((target / "reports" / "seg0.md").is_file())
        self.assertTrue((target / "reports" / "seg1.md").is_file())

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
