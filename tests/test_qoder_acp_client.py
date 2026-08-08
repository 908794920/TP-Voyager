from __future__ import annotations

import json
import queue
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.backends.qoder.acp_client import QoderAcpClient
from agent_runtime.domain.dispatch import CommandSpec


class _QueueReader:
    def __init__(self) -> None:
        self.items: queue.Queue[bytes] = queue.Queue()

    def push(self, payload: dict) -> None:
        self.items.put((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))

    def readline(self, limit: int = -1) -> bytes:
        return self.items.get(timeout=5)

    def read(self, size: int = -1) -> bytes:
        return b""


class _ProtocolWriter:
    def __init__(self, process: "FakeAcpProcess") -> None:
        self.process = process
        self.buffer = bytearray()

    def write(self, value: bytes) -> int:
        self.buffer.extend(value)
        while b"\n" in self.buffer:
            raw, _, remainder = self.buffer.partition(b"\n")
            self.buffer[:] = remainder
            if raw:
                self.process.handle(json.loads(raw.decode("utf-8")))
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class FakeAcpProcess:
    def __init__(self) -> None:
        self.stdout = _QueueReader()
        self.stderr = _QueueReader()
        self.stdin = _ProtocolWriter(self)
        self.pid = 4321
        self.returncode = None
        self.config_requests: list[tuple[str, str]] = []
        self.initialize_params: list[dict] = []
        self.commands: list[list[str]] = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode or 0

    def handle(self, message: dict) -> None:
        request_id = message.get("id")
        method = message.get("method")
        if request_id is None:
            return
        if method == "initialize":
            self.initialize_params.append(dict(message.get("params") or {}))
            result = {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True},
            }
        elif method == "session/new":
            result = {
                "sessionId": "session-protocol",
                "configOptions": [
                    {
                        "id": "active-model",
                        "category": "model",
                        "options": [
                            {"name": "Model Two", "value": "model-2"},
                        ],
                    },
                    {
                        "id": "thought-depth",
                        "category": "thought_level",
                        "options": [
                            {"name": "High", "value": "high"},
                        ],
                    },
                ],
            }
        elif method == "session/set_config_option":
            params = message["params"]
            self.config_requests.append((params["configId"], params["value"]))
            result = {"configOptions": []}
        elif method == "session/prompt":
            self.stdout.push(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-protocol",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "protocol answer"},
                        },
                    },
                }
            )
            self.stdout.push(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-protocol",
                        "update": {
                            "sessionUpdate": "usage_update",
                            "inputTokens": 12,
                            "outputTokens": 3,
                        },
                    },
                }
            )
            result = {"stopReason": "end_turn"}
        elif method == "session/load":
            result = {"configOptions": []}
        else:
            result = {}
        self.stdout.push({"jsonrpc": "2.0", "id": request_id, "result": result})


class QoderAcpProtocolTests(unittest.TestCase):
    def test_protocol_applies_dynamic_config_and_collects_usage(self) -> None:
        fake = FakeAcpProcess()
        activity: list[str] = []
        with (
            patch("agent_runtime.backends.qoder.acp_client.popen_command", return_value=fake),
            patch("agent_runtime.backends.qoder.acp_client.terminate_process_tree"),
        ):
            client = QoderAcpClient(
                cwd=str(Path.cwd()),
                cli_path="qodercli",
                on_activity=lambda item: activity.append(item.kind),
            )
            result = client.run(
                prompt="do work",
                model="model-2",
                reasoning_effort="high",
                idle_timeout_seconds=5,
                max_task_duration_seconds=10,
                on_dispatch_accepted=lambda session_id: self.assertEqual(
                    session_id, "session-protocol"
                ),
            )
            client.close()

        self.assertEqual(
            fake.config_requests,
            [("active-model", "model-2"), ("thought-depth", "high")],
        )
        self.assertEqual(result.answer, "protocol answer")
        self.assertTrue(result.model_applied)
        self.assertTrue(result.reasoning_effort_applied)
        self.assertEqual(result.usage["inputTokens"], 12)
        self.assertEqual(result.usage["outputTokens"], 3)
        self.assertGreaterEqual(activity.count("stream_activity"), 2)

    def test_read_only_policy_denies_mutating_client_capabilities(self) -> None:
        fake = FakeAcpProcess()
        with (
            patch("agent_runtime.backends.qoder.acp_client.popen_command", return_value=fake) as spawn,
            patch("agent_runtime.backends.qoder.acp_client.terminate_process_tree"),
        ):
            client = QoderAcpClient(
                cwd=str(Path.cwd()),
                cli_path="qodercli",
                on_activity=lambda item: None,
                read_only=True,
                allow_permissions=False,
            )
            result = client.run(
                prompt="inspect",
                idle_timeout_seconds=5,
                max_task_duration_seconds=10,
                on_dispatch_accepted=lambda session_id: None,
            )
            self.assertEqual(result.answer, "protocol answer")
            command = list(spawn.call_args.args[0])
            self.assertEqual(command, ["qodercli", "--acp"])
            caps = fake.initialize_params[0]["clientCapabilities"]
            self.assertTrue(caps["fs"]["readTextFile"])
            self.assertFalse(caps["fs"]["writeTextFile"])
            self.assertFalse(caps["terminal"])
            with self.assertRaises(PermissionError):
                client._dispatch_client_method(
                    "fs/write_text_file", {"path": "blocked.txt", "content": "x"}
                )
            with self.assertRaises(PermissionError):
                client._dispatch_client_method("terminal/create", {"command": "git"})
            permission = client._dispatch_client_method(
                "session/request_permission",
                {"options": [{"kind": "allow_once", "optionId": "allow"}]},
            )
            self.assertEqual(permission, {"outcome": {"outcome": "cancelled"}})
            client.close()

    def test_patch_policy_bounds_paths_commands_and_unknown_permissions(self) -> None:
        fake = FakeAcpProcess()
        spec = CommandSpec("version", ("python", "-V"))
        with (
            patch("agent_runtime.backends.qoder.acp_client.popen_command", return_value=fake) as spawn,
            patch("agent_runtime.backends.qoder.acp_client.terminate_process_tree"),
            patch("agent_runtime.backends.qoder.acp_client.subprocess.Popen") as popen,
        ):
            process = unittest.mock.MagicMock()
            process.stdout = None
            popen.return_value = process
            client = QoderAcpClient(
                cwd=str(Path.cwd()),
                cli_path="qodercli",
                on_activity=lambda item: None,
                read_only=False,
                allow_permissions=True,
                allowed_paths=("tests",),
                forbidden_paths=(".git",),
                command_specs=(spec,),
            )
            self.assertEqual(list(spawn.call_args.args[0]), ["qodercli", "--acp"])
            with self.assertRaises(PermissionError):
                client._dispatch_client_method(
                    "fs/write_text_file", {"path": "outside.txt", "content": "x"}
                )
            with self.assertRaises(PermissionError):
                client._dispatch_client_method(
                    "terminal/create", {"command": "python", "args": ["-c", "print(1)"]}
                )
            with self.assertRaises(PermissionError):
                client._dispatch_client_method(
                    "terminal/create",
                    {
                        "command": "python",
                        "args": ["-V"],
                        "env": [{"name": "PYTHONPATH", "value": "outside"}],
                    },
                )
            result = client._dispatch_client_method(
                "terminal/create", {"command": "python", "args": ["-V"]}
            )
            self.assertIn("terminalId", result)
            popen.assert_called_once()
            args, kwargs = popen.call_args
            self.assertEqual(args[0], ["python", "-V"])
            self.assertEqual(kwargs["cwd"], str(Path.cwd().resolve()))

            selected = client._dispatch_client_method(
                "session/request_permission",
                {
                    "options": [
                        {"kind": "mystery_allow", "optionId": "unknown"},
                        {"kind": "allow_always", "optionId": "always"},
                        {"kind": "allow_once", "optionId": "once"},
                    ]
                },
            )
            self.assertEqual(selected, {"outcome": {"outcome": "selected", "optionId": "once"}})
            client.close()



if __name__ == "__main__":
    unittest.main()
