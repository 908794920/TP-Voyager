from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from agent_runtime.backends.base import BackendActivity
from agent_runtime.backends.codebuddy.sdk_client import CodeBuddySdkClient
from agent_runtime.backends.qoder.acp_client import QoderAcpClient
from agent_runtime.runtime.backend_callbacks import RuntimeBackendCallbacks


class RuntimeBackendCallbacksTests(unittest.TestCase):
    def test_typed_activity_detail_reaches_runtime_sink(self) -> None:
        received: list[BackendActivity] = []
        callbacks = RuntimeBackendCallbacks(
            on_dispatch_accepted=lambda _session: None,
            on_activity=received.append,
        )
        activity = BackendActivity(
            kind="stream_activity",
            timestamp=1.0,
            detail={"observation_kind": "assistant_message", "text": "hello"},
        )

        callbacks.on_activity(activity)

        self.assertEqual(received, [activity])
        self.assertEqual(received[0].detail["text"], "hello")


class QoderObservationTests(unittest.TestCase):
    def test_agent_message_chunk_is_forwarded_as_observable_assistant_text(self) -> None:
        events: list[BackendActivity] = []
        client = object.__new__(QoderAcpClient)
        client._answer = []
        client._usage = {}
        client._usage_events = []
        client._last_activity = time.monotonic()
        client._event_count = 0
        client.on_activity = events.append

        client._handle_notification(
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "Inspecting src/login.py"},
                    }
                },
            }
        )

        self.assertEqual(client._answer, ["Inspecting src/login.py"])
        self.assertEqual(events[-1].detail["observation_kind"], "assistant_message")
        self.assertEqual(events[-1].detail["text"], "Inspecting src/login.py")

    def test_known_tool_update_forwards_only_safe_tool_metadata(self) -> None:
        events: list[BackendActivity] = []
        client = object.__new__(QoderAcpClient)
        client._answer = []
        client._usage = {}
        client._usage_events = []
        client._last_activity = time.monotonic()
        client._event_count = 0
        client.on_activity = events.append

        client._handle_notification(
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCall": {"name": "Read", "status": "running"},
                        "content": "raw file contents must not be forwarded",
                    }
                },
            }
        )

        detail = events[-1].detail
        self.assertEqual(detail["observation_kind"], "tool_activity")
        self.assertEqual(detail["tool"], "Read")
        self.assertEqual(detail["status"], "running")
        self.assertNotIn("content", detail)

    def test_standard_acp_tool_call_projects_safe_action_and_workspace_path(self) -> None:
        events: list[BackendActivity] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "src" / "login.py"
            target.parent.mkdir(parents=True)
            target.write_text("print('ok')", encoding="utf-8")
            client = object.__new__(QoderAcpClient)
            client.cwd = root.resolve()
            client._answer = []
            client._usage = {}
            client._usage_events = []
            client._last_activity = time.monotonic()
            client._event_count = 0
            client.on_activity = events.append

            client._handle_notification(
                {
                    "method": "session/update",
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "tool-1",
                            "title": "Read login.py",
                            "kind": "read",
                            "status": "in_progress",
                            "rawInput": {"path": str(target)},
                            "content": "raw file contents must not be forwarded",
                        }
                    },
                }
            )

        detail = events[-1].detail
        self.assertEqual(detail["observation_kind"], "tool_activity")
        self.assertEqual(detail["tool"], "Read")
        self.assertEqual(detail["action"], "read")
        self.assertEqual(detail["path"], "src/login.py")
        self.assertEqual(detail["status"], "in_progress")
        self.assertNotIn("rawInput", detail)
        self.assertNotIn("content", detail)

    def test_qoder_tool_like_vendor_update_is_forward_compatible(self) -> None:
        events: list[BackendActivity] = []
        client = object.__new__(QoderAcpClient)
        client.cwd = Path.cwd().resolve()
        client._answer = []
        client._usage = {}
        client._usage_events = []
        client._last_activity = time.monotonic()
        client._event_count = 0
        client.on_activity = events.append

        client._handle_notification(
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "qoder_tool_progress",
                        "toolCallId": "tool-vendor-1",
                        "title": "Grep source",
                        "kind": "search",
                        "status": "in_progress",
                    }
                },
            }
        )

        detail = events[-1].detail
        self.assertEqual(detail["observation_kind"], "tool_activity")
        self.assertEqual(detail["action"], "search")
        self.assertEqual(detail["tool"], "Search")

    def test_unknown_tool_title_does_not_expose_host_path(self) -> None:
        events: list[BackendActivity] = []
        client = object.__new__(QoderAcpClient)
        client.cwd = Path.cwd().resolve()
        client._answer = []
        client._usage = {}
        client._usage_events = []
        client._last_activity = time.monotonic()
        client._event_count = 0
        client.on_activity = events.append

        client._handle_notification(
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "tool-private-title",
                        "title": "C:/Users/example/private.txt",
                        "kind": "other",
                        "status": "in_progress",
                    }
                },
            }
        )

        encoded = str(events[-1].detail)
        self.assertEqual(events[-1].detail["tool"], "tool")
        self.assertNotIn("private.txt", encoded)

    def test_client_fs_callback_is_observable_when_qoder_omits_tool_update(self) -> None:
        events: list[BackendActivity] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "src" / "service.py"
            target.parent.mkdir(parents=True)
            target.write_text("service = True\n", encoding="utf-8")
            client = object.__new__(QoderAcpClient)
            client.cwd = root.resolve()
            client.allowed_paths = ()
            client.forbidden_paths = ()
            client.allow_file_writes = False
            client.allow_permissions = False
            client.allow_terminal = False
            client.command_specs = ()
            client._file_access_events = []
            client.on_activity = events.append

            result = client._dispatch_client_method(
                "fs/read_text_file",
                {"path": str(target)},
            )

        self.assertEqual(result, {"content": "service = True"})
        tool_events = [item for item in events if item.detail.get("observation_kind") == "tool_activity"]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0].detail["tool"], "Read")
        self.assertEqual(tool_events[0].detail["action"], "read")
        self.assertEqual(tool_events[0].detail["path"], "src/service.py")
        self.assertEqual(tool_events[0].detail["status"], "completed")


class FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakePermissionResultDeny:
    def __init__(self, *, message: str, interrupt: bool = False):
        self.message = message
        self.interrupt = interrupt


class FakePermissionResultAllow:
    def __init__(self, *, updated_input):
        self.updated_input = updated_input


class TextBlock:
    def __init__(self, text: str):
        self.text = text


class AssistantMessage:
    def __init__(self, text: str):
        self.content = [TextBlock(text)]


class ResultMessage:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.is_error = False
        self.result = "final answer"
        self.usage = {"input_tokens": 7, "output_tokens": 3}
        self.total_cost_usd = 0.01
        self.duration_ms = 12
        self.num_turns = 1


class FakeSdkClient:
    def __init__(self, *, options):
        self.options = options
        self.session_id = ""

    async def connect(self):
        return None

    async def query(self, prompt: str, session_id: str):
        self.session_id = session_id

    def receive_response(self):
        async def generator():
            yield AssistantMessage("Checking the service layer.")
            yield ResultMessage(self.session_id)
        return generator()

    async def disconnect(self):
        return None

    async def interrupt(self):
        return None


class FakeSdkModule:
    CodeBuddyAgentOptions = FakeOptions
    CodeBuddySDKClient = FakeSdkClient
    PermissionResultDeny = FakePermissionResultDeny
    PermissionResultAllow = FakePermissionResultAllow


class CodeBuddyObservationTests(unittest.TestCase):
    def test_assistant_text_block_is_forwarded_as_observable_assistant_text(self) -> None:
        activities: list[BackendActivity] = []
        with tempfile.TemporaryDirectory() as tmp:
            client = CodeBuddySdkClient(
                cwd=tmp,
                on_activity=activities.append,
                sdk_module=FakeSdkModule,
                access_mode="read_only",
            )
            result = client.run(
                prompt="inspect",
                model="GLM-5.3",
                idle_timeout_seconds=5,
                max_task_duration_seconds=30,
                on_dispatch_accepted=lambda _session: None,
            )

        messages = [
            item for item in activities
            if item.detail.get("observation_kind") == "assistant_message"
        ]
        self.assertEqual(result.answer, "final answer")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].detail["text"], "Checking the service layer.")


if __name__ == "__main__":
    unittest.main()
