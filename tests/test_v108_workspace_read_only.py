from __future__ import annotations

import tempfile

import pytest
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.application.context_service import ContextError, ProjectContextService
from agent_runtime.application.crew import CrewProvider, CrewRegistryService
from agent_runtime.application.dispatch import CaptainDispatchService
from agent_runtime.backends.base import BackendActivity, BackendResult, BackendStartRequest
from agent_runtime.backends.qoder.acp_client import AcpRunResult
from agent_runtime.backends.qoder.backend import QoderBackend
from agent_runtime.backends.workspace_snapshot import materialize_workspace_snapshot
from agent_runtime.backends.qoder.captain_dispatch import QoderReadOnlyDispatcher
from agent_runtime.domain.crew import CrewDescriptor
from agent_runtime.domain.dispatch import CaptainDispatchRequest, ReadScope, _MANDATORY_FORBIDDEN
from agent_runtime.persistence.database import Database


def build_large_workspace(
    root: Path,
    *,
    file_count: int = 300,
    bytes_per_file: int = 48 * 1024,
) -> Path:
    workspace = root / "workspace"
    src = workspace / "src"
    src.mkdir(parents=True)
    payload = "x" * bytes_per_file

    for index in range(file_count):
        (src / f"File{index:04d}.java").write_text(
            f"class File{index:04d} {{ /* {payload} */ }}\n",
            encoding="utf-8",
        )

    return workspace


def _ready_registry() -> CrewRegistryService:
    descriptor = CrewDescriptor(
        backend="qoder",
        display_name="Qoder",
        maturity="official",
        official_sources=("https://example.invalid/qoder",),
        capabilities=("analyze_context", "verify_commands"),
        controlled_capabilities=("analyze_context", "verify_commands"),
        documented_routes=("acp",),
        implemented_routes=("acp_read_only", "acp_verify"),
        dispatch_ready=True,
    )
    return CrewRegistryService({"qoder": CrewProvider(descriptor)})


class V108WorkspaceReadOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _service(self):
        calls: list[CaptainDispatchRequest] = []
        service = CaptainDispatchService(
            _ready_registry(),
            {"qoder": lambda request: calls.append(request) or {"ok": True, "task_id": "task-v108"}},
        )
        return service, calls

    def test_snapshot_prunes_nested_sensitive_directories_in_aggregate_workspace(self) -> None:
        workspace = self.root / "aggregate"
        repo = workspace / "dev" / "TP_Voyager-Dev"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (repo / ".git" / "refs" / "codex" / "turn-diffs" / ("x" * 80)).mkdir(parents=True)
        (repo / ".git" / "refs" / "codex" / "turn-diffs" / ("x" * 80) / "ref").write_text("deadbeef\n", encoding="utf-8")
        (workspace / "other" / ".codebuddy" / "cache").mkdir(parents=True)
        (workspace / "other" / ".codebuddy" / "cache" / "state.json").write_text("{}", encoding="utf-8")
        (workspace / "tools" / ".qoder" / "sessions").mkdir(parents=True)
        (workspace / "tools" / ".qoder" / "sessions" / "s.json").write_text("{}", encoding="utf-8")
        (workspace / "tools" / ".codex" / "state").mkdir(parents=True)
        (workspace / "tools" / ".codex" / "state" / "session.json").write_text("{}", encoding="utf-8")
        (workspace / "web" / "node_modules" / "pkg").mkdir(parents=True)
        (workspace / "web" / "node_modules" / "pkg" / "index.js").write_text("module.exports = {}", encoding="utf-8")
        (workspace / "py" / ".venv" / "Lib").mkdir(parents=True)
        (workspace / "py" / ".venv" / "Lib" / "site.py").write_text("# generated", encoding="utf-8")
        (workspace / "py" / "pkg" / "__pycache__").mkdir(parents=True)
        (workspace / "py" / "pkg" / "__pycache__" / "mod.pyc").write_bytes(b"cache")

        temp, snapshot = materialize_workspace_snapshot(str(workspace))
        try:
            self.assertTrue((snapshot / "dev" / "TP_Voyager-Dev" / "src" / "main.py").is_file())
            self.assertFalse((snapshot / "dev" / "TP_Voyager-Dev" / ".git").exists())
            self.assertFalse((snapshot / "other" / ".codebuddy").exists())
            self.assertFalse((snapshot / "tools" / ".qoder").exists())
            self.assertFalse((snapshot / "tools" / ".codex").exists())
            self.assertFalse((snapshot / "web" / "node_modules").exists())
            self.assertFalse((snapshot / "py" / ".venv").exists())
            self.assertFalse((snapshot / "py" / "pkg" / "__pycache__").exists())
        finally:
            temp.cleanup()

    def test_snapshot_wraps_copy_os_error_with_bounded_relative_context(self) -> None:
        from agent_runtime.backends import workspace_snapshot as snapshot_module

        workspace = self.root / "aggregate-error"
        (workspace / "src").mkdir(parents=True)
        (workspace / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

        with patch.object(
            snapshot_module.shutil,
            "copyfile",
            side_effect=FileNotFoundError(3, "The system cannot find the path specified"),
        ):
            with self.assertRaises(RuntimeError) as caught:
                materialize_workspace_snapshot(str(workspace))

        self.assertEqual(type(caught.exception).__name__, "WorkspaceSnapshotError")
        self.assertIn("src/main.py", str(caught.exception).replace("\\", "/"))
        self.assertNotIn(str(workspace), str(caught.exception))

    def test_large_workspace_fixture_exceeds_legacy_read_scope_capacity(self) -> None:
        workspace = build_large_workspace(self.root)
        files = list((workspace / "src").glob("*.java"))
        self.assertGreater(len(files), 256)
        self.assertGreater(sum(path.stat().st_size for path in files), 12 * 1024 * 1024)

    def test_normal_read_only_task_kinds_dispatch_without_read_scope(self) -> None:
        workspace = build_large_workspace(self.root)
        service, calls = self._service()

        for task_kind in ("research", "code_review", "test_failure_triage"):
            with self.subTest(task_kind=task_kind):
                result = service.dispatch(
                    CaptainDispatchRequest(
                        objective=f"analyze {task_kind}",
                        crew="qoder",
                        task_kind=task_kind,
                        model="lite",
                        access_mode="read_only",
                        cwd=str(workspace),
                    )
                )
                self.assertTrue(result["ok"], result)

        self.assertEqual([request.task_kind for request in calls], ["research", "code_review", "test_failure_triage"])
        self.assertTrue(all(request.read_scope is None for request in calls))
        self.assertTrue(all(request.resolved_read_files == () for request in calls))

    def test_explicit_read_scope_stays_bounded_and_fails_closed(self) -> None:
        workspace = build_large_workspace(self.root, file_count=64, bytes_per_file=32 * 1024)
        db = Database(self.root / "runtime.db")
        db.initialize()
        service = ProjectContextService(db)

        scope = ReadScope.from_dict(
            {
                "directories": ["src"],
                "max_files": 32,
                "max_bytes": 1024 * 1024,
            }
        )
        with self.assertRaises(ContextError):
            service.resolve_read_scope(str(workspace), scope)

    def test_repository_research_without_scope_is_still_rejected(self) -> None:
        service, calls = self._service()
        result = service.dispatch(
            CaptainDispatchRequest(
                objective="research external repository",
                crew="qoder",
                task_kind="repository_research",
                model="lite",
                access_mode="read_only",
                repository_research={"verified": True},
            )
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "REPOSITORY_RESEARCH_SCOPE_REQUIRED")
        self.assertEqual(calls, [])

    def test_verification_without_existing_contract_is_still_rejected(self) -> None:
        service, calls = self._service()
        result = service.dispatch(
            CaptainDispatchRequest(
                objective="verify accepted patch",
                crew="qoder",
                task_kind="verify_only",
                model="lite",
                access_mode="verification",
            )
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "APPLY_RECEIPT_REQUIRED")
        self.assertEqual(calls, [])

    def test_captain_skill_defaults_normal_workspace_read_only_to_no_read_scope(self) -> None:
        skill = Path("skills/tp-voyager-captain/SKILL.md").read_text(encoding="utf-8")
        readme = Path("skills/tp-voyager-captain/README.md").read_text(encoding="utf-8")
        combined = f"{skill}\n{readme}".lower()

        self.assertIn("normal workspace read-only", combined)
        self.assertIn("do not provide `read_scope` by default", combined)
        self.assertIn("explicit frozen/bounded corpus", combined)

        repo_section = skill.lower().split("## 7. controlled repository research pattern", 1)[1]
        self.assertIn("read_scope", repo_section)
        self.assertIn("max_files", repo_section)
        self.assertIn("max_bytes", repo_section)


class _QoderCallbacks:
    def __init__(self) -> None:
        self.accepted: list[str] = []
        self.results: list[BackendResult] = []

    def on_dispatch_accepted(self, session_id: str) -> None:
        self.accepted.append(session_id)

    def on_activity(self, activity: BackendActivity) -> None:
        return None

    def on_result(self, result: BackendResult) -> None:
        self.results.append(result)


class _QoderClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = dict(kwargs)
        self.process = type("P", (), {"poll": lambda self: 0})()

    def run(self, **kwargs):
        kwargs["on_dispatch_accepted"]("qoder-v108")
        return AcpRunResult(
            session_id="qoder-v108",
            stop_reason="end_turn",
            answer="ok",
            observability={"route": "acp"},
        )

    def usage_snapshot(self):
        return {}

    def cancel(self, session_id="") -> None:
        return None

    def close(self) -> None:
        return None


class V108QoderWorkspaceReadOnlyTests(unittest.TestCase):
    def test_no_scope_uses_sensitive_free_snapshot_and_read_only_vendor_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "README.md").write_text("readme\n", encoding="utf-8")
            (workspace / ".env").write_text("SECRET_TOKEN=snapshot-leak-test\n", encoding="utf-8")
            (workspace / "secret.pem").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
            (workspace / ".git").mkdir()
            (workspace / ".git" / "config").write_text("[core]\n", encoding="utf-8")
            calls: list[dict] = []
            observed: dict[str, bool] = {}

            def factory(**kwargs):
                cwd = Path(kwargs["cwd"])
                calls.append(dict(kwargs))
                # snapshot content is inspectable before backend cleanup
                observed["snapshot_has_readme"] = (cwd / "README.md").is_file()
                observed["snapshot_has_env"] = (cwd / ".env").exists()
                observed["snapshot_has_pem"] = (cwd / "secret.pem").exists()
                observed["snapshot_has_git"] = (cwd / ".git").exists()
                return _QoderClient(**kwargs)

            request = BackendStartRequest(
                task_id="qoder-v108-broad",
                attempt_id="at-qoder-v108-broad",
                runtime_session_id="rs-qoder-v108-broad",
                prompt="inspect workspace",
                cwd=str(workspace),
                context_window_tokens=200000,
                metadata={"route": "acp_read_only", "routing_metadata": {}},
            )

            result = QoderBackend(read_only_acp_client_factory=factory).start(
                request, _QoderCallbacks()
            )

            self.assertEqual(result.answer, "ok")
            self.assertEqual(len(calls), 1)
            # no-scope read-only runs against a snapshot, not the live cwd
            self.assertNotEqual(Path(calls[0]["cwd"]).resolve(), workspace.resolve())
            # sensitive paths are physically excluded from the snapshot
            self.assertTrue(observed["snapshot_has_readme"])
            self.assertFalse(observed["snapshot_has_env"])
            self.assertFalse(observed["snapshot_has_pem"])
            self.assertFalse(observed["snapshot_has_git"])
            self.assertEqual(calls[0]["context_window_tokens"], 200000)
            self.assertEqual(calls[0]["forbidden_paths"], _MANDATORY_FORBIDDEN)
            self.assertEqual(calls[0]["visible_tools"], ("Read", "Grep", "Glob"))
            self.assertEqual(calls[0]["allowed_tools"], ("Read", "Grep", "Glob"))
            self.assertNotIn("allowed_paths", calls[0])

    def test_explicit_scope_still_materializes_snapshot_and_uses_same_tool_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "approved.txt").write_text("approved\n", encoding="utf-8")
            calls: list[dict] = []

            def factory(**kwargs):
                calls.append(dict(kwargs))
                return _QoderClient(**kwargs)

            request = BackendStartRequest(
                task_id="qoder-v108-bounded",
                attempt_id="at-qoder-v108-bounded",
                runtime_session_id="rs-qoder-v108-bounded",
                prompt="inspect bounded corpus",
                cwd=str(workspace),
                metadata={
                    "route": "acp_read_only",
                    "routing_metadata": {
                        "read_scope": {"resolved_files": ["approved.txt"]}
                    },
                },
            )
            QoderBackend(read_only_acp_client_factory=factory).start(
                request, _QoderCallbacks()
            )

            self.assertEqual(len(calls), 1)
            self.assertNotEqual(Path(calls[0]["cwd"]).resolve(), workspace.resolve())
            self.assertEqual(calls[0]["allowed_paths"], ("approved.txt",))
            self.assertEqual(calls[0]["visible_tools"], ("Read", "Grep", "Glob"))
            self.assertEqual(calls[0]["allowed_tools"], ("Read", "Grep", "Glob"))


@pytest.mark.parametrize("file_count", [32, 64, 150, 220, 300, 1000])
def test_normal_workspace_size_is_not_an_admission_limit(tmp_path: Path, file_count: int) -> None:
    workspace = build_large_workspace(
        tmp_path, file_count=file_count, bytes_per_file=256
    )
    calls: list[CaptainDispatchRequest] = []
    service = CaptainDispatchService(
        _ready_registry(),
        {"qoder": lambda request: calls.append(request) or {"ok": True, "task_id": "size-gate"}},
    )

    result = service.dispatch(
        CaptainDispatchRequest(
            objective="trace a cross-file call chain",
            crew="qoder",
            task_kind="research",
            model="lite",
            access_mode="read_only",
            cwd=str(workspace),
        )
    )

    assert result["ok"], result
    assert len(calls) == 1
    assert calls[0].read_scope is None


def test_normal_workspace_over_12_mib_is_accepted_without_read_scope(tmp_path: Path) -> None:
    workspace = build_large_workspace(tmp_path)
    total_bytes = sum(path.stat().st_size for path in (workspace / "src").glob("*.java"))
    assert total_bytes > 12 * 1024 * 1024
    calls: list[CaptainDispatchRequest] = []
    service = CaptainDispatchService(
        _ready_registry(),
        {"qoder": lambda request: calls.append(request) or {"ok": True, "task_id": "bytes-gate"}},
    )

    result = service.dispatch(
        CaptainDispatchRequest(
            objective="analyze the large Java workspace",
            crew="qoder",
            task_kind="code_review",
            model="lite",
            access_mode="read_only",
            cwd=str(workspace),
        )
    )

    assert result["ok"], result
    assert calls[0].read_scope is None


def test_qoder_explicit_scope_uses_routing_metadata_not_task_launch_allowed_paths(tmp_path: Path) -> None:
    class Launch:
        def __init__(self) -> None:
            self.requests = []

        def start(self, request):
            self.requests.append(request)
            return {"ok": True, "task_id": "qoder-scope-truth"}

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("bounded\n", encoding="utf-8")
    launch = Launch()
    scope = ReadScope.from_dict(
        {"files": ["README.md"], "max_files": 10, "max_bytes": 1024}
    )
    result = QoderReadOnlyDispatcher(launch)(
        CaptainDispatchRequest(
            objective="inspect bounded input",
            crew="qoder",
            task_kind="research",
            model="lite",
            access_mode="read_only",
            cwd=str(workspace),
            read_scope=scope,
            resolved_read_files=("README.md",),
        )
    )

    assert result["ok"], result
    request = launch.requests[0]
    assert request.routing_metadata["read_scope"]["resolved_files"] == ["README.md"]
    assert request.allowed_paths is None



if __name__ == "__main__":
    unittest.main()
