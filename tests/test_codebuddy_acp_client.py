from __future__ import annotations

import importlib
import json
import queue
import unittest
from pathlib import Path
from unittest.mock import patch


REAL_USAGE = {
    "prompt_tokens": 28600,
    "completion_tokens": 2,
    "total_tokens": 28602,
    "prompt_cache_hit_tokens": 28544,
    "prompt_cache_miss_tokens": 56,
    "credit": 0.09,
}


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
    def __init__(self, process: "FakeCodeBuddyAcpProcess") -> None:
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


class FakeCodeBuddyAcpProcess:
    def __init__(self, *, include_ids: bool = True, second_credit: float | None = None) -> None:
        self.stdout = _QueueReader()
        self.stderr = _QueueReader()
        self.stdin = _ProtocolWriter(self)
        self.pid = 9876
        self.returncode = None
        self.include_ids = include_ids
        self.second_credit = second_credit
        self.prompt_requests = 0
        self.config_requests: list[tuple[str, str]] = []

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
            result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
        elif method == "session/new":
            result = {
                "sessionId": "cb-acp-session",
                "configOptions": [
                    {
                        "id": "active-model",
                        "category": "model",
                        "options": [{"name": "DeepSeek V4 Flash", "value": "deepseek-v4-flash"}],
                    },
                    {
                        "id": "thought-depth",
                        "category": "thought_level",
                        "options": [{"name": "High", "value": "high"}],
                    },
                ],
            }
        elif method == "session/set_config_option":
            params = message.get("params") or {}
            self.config_requests.append((str(params.get("configId")), str(params.get("value"))))
            result = {"configOptions": []}
        elif method == "session/load":
            result = {"configOptions": []}
        elif method == "session/prompt":
            self.prompt_requests += 1
            self.stdout.push({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "cb-acp-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "codebuddy acp answer"},
                    },
                },
            })
            for index in range(2):
                usage = dict(REAL_USAGE)
                if index == 1 and self.second_credit is not None:
                    usage["credit"] = self.second_credit
                meta = {"usage": usage}
                if self.include_ids:
                    meta["codebuddy.ai/requestId"] = "req-real-123"
                    meta["codebuddy.ai/messageRequestId"] = "msg-real-456"
                self.stdout.push({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "cb-acp-session",
                        "update": {
                            "sessionUpdate": "usage_update",
                            "_meta": meta,
                        },
                    },
                })
            result = {"stopReason": "end_turn"}
        else:
            result = {}
        self.stdout.push({"jsonrpc": "2.0", "id": request_id, "result": result})


class CodeBuddyAcpProtocolTests(unittest.TestCase):
    def _client_class(self):
        try:
            module = importlib.import_module("agent_runtime.backends.codebuddy.acp_client")
        except ModuleNotFoundError as exc:
            self.fail(f"CodeBuddy native ACP client is missing: {exc}")
        client = getattr(module, "CodeBuddyAcpClient", None)
        self.assertIsNotNone(client, "CodeBuddyAcpClient is missing")
        return module, client

    def test_codebuddy_acp_usage_meta_fixture(self) -> None:
        module, client_class = self._client_class()
        fake = FakeCodeBuddyAcpProcess(include_ids=True)
        with (
            patch.object(module, "popen_command", return_value=fake) as spawn,
            patch.object(module, "terminate_process_tree"),
        ):
            client = client_class(
                cwd=str(Path.cwd()), cli_path="codebuddy", on_activity=lambda item: None,
                access_mode="read_only",
            )
            result = client.run(
                prompt="analyze", model="deepseek-v4-flash", reasoning_effort="high",
                idle_timeout_seconds=5, max_task_duration_seconds=10,
                on_dispatch_accepted=lambda value: self.assertEqual(value, "cb-acp-session"),
            )
            client.close()

        command = list(spawn.call_args.args[0])
        self.assertEqual(command[:2], ["codebuddy", "--acp"])
        self.assertIn("--strict-mcp-config", command)
        self.assertEqual(fake.prompt_requests, 1)
        self.assertEqual(result.answer, "codebuddy acp answer")
        self.assertEqual(len(result.usage_samples), 1)
        sample = result.usage_samples[0]
        self.assertEqual(sample["sample_id"], "req-real-123")
        self.assertEqual(sample["accounting"], "delta")
        self.assertEqual(sample["usage"]["prompt_tokens"], 28600)
        self.assertEqual(sample["usage"]["completion_tokens"], 2)
        self.assertEqual(sample["usage"]["total_tokens"], 28602)
        self.assertEqual(sample["usage"]["prompt_cache_hit_tokens"], 28544)
        self.assertEqual(sample["usage"]["prompt_cache_miss_tokens"], 56)
        self.assertEqual(sample["usage"]["credit"], 0.09)

    def test_codebuddy_acp_credit_zero_valid(self) -> None:
        module, client_class = self._client_class()
        fake = FakeCodeBuddyAcpProcess(include_ids=False, second_credit=0.0)
        with (
            patch.object(module, "popen_command", return_value=fake),
            patch.object(module, "terminate_process_tree"),
        ):
            client = client_class(
                cwd=str(Path.cwd()), cli_path="codebuddy", on_activity=lambda item: None,
                access_mode="read_only",
            )
            result = client.run(
                prompt="analyze", model="deepseek-v4-flash", reasoning_effort="",
                idle_timeout_seconds=5, max_task_duration_seconds=10,
                on_dispatch_accepted=lambda value: None,
            )
            client.close()

        self.assertEqual(len(result.usage_samples), 1)
        self.assertEqual(result.usage_samples[0]["usage"]["credit"], 0.0)
        self.assertEqual(result.usage["credit"], 0.0)

    def test_codebuddy_acp_request_id_dedup(self) -> None:
        module, client_class = self._client_class()
        fake = FakeCodeBuddyAcpProcess(include_ids=True)
        with (
            patch.object(module, "popen_command", return_value=fake),
            patch.object(module, "terminate_process_tree"),
        ):
            client = client_class(
                cwd=str(Path.cwd()), cli_path="codebuddy", on_activity=lambda item: None,
                access_mode="read_only",
            )
            result = client.run(
                prompt="analyze", model="deepseek-v4-flash", reasoning_effort="",
                idle_timeout_seconds=5, max_task_duration_seconds=10,
                on_dispatch_accepted=lambda value: None,
            )
            client.close()

        self.assertEqual(fake.prompt_requests, 1)
        self.assertEqual(len(result.usage_samples), 1)
        self.assertEqual(result.usage_samples[0]["sample_id"], "req-real-123")
        self.assertEqual(result.usage_samples[0]["accounting"], "delta")
        self.assertEqual(result.usage_samples[0]["usage"]["credit"], 0.09)

    def test_codebuddy_acp_prompt_tokens_not_double_count_cache(self) -> None:
        module, client_class = self._client_class()
        fake = FakeCodeBuddyAcpProcess(include_ids=True)
        with (
            patch.object(module, "popen_command", return_value=fake),
            patch.object(module, "terminate_process_tree"),
        ):
            client = client_class(
                cwd=str(Path.cwd()), cli_path="codebuddy", on_activity=lambda item: None,
                access_mode="read_only",
            )
            result = client.run(
                prompt="analyze", model="deepseek-v4-flash", reasoning_effort="",
                idle_timeout_seconds=5, max_task_duration_seconds=10,
                on_dispatch_accepted=lambda value: None,
            )
            client.close()

        usage = result.usage_samples[0]["usage"]
        self.assertEqual(usage["prompt_tokens"], 28600)
        self.assertEqual(usage["prompt_cache_hit_tokens"], 28544)
        self.assertEqual(usage["prompt_cache_miss_tokens"], 56)
        self.assertEqual(usage["total_tokens"], 28602)
        self.assertNotEqual(usage["prompt_tokens"] + usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"], usage["total_tokens"])

    def test_anonymous_usage_updates_keep_latest_snapshot_without_addition(self) -> None:
        module, client_class = self._client_class()
        fake = FakeCodeBuddyAcpProcess(include_ids=False, second_credit=0.11)
        with (
            patch.object(module, "popen_command", return_value=fake),
            patch.object(module, "terminate_process_tree"),
        ):
            client = client_class(
                cwd=str(Path.cwd()), cli_path="codebuddy", on_activity=lambda item: None,
                access_mode="read_only",
            )
            result = client.run(
                prompt="analyze", model="deepseek-v4-flash", reasoning_effort="",
                idle_timeout_seconds=5, max_task_duration_seconds=10,
                on_dispatch_accepted=lambda value: None,
            )
            client.close()

        self.assertEqual(len(result.usage_samples), 1)
        sample = result.usage_samples[0]
        self.assertEqual(sample["sample_id"], "")
        self.assertEqual(sample["accounting"], "snapshot")
        self.assertEqual(sample["usage"]["credit"], 0.11)
        self.assertEqual(result.usage["credit"], 0.11)


if __name__ == "__main__":
    unittest.main()
