from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.backends.base import BackendActivity, BackendResult, BackendStartRequest
from agent_runtime.backends.codebuddy.backend import CodeBuddyBackend
from agent_runtime.backends.codebuddy.captain_dispatch import CodeBuddyContextReadOnlyDispatcher
from agent_runtime.backends.codebuddy.sdk_client import CodeBuddySdkClient
from agent_runtime.backends.codebuddy.process import probe_codebuddy_cli
from agent_runtime.backends.errors import BackendProtocolError
from agent_runtime.application.context_service import ProjectContextService
from agent_runtime.application.task_service import TaskService
from agent_runtime.application.dispatch.repository_research import RepositoryResearchService
from agent_runtime.domain.dispatch import CaptainDispatchRequest, ModelParameters, _MANDATORY_FORBIDDEN
from agent_runtime.domain.artifact import Artifact
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database


class FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakePermissionResultDeny:
    def __init__(self, *, message: str, interrupt: bool = False):
        self.message = message
        self.interrupt = interrupt
        self.behavior = "deny"


class FakePermissionResultAllow:
    def __init__(self, *, updated_input):
        self.updated_input = updated_input
        self.behavior = "allow"


class TextBlock:
    def __init__(self, text: str):
        self.text = text


class AssistantMessage:
    def __init__(self, text: str):
        self.content = [TextBlock(text)]


class ResultMessage:
    def __init__(self, session_id: str = "cb-session"):
        self.subtype = "success"
        self.duration_ms = 25
        self.duration_api_ms = 20
        self.is_error = False
        self.num_turns = 1
        self.session_id = session_id
        self.total_cost_usd = 0.01
        self.usage = {"input_tokens": 10, "output_tokens": 2}
        self.result = "sdk answer"


class FakeSdkClient:
    last_options = None
    events: list[str] = []

    def __init__(self, *, options):
        FakeSdkClient.last_options = options
        self.options = options
        self.disconnected = False
        self.interrupted = False

    async def connect(self):
        FakeSdkClient.events.append("connect")

    async def query(self, prompt: str, session_id: str = "default"):
        self.prompt = prompt
        self.session_id = session_id
        FakeSdkClient.events.append("query")

    def receive_response(self):
        async def gen():
            yield AssistantMessage("partial")
            yield ResultMessage(self.session_id)
        return gen()

    async def disconnect(self):
        self.disconnected = True
        FakeSdkClient.events.append("disconnect")

    async def interrupt(self):
        self.interrupted = True
        FakeSdkClient.events.append("interrupt")


class FakeSdkModule:
    CodeBuddyAgentOptions = FakeOptions
    CodeBuddySDKClient = FakeSdkClient
    PermissionResultDeny = FakePermissionResultDeny
    PermissionResultAllow = FakePermissionResultAllow


class Callbacks:
    def __init__(self) -> None:
        self.accepted: list[str] = []
        self.activities: list[str] = []
        self.results: list[BackendResult] = []

    def on_dispatch_accepted(self, session_id: str) -> None:
        self.accepted.append(session_id)
        FakeSdkClient.events.append("accepted")

    def on_activity(self, activity: BackendActivity) -> None:
        self.activities.append(activity.kind)

    def on_result(self, result: BackendResult) -> None:
        self.results.append(result)


class FakeSyncSdkTransport:
    def __init__(self, *, cwd, on_activity, **kwargs):
        self.cwd = cwd
        self.on_activity = on_activity
        self.kwargs = dict(kwargs)
        self.running = False
        self.cancelled = False

    def run(self, **kwargs):
        from agent_runtime.backends.codebuddy.sdk_client import CodeBuddySdkRunResult

        kwargs["on_dispatch_accepted"]("cb-synthetic")
        self.on_activity(BackendActivity(kind="stream_activity", timestamp=1.0))
        return CodeBuddySdkRunResult(
            session_id="cb-real",
            stop_reason="success",
            answer="codebuddy answer",
            observability={"route": "sdk_context_read_only", "native_tools_enabled": False},
            usage={"input_tokens": 5},
            total_cost_usd=0.001,
        )

    def cancel(self):
        self.cancelled = True

    def close(self):
        pass


class CodeBuddyProbeTests(unittest.TestCase):
    def test_probe_defaults_to_cn_and_requires_official_sdk_for_dispatch_readiness(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="2.121.1\n", stderr="")
        with (
            patch("agent_runtime.backends.codebuddy.process.resolve_codebuddy_cli", return_value="codebuddy"),
            patch("agent_runtime.backends.codebuddy.process.subprocess.run", return_value=completed),
            patch("agent_runtime.backends.codebuddy.process.importlib.util.find_spec", return_value=object()),
            patch.dict("os.environ", {}, clear=True),
            patch.object(Path, "home", return_value=Path(tempfile.gettempdir())),
        ):
            result = probe_codebuddy_cli()
        self.assertTrue(result["installed"])
        self.assertTrue(result["sdk_installed"])
        self.assertEqual(result["region"], "cn")
        self.assertTrue(result["dispatch_ready"])
        self.assertFalse(result["auth_probe_performed"])



class CodeBuddySdkClientTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSdkClient.events = []
        FakeSdkClient.last_options = None

    def test_workspace_read_only_policy_allows_only_read_search_tools_in_plan_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / ".git").mkdir()
            (root / "src" / "x.py").write_text("VALUE = 1\n", encoding="utf-8")
            client = CodeBuddySdkClient(
                cwd=tmp,
                on_activity=lambda activity: None,
                sdk_module=FakeSdkModule,
                region="cn",
                access_mode="read_only",
                forbidden_paths=(".git", ".codebuddy", ".qoder"),
            )
            accepted: list[str] = []
            def accept(value: str) -> None:
                accepted.append(value)
                FakeSdkClient.events.append("accepted")
            result = client.run(
                prompt="analyze workspace",
                model="hy3",
                reasoning_effort="high",
                idle_timeout_seconds=5,
                max_task_duration_seconds=30,
                on_dispatch_accepted=accept,
            )

            self.assertEqual(result.session_id, accepted[0])
            self.assertEqual(result.answer, "sdk answer")
            self.assertEqual(result.observability["native_tools_enabled"], True)
            options = FakeSdkClient.last_options.kwargs
            self.assertEqual(options["permission_mode"], "plan")
            self.assertEqual(options["allowed_tools"], ["Glob", "Grep", "Read"])
            self.assertNotIn("Read", options["disallowed_tools"])
            self.assertNotIn("Glob", options["disallowed_tools"])
            self.assertNotIn("Grep", options["disallowed_tools"])
            self.assertIn("Bash", options["disallowed_tools"])
            self.assertIn("Edit", options["disallowed_tools"])
            self.assertIn("Task", options["disallowed_tools"])
            self.assertIn("WebFetch", options["disallowed_tools"])
            self.assertEqual(options["mcp_servers"], {})
            self.assertEqual(options["setting_sources"], [])
            self.assertEqual(options["env"]["CODEBUDDY_INTERNET_ENVIRONMENT"], "internal")
            self.assertEqual(options["model"], "hy3")
            self.assertEqual(options["effort"], "high")
            import uuid
            self.assertEqual(str(uuid.UUID(accepted[0])), accepted[0])
            self.assertLess(FakeSdkClient.events.index("accepted"), FakeSdkClient.events.index("query"))

            authorize = options["can_use_tool"]
            read = asyncio.run(authorize("Read", {"file_path": "src/x.py"}, object()))
            outside = asyncio.run(authorize("Read", {"file_path": str(root.parent / "outside.py")}, object()))
            forbidden = asyncio.run(authorize("Read", {"file_path": ".git/config"}, object()))
            glob = asyncio.run(authorize("Glob", {"pattern": "**/*.py"}, object()))
            grep = asyncio.run(authorize("Grep", {"pattern": "VALUE"}, object()))
            edit = asyncio.run(authorize("Edit", {"file_path": "src/x.py"}, object()))
            bash = asyncio.run(authorize("Bash", {"command": "echo no"}, object()))
            web = asyncio.run(authorize("WebFetch", {"url": "https://example.com"}, object()))
            task = asyncio.run(authorize("Task", {"prompt": "spawn"}, object()))
            unknown = asyncio.run(authorize("FutureTool", {}, object()))

            self.assertEqual(read.behavior, "allow")
            self.assertEqual(outside.behavior, "deny")
            self.assertEqual(forbidden.behavior, "deny")
            self.assertEqual(glob.behavior, "allow")
            self.assertEqual(glob.updated_input["path"], ".")
            self.assertEqual(grep.behavior, "allow")
            self.assertEqual(grep.updated_input["path"], ".")
            for denied in (edit, bash, web, task, unknown):
                self.assertEqual(denied.behavior, "deny")

    def test_frozen_context_read_only_keeps_all_native_tools_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = CodeBuddySdkClient(
                cwd=tmp,
                on_activity=lambda activity: None,
                sdk_module=FakeSdkModule,
                region="cn",
                access_mode="read_only",
                native_read_tools=False,
            )
            options = client._build_options(
                FakeSdkModule, resume_session_id="", model="", session_id="session"
            )
            self.assertEqual(options.kwargs["permission_mode"], "plan")
            self.assertEqual(options.kwargs["allowed_tools"], [])
            self.assertIn("Read", options.kwargs["disallowed_tools"])
            self.assertIn("Glob", options.kwargs["disallowed_tools"])
            self.assertIn("Grep", options.kwargs["disallowed_tools"])
            deny = asyncio.run(
                options.kwargs["can_use_tool"](
                    "Read", {"file_path": "anything.py"}, object()
                )
            )
            self.assertEqual(deny.behavior, "deny")

    def test_patch_policy_allows_only_bounded_paths_exact_commands_and_known_tools(self) -> None:
        from agent_runtime.domain.dispatch import CommandSpec

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            client = CodeBuddySdkClient(
                cwd=tmp,
                on_activity=lambda activity: None,
                sdk_module=FakeSdkModule,
                region="cn",
                access_mode="patch",
                allowed_paths=("src",),
                forbidden_paths=(".git", "secret"),
                command_specs=(CommandSpec("tests", ("python", "-m", "unittest")),),
            )
            options = client._build_options(FakeSdkModule, resume_session_id="", model="", session_id="session")
            authorize = options.kwargs["can_use_tool"]

            allowed_write = asyncio.run(
                authorize("Write", {"file_path": "src/a.py", "content": "x"}, object())
            )
            outside = asyncio.run(
                authorize("Write", {"file_path": "outside.py", "content": "x"}, object())
            )
            command = asyncio.run(
                authorize("Bash", {"command": 'python -m unittest'}, object())
            )
            unlisted = asyncio.run(
                authorize("Bash", {"command": 'python -c "print(1)"'}, object())
            )
            env_override = asyncio.run(
                authorize(
                    "Bash",
                    {"command": "python -m unittest", "env": {"PYTHONPATH": "outside"}},
                    object(),
                )
            )
            background = asyncio.run(
                authorize(
                    "Bash",
                    {"command": "python -m unittest", "run_in_background": True},
                    object(),
                )
            )
            unknown = asyncio.run(authorize("WebFetch", {"url": "https://example.com"}, object()))

        self.assertEqual(allowed_write.behavior, "allow")
        self.assertEqual(command.behavior, "allow")
        self.assertEqual(outside.behavior, "deny")
        self.assertEqual(unlisted.behavior, "deny")
        self.assertEqual(env_override.behavior, "deny")
        self.assertEqual(background.behavior, "deny")
        self.assertEqual(unknown.behavior, "deny")
        self.assertEqual(options.kwargs["permission_mode"], "default")
        self.assertEqual(options.kwargs["setting_sources"], [])
        self.assertIn("WebFetch", options.kwargs["disallowed_tools"])
        self.assertNotIn("Write", options.kwargs["disallowed_tools"])

    def test_bash_authorization_preserves_literal_argv_semantics(self) -> None:
        from agent_runtime.domain.dispatch import CommandSpec
        with tempfile.TemporaryDirectory() as tmp:
            client = CodeBuddySdkClient(
                cwd=tmp, on_activity=lambda activity: None, sdk_module=FakeSdkModule, region="cn",
                access_mode="patch", allowed_paths=("src",), forbidden_paths=(".git",),
                command_specs=(CommandSpec("literal", ("echo", "$HOME", "*", "$(whoami)")),),
            )
            options = client._build_options(FakeSdkModule, resume_session_id="", model="", session_id="session")
            authorize = options.kwargs["can_use_tool"]
            literal = asyncio.run(
                authorize("Bash", {"command": "echo '$HOME' '*' '$(whoami)'"}, object())
            )
            expanded = asyncio.run(
                authorize("Bash", {"command": "echo $HOME * $(whoami)"}, object())
            )
            self.assertEqual(literal.behavior, "allow")
            self.assertEqual(expanded.behavior, "deny")

    def test_verification_policy_denies_write_tools_and_allows_exact_command(self) -> None:
        from agent_runtime.domain.dispatch import CommandSpec
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("VALUE=1\n", encoding="utf-8")
            client = CodeBuddySdkClient(
                cwd=tmp, on_activity=lambda activity: None, sdk_module=FakeSdkModule, region="cn",
                access_mode="verification", allowed_paths=("src/a.py",), forbidden_paths=(".git",),
                command_specs=(CommandSpec("tests", ("python", "-m", "unittest")),),
            )
            options = client._build_options(FakeSdkModule, resume_session_id="", model="", session_id="session")
            authorize = options.kwargs["can_use_tool"]
            read = asyncio.run(authorize("Read", {"file_path": "src/a.py"}, object()))
            write = asyncio.run(authorize("Write", {"file_path": "src/a.py", "content": "x"}, object()))
            command = asyncio.run(authorize("Bash", {"command": "python -m unittest"}, object()))
            self.assertEqual(read.behavior, "allow")
            self.assertEqual(write.behavior, "deny")
            self.assertEqual(command.behavior, "allow")
            self.assertIn("Write", options.kwargs["disallowed_tools"])
            self.assertNotIn("Bash", options.kwargs["disallowed_tools"])

    def test_sdk_error_result_fails_closed(self) -> None:
        class ErrorClient(FakeSdkClient):
            def receive_response(self):
                async def gen():
                    message = ResultMessage(self.session_id)
                    message.is_error = True
                    message.result = "sensitive vendor error"
                    yield message
                return gen()

        class ErrorSdk(FakeSdkModule):
            CodeBuddySDKClient = ErrorClient

        with tempfile.TemporaryDirectory() as tmp:
            client = CodeBuddySdkClient(
                cwd=tmp,
                on_activity=lambda activity: None,
                sdk_module=ErrorSdk,
            )
            with self.assertRaisesRegex(BackendProtocolError, "error result"):
                client.run(
                    prompt="x",
                    idle_timeout_seconds=5,
                    max_task_duration_seconds=30,
                    on_dispatch_accepted=lambda value: None,
                )


class CodeBuddyBackendTests(unittest.TestCase):
    def request(self, route: str = "sdk_context_read_only") -> BackendStartRequest:
        return BackendStartRequest(
            task_id="cb-task",
            attempt_id="cb-attempt",
            runtime_session_id="cb-runtime-session",
            prompt="bounded context prompt",
            cwd=str(Path.cwd()),
            metadata={"route": route},
        )

    def test_backend_uses_shared_contract_and_returns_vendor_session(self) -> None:
        callbacks = Callbacks()
        backend = CodeBuddyBackend(sdk_client_factory=FakeSyncSdkTransport)
        result = backend.start(self.request(), callbacks)
        self.assertEqual(result.backend, "codebuddy")
        self.assertEqual(result.backend_session_id, "cb-real")
        self.assertEqual(result.answer, "codebuddy answer")
        self.assertEqual(callbacks.accepted, ["cb-synthetic"])
        self.assertEqual(len(callbacks.results), 1)

    def test_patch_route_passes_captain_policy_to_sdk_factory(self) -> None:
        calls = []

        class PatchTransport(FakeSyncSdkTransport):
            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                super().__init__(cwd=kwargs["cwd"], on_activity=kwargs["on_activity"])

        request = self.request("sdk_patch")
        request = BackendStartRequest(
            **{
                **request.__dict__,
                "metadata": {
                    "route": "sdk_patch",
                    "patch_policy": {
                        "allowed_paths": ["src"],
                        "forbidden_paths": [".git"],
                        "commands": [{"id": "verify", "argv": ["python", "-V"]}],
                    },
                },
            }
        )
        result = CodeBuddyBackend(sdk_client_factory=PatchTransport).start(request, Callbacks())
        self.assertEqual(result.backend, "codebuddy")
        self.assertEqual(calls[0]["access_mode"], "patch")
        self.assertEqual(calls[0]["allowed_paths"], ("src",))
        self.assertEqual(calls[0]["command_specs"][0].command_id, "verify")

    def test_read_only_backend_enables_native_reads_only_for_vendor_workspace_delivery(self) -> None:
        calls = []

        class ReadOnlyTransport(FakeSyncSdkTransport):
            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                super().__init__(**kwargs)

        base = self.request()
        workspace_request = BackendStartRequest(
            **{
                **base.__dict__,
                "task_id": "cb-workspace",
                "metadata": {
                    "route": "sdk_context_read_only",
                    "routing_metadata": {"context_delivery": "vendor_workspace"},
                },
            }
        )
        CodeBuddyBackend(sdk_client_factory=ReadOnlyTransport).start(workspace_request, Callbacks())
        frozen_request = BackendStartRequest(
            **{
                **base.__dict__,
                "task_id": "cb-frozen",
                "metadata": {
                    "route": "sdk_context_read_only",
                    "routing_metadata": {"context_delivery": "runtime_snapshot"},
                },
            }
        )
        CodeBuddyBackend(sdk_client_factory=ReadOnlyTransport).start(frozen_request, Callbacks())

        self.assertEqual(calls[0]["access_mode"], "read_only")
        self.assertEqual(calls[0]["forbidden_paths"], _MANDATORY_FORBIDDEN)
        self.assertTrue(calls[0]["native_read_tools"])
        self.assertFalse(calls[1]["native_read_tools"])

    def test_unsupported_route_is_rejected(self) -> None:
        backend = CodeBuddyBackend(sdk_client_factory=FakeSyncSdkTransport)
        with self.assertRaises(BackendProtocolError):
            backend.start(self.request("headless"), Callbacks())


class _FakeLaunchService:
    def __init__(self, result=None):
        self.requests = []
        self.result = result or {"ok": True, "task_id": "cb-task"}

    def start(self, request):
        self.requests.append(request)
        return dict(self.result)


class CodeBuddyCaptainDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "sample.py").write_text("VALUE = 7\n", encoding="utf-8")
        self.db = Database(Path(self.tmp.name) / "runtime.db")
        self.db.initialize()
        self.contexts = ProjectContextService(self.db)
        self.context = self.contexts.register(
            str(self.root), ["src/sample.py"], context_id="ctx-codebuddy"
        ).manifest

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def dispatcher(self, launch=None):
        return CodeBuddyContextReadOnlyDispatcher(
            launch or _FakeLaunchService(),
            self.contexts,
            preflight=lambda: None,
        )

    def request(self, **overrides):
        values = dict(
            objective="Explain VALUE from the supplied context",
            crew="codebuddy",
            task_kind="research",
            cwd=str(self.root),
            context_id="ctx-codebuddy",
            timeout_seconds=30,
        )
        values.update(overrides)
        return CaptainDispatchRequest(**values)

    def test_without_context_manifest_dispatches_workspace_native_read_only(self) -> None:
        launch = _FakeLaunchService()
        with (
            patch.object(self.contexts, "verify", side_effect=AssertionError("workspace mode must not verify context")),
            patch.object(self.contexts, "render", side_effect=AssertionError("workspace mode must not render context")),
        ):
            result = self.dispatcher(launch)(self.request(context_id=""))

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dispatch_performed"])
        self.assertEqual(result["context_delivery"], "vendor_workspace")
        self.assertEqual(result["context_id"], "")
        self.assertEqual(len(launch.requests), 1)
        request = launch.requests[0]
        self.assertEqual(request.runtime, "codebuddy")
        self.assertEqual(request.route, "sdk_context_read_only")
        self.assertEqual(request.cwd, str(self.root))
        self.assertEqual(request.context_id, "")
        self.assertEqual(request.routing_metadata["context_delivery"], "vendor_workspace")
        self.assertIn("workspace read-only", request.prompt.lower())
        self.assertIn("read-only repository exploration tools only", request.prompt.lower())
        self.assertNotIn("analyze only the supplied project context", request.prompt.lower())
        self.assertNotIn("do not request filesystem access", request.prompt.lower())

    def test_context_window_parameter_is_rejected_before_task_creation(self) -> None:
        launch = _FakeLaunchService()
        result = self.dispatcher(launch)(self.request(
            model="deepseek-v4-flash",
            model_parameters=ModelParameters(context_window_tokens=200000),
        ))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "MODEL_PARAMETERS_UNSUPPORTED")
        self.assertEqual(launch.requests, [])

    def test_context_drift_blocks_before_task_creation(self) -> None:
        launch = _FakeLaunchService()
        (self.root / "src" / "sample.py").write_text("VALUE = 8\n", encoding="utf-8")
        result = self.dispatcher(launch)(self.request())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "CONTEXT_DRIFT")
        self.assertEqual(launch.requests, [])

    def test_verified_context_is_rendered_into_context_only_sdk_task(self) -> None:
        launch = _FakeLaunchService()
        result = self.dispatcher(launch)(self.request())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["context_delivery"], "runtime_snapshot")
        self.assertEqual(result["context_id"], "ctx-codebuddy")
        self.assertEqual(len(launch.requests), 1)
        request = launch.requests[0]
        self.assertEqual(request.runtime, "codebuddy")
        self.assertEqual(request.route, "sdk_context_read_only")
        self.assertEqual(request.context_id, "ctx-codebuddy")
        self.assertEqual(request.routing_metadata["context_delivery"], "runtime_snapshot")
        self.assertIn("src/sample.py", request.prompt)
        self.assertIn("VALUE = 7", request.prompt)
        self.assertIn("Analyze only the supplied project context", request.prompt)



class CodeBuddyServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from agent_runtime import server

        self.server = server
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cwd = Path(self.tmp.name) / "project"
        self.cwd.mkdir()
        server.configure_runtime_database(Path(self.tmp.name) / "runtime.db")
        server.TASKS.clear()
        server.IDEMPOTENCY_TASKS.clear()

    def tearDown(self) -> None:
        self.server.TASKS.clear()
        self.server.IDEMPOTENCY_TASKS.clear()
        self.server.configure_runtime_database(None)
        self.tmp.cleanup()

    def wait(self, task_id: str) -> dict:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = self.server.subagent_status(task_id)
            if state.get("state") in {"completed", "failed", "cancelled"}:
                return state
            time.sleep(0.05)
        self.fail("CodeBuddy task did not finish")

    def test_official_codebuddy_route_reuses_durable_task_lifecycle(self) -> None:
        from agent_runtime.backends.fake import FakeBackend

        fake = FakeBackend(
            result=BackendResult(
                backend="codebuddy",
                stop_reason="success",
                answer="bounded codebuddy",
                result={"backend": "codebuddy", "stopReason": "success"},
                backend_session_id="cb-private-session",
            )
        )
        with patch("agent_runtime.server._create_codebuddy_backend", return_value=fake):
            started = self.server.subagent_start(
                prompt="analyze already bounded context only",
                runtime="codebuddy",
                route="sdk_context_read_only",
                cwd=str(self.cwd),
                timeout_seconds=10,
                idle_timeout_seconds=5,
            )
            self.assertTrue(started["ok"], started)
            self.assertEqual(self.wait(started["task_id"])["state"], "completed")

        self.assertEqual(len(fake.starts), 1)
        self.assertEqual(fake.starts[0].metadata["route"], "sdk_context_read_only")
        result = self.server.task_result(started["task_id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["runtime"], "codebuddy")
        self.assertEqual(result["answer"], "bounded codebuddy")


    def test_repository_research_uses_context_only_codebuddy_route_and_runtime_report(self) -> None:
        target = Path(self.tmp.name) / "codebuddy-research"

        def runner(argv, **kwargs):
            if argv[:2] == ["git", "clone"]:
                source = Path(argv[-1])
                source.mkdir(parents=True)
                (source / ".git").mkdir()
                (source / "README.md").write_text("external source\n", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[:3] == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(returncode=0, stdout="cafebabe\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        research_service = RepositoryResearchService(
            metadata_loader=lambda owner, repo: {"size": 1, "private": False}, runner=runner
        )
        from agent_runtime.backends.fake import FakeBackend
        fake = FakeBackend(result=BackendResult(
            backend="codebuddy", stop_reason="success", answer="CodeBuddy static findings.",
            result={"backend": "codebuddy", "stopReason": "success"},
            backend_session_id="cb-research",
        ))
        with patch("agent_runtime.server.RepositoryResearchService", return_value=research_service), patch(
            "agent_runtime.backends.codebuddy.captain_dispatch.resolve_codebuddy_cli", return_value="codebuddy"
        ), patch(
            "agent_runtime.backends.codebuddy.captain_dispatch.load_codebuddy_sdk", return_value=object()
        ), patch("agent_runtime.server._create_codebuddy_backend", return_value=fake):
            started = self.server.task_dispatch(
                objective="Study architecture", crew="codebuddy", task_kind="repository_research",
                model="deepseek-v4-flash",
                read_scope={"files": ["README.md"], "max_files": 10, "max_bytes": 2048},
                repository_research={
                    "url": "https://github.com/example/codebuddy-project",
                    "target_directory": str(target), "max_size_bytes": 1024 * 1024,
                    "report_path": "reports/codebuddy.md",
                },
                timeout_seconds=10,
            )
            self.assertTrue(started["ok"], started)
            self.assertTrue(started["context_auto_created"])
            self.assertEqual(self.wait(started["task_id"])["state"], "completed")
        result = self.server.task_result(started["task_id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["changed_files"], [])
        self.assertTrue(any(item.get("kind") == "report" and item.get("name") == "codebuddy.md" for item in result["artifacts"]))
        self.assertIn("CodeBuddy static findings.", (target / "reports" / "codebuddy.md").read_text(encoding="utf-8"))
        self.assertEqual((target / "source" / "README.md").read_text(encoding="utf-8"), "external source\n")
        self.assertEqual(fake.starts[0].metadata["route"], "sdk_context_read_only")
        self.assertIn("external source", fake.starts[0].prompt)

    def test_captain_dispatch_uses_verified_context_and_controlled_codebuddy_route(self) -> None:
        from agent_runtime.backends.fake import FakeBackend

        source = self.cwd / "sample.py"
        source.write_text("VALUE = 11\n", encoding="utf-8")
        registered = self.server.context_register(
            str(self.cwd), ["sample.py"], context_id="ctx-captain-codebuddy"
        )
        self.assertTrue(registered["ok"], registered)
        fake = FakeBackend(
            result=BackendResult(
                backend="codebuddy",
                stop_reason="success",
                answer="captain bounded result",
                result={"backend": "codebuddy", "stopReason": "success"},
                backend_session_id="cb-captain-session",
            )
        )
        with (
            patch("agent_runtime.backends.codebuddy.captain_dispatch.resolve_codebuddy_cli", return_value="codebuddy"),
            patch("agent_runtime.backends.codebuddy.captain_dispatch.load_codebuddy_sdk", return_value=object()),
            patch("agent_runtime.server._create_codebuddy_backend", return_value=fake),
        ):
            started = self.server.task_dispatch(
                objective="Explain VALUE",
                crew="codebuddy",
                task_kind="research",
                model="hy3",
                cwd=str(self.cwd),
                context_id="ctx-captain-codebuddy",
                timeout_seconds=10,
            )
            self.assertTrue(started["ok"], started)
            self.assertEqual(started["crew"], "codebuddy")
            self.assertEqual(self.wait(started["task_id"])["state"], "completed")

        self.assertEqual(len(fake.starts), 1)
        self.assertEqual(fake.starts[0].metadata["route"], "sdk_context_read_only")
        self.assertIn("VALUE = 11", fake.starts[0].prompt)
        result = self.server.task_result(started["task_id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["answer"], "captain bounded result")

    def test_artifact_snapshot_replays_and_changed_hash_conflicts(self) -> None:
        from agent_runtime.backends.fake import FakeBackend

        data = b"bounded source report\n"
        digest = hashlib.sha256(data).hexdigest()
        database = self.server._get_runtime_database()
        tasks = TaskService(database)
        created = tasks.create_task(
            task=Task(
                "wb-source", "codebuddy", "completed", "sdk_context_read_only", 1, 1,
                result_available=True,
                result_json=json.dumps({"verification": {"status": "PASSED"}}),
            ),
            session=Session("rs-source", "wb-source", "codebuddy", "sdk_context_read_only", 1, 1),
            metadata={}, idempotency_key="", request_fingerprint="source", now=1,
        )
        blob = database.path.parent / "artifacts" / "sha256" / digest[:2] / digest
        blob.parent.mkdir(parents=True)
        blob.write_bytes(data)
        artifact = Artifact(
            artifact_id="art-source", task_id="wb-source", attempt_id=str(created.attempt_id),
            origin="runtime", kind="report", name="source.md", capture_state="captured",
            declared_at=1, created_at=1, updated_at=1, storage_key=f"sha256/{digest[:2]}/{digest}",
            sha256=digest, size_bytes=len(data), captured_at=1,
            metadata_json=json.dumps({"input_kind": "technical_report"}),
        )
        with database.transaction() as connection:
            tasks.artifacts.insert_many(connection, [artifact])

        source = self.cwd / "sample.py"
        source.write_text("VALUE = 12\n", encoding="utf-8")
        registered = self.server.context_register(str(self.cwd), ["sample.py"], context_id="ctx-artifact")
        self.assertTrue(registered["ok"], registered)
        ref = {"artifact_id": "art-source", "source_task_id": "wb-source", "kind": "technical_report", "sha256": digest, "byte_size": len(data)}
        fake = FakeBackend(result=BackendResult(
            backend="codebuddy", stop_reason="success", answer="artifact result",
            result={"backend": "codebuddy", "stopReason": "success"}, backend_session_id="cb-artifact",
        ))
        kwargs = dict(
            objective="Use the bounded report", crew="codebuddy", task_kind="research", model="hy3",
            cwd=str(self.cwd), context_id="ctx-artifact", input_artifact_refs=[ref],
            idempotency_key="artifact-replay", timeout_seconds=10,
        )
        with patch("agent_runtime.backends.codebuddy.captain_dispatch.resolve_codebuddy_cli", return_value="codebuddy"), patch(
            "agent_runtime.backends.codebuddy.captain_dispatch.load_codebuddy_sdk", return_value=object()
        ), patch("agent_runtime.server._create_codebuddy_backend", return_value=fake):
            started = self.server.task_dispatch(**kwargs)
            self.assertTrue(started["ok"], started)
            self.assertEqual(self.wait(started["task_id"])["state"], "completed")
        self.assertIn("[Untrusted Input Artifacts]", fake.starts[0].prompt)
        self.assertIn("bounded source report", fake.starts[0].prompt)

        replay = self.server.task_dispatch(**kwargs)
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["dispatch_performed"])

        changed = {**ref, "sha256": "b" * 64}
        conflict = self.server.task_dispatch(**{**kwargs, "input_artifact_refs": [changed]})
        self.assertFalse(conflict["ok"], conflict)
        self.assertEqual(conflict["reason_code"], "IDEMPOTENCY_CONFLICT")

    def test_policy_reload_affects_new_key_but_replay_uses_recorded_decision(self) -> None:
        from agent_runtime.backends.fake import FakeBackend

        source = self.cwd / "policy.py"
        source.write_text("VALUE = 13\n", encoding="utf-8")
        registered = self.server.context_register(str(self.cwd), ["policy.py"], context_id="ctx-policy")
        self.assertTrue(registered["ok"], registered)
        runtime_home = Path(self.tmp.name) / "policy-home"
        runtime_home.mkdir()
        from agent_runtime.configuration.user_config import VoyagerUserConfig
        policy_path = runtime_home / "config.json"
        config = VoyagerUserConfig.defaults(runtime_home).to_dict()
        config["dispatch"] = {
            "allowed_models": ["codebuddy:hy3"],
            "preferred_models": ["codebuddy:hy3"],
            "task_kind_allowed_models": {},
        }
        policy_path.write_text(json.dumps(config), encoding="utf-8")
        fake = FakeBackend(result=BackendResult(
            backend="codebuddy", stop_reason="success", answer="policy result",
            result={"backend": "codebuddy", "stopReason": "success"}, backend_session_id="cb-policy",
        ))
        kwargs = dict(
            objective="Inspect policy sample", crew="codebuddy", task_kind="research", model="hy3",
            cwd=str(self.cwd), context_id="ctx-policy", idempotency_key="policy-replay", timeout_seconds=10,
        )
        with patch.dict(os.environ, {"TP_VOYAGER_HOME": str(runtime_home)}), patch(
            "agent_runtime.backends.codebuddy.captain_dispatch.resolve_codebuddy_cli", return_value="codebuddy"
        ), patch("agent_runtime.backends.codebuddy.captain_dispatch.load_codebuddy_sdk", return_value=object()), patch(
            "agent_runtime.server._create_codebuddy_backend", return_value=fake
        ):
            started = self.server.task_dispatch(**kwargs)
            self.assertTrue(started["ok"], started)
            self.assertEqual(self.wait(started["task_id"])["state"], "completed")
            recorded_hash = started["effective_model_policy"]["policy_sha256"]
            config["dispatch"] = {
                "allowed_models": ["codebuddy:kimi"],
                "preferred_models": ["codebuddy:kimi"],
                "task_kind_allowed_models": {},
            }
            policy_path.write_text(json.dumps(config), encoding="utf-8")
            replay = self.server.task_dispatch(**kwargs)
            self.assertTrue(replay["ok"], replay)
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["effective_model_policy"]["policy_sha256"], recorded_hash)
            rejected = self.server.task_dispatch(**{**kwargs, "idempotency_key": "policy-new"})
            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(rejected["reason_code"], "MODEL_POLICY_REJECTED")


if __name__ == "__main__":
    unittest.main()
