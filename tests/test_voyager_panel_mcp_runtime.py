from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import queue
import threading
from collections import deque
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[1]
_MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None

_WRITER = r'''
import sys
from agent_runtime.application.task_service import TaskService
from agent_runtime.domain.enums import EventType, TaskRoute
from agent_runtime.domain.session import Session
from agent_runtime.domain.structured_result import RESULT_SCHEMA, StructuredResult
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.runtime_paths import runtime_database_path

action = sys.argv[1]
service = TaskService(Database(runtime_database_path()))
service.db.initialize()

def create(task_id):
    if service.get_task(task_id) is not None:
        return
    service.create_task(
        task=Task(task_id=task_id, task_type="qoder", status="queued", route=TaskRoute.ACP.value, created_at=1.0, updated_at=1.0),
        session=Session(session_id=f"rs-{task_id}", task_id=task_id, backend="qoder", route=TaskRoute.ACP.value, created_at=1.0, updated_at=1.0),
        metadata={"routing_metadata": {"presentation_group_id": "pg-mcp-runtime"}},
        idempotency_key="", request_fingerprint=f"fp-{task_id}", now=1.0,
    )
    task = service.get_task(task_id)
    service.update_status(task_id, status="running", event_type=EventType.TASK_STARTED.value, version=task.version, started_at=2.0)
    service.append_activity(task_id, "tool_activity", details={"tool":"Read","action":"read","path":f"src/{task_id}.py","status":"completed"})
    service.append_activity(task_id, "status", details={"phase":"analysis","status":"running","summary":"Inspecting runtime"})
    service.append_activity(task_id, "file_change", details={"action":"modify","path":f"src/{task_id}.py","status":"completed"})

def complete(task_id):
    task = service.get_task(task_id)
    if task.status == "completed":
        return
    service.save_result(
        task_id,
        structured_result=StructuredResult(schema=RESULT_SCHEMA, attempt_id=task.current_attempt_id or "", answer=f"done {task_id}", backend="qoder", stop_reason="end_turn"),
        status="completed", version=task.version, terminal_reason="end_turn",
    )
    service.append_activity(task_id, "final_response", details={"status":"completed"})
    service.append_activity(task_id, "agent_completed", details={"status":"completed"})

if action == "seed":
    create("mcp-task-a")
    create("mcp-task-b")
elif action == "complete":
    complete("mcp-task-a")
    complete("mcp-task-b")
else:
    raise SystemExit(action)
'''


class _StdioMcpProcess:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._stdout: queue.Queue[str | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=200)

    def __enter__(self) -> "_StdioMcpProcess":
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "agent_runtime.server"],
            cwd=str(_REPO_ROOT),
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self.proc.stdout is not None and self.proc.stderr is not None

        def read_stdout() -> None:
            assert self.proc is not None and self.proc.stdout is not None
            try:
                for line in self.proc.stdout:
                    self._stdout.put(line)
            finally:
                self._stdout.put(None)

        def read_stderr() -> None:
            assert self.proc is not None and self.proc.stderr is not None
            for line in self.proc.stderr:
                self._stderr.append(line)

        threading.Thread(target=read_stdout, name="tp-voyager-mcp-test-stdout", daemon=True).start()
        threading.Thread(target=read_stderr, name="tp-voyager-mcp-test-stderr", daemon=True).start()

        initialized = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "tp-voyager-runtime-test", "version": "1"},
            },
            timeout=15.0,
        )
        if "result" not in initialized:
            raise AssertionError(f"MCP initialize failed: {initialized}")
        self.notify("notifications/initialized", {})
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def _write(self, payload: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        assert self.proc is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None and self._stdout.empty():
                stderr = "".join(self._stderr)
                raise AssertionError(f"MCP server exited early ({self.proc.returncode}): {stderr[-4000:]}")
            remaining = max(0.0, deadline - time.monotonic())
            try:
                line = self._stdout.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if line is None:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                return message
        stderr = "".join(self._stderr)
        raise AssertionError(f"MCP request timed out: {method}; stderr={stderr[-2000:]}")

    def call_panel(self, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self.request(
            "tools/call",
            {"name": "render_voyager_panel", "arguments": arguments},
            timeout=15.0,
        )
        if "error" in response:
            raise AssertionError(response["error"])
        result = response.get("result") or {}
        structured = result.get("structuredContent") or result.get("structured_content")
        if isinstance(structured, dict):
            return structured
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    parsed = json.loads(item.get("text") or "")
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        raise AssertionError(f"MCP call did not return structured panel data: {result}")


def _kinds(detail: dict[str, Any]) -> list[str]:
    return [str(item.get("kind") or "") for item in detail.get("timeline") or []]


@unittest.skipUnless(_MCP_AVAILABLE, "official mcp package is not installed")
class VoyagerPanelMcpRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "voyager-home"
        self.env = dict(os.environ)
        self.env["TP_VOYAGER_HOME"] = str(self.home)
        self.env["TP_VOYAGER_MCP_SURFACE"] = "diagnostic"
        existing = self.env.get("PYTHONPATH", "")
        self.env["PYTHONPATH"] = str(_REPO_ROOT) + (os.pathsep + existing if existing else "")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _writer(self, action: str) -> None:
        subprocess.run(
            [sys.executable, "-c", _WRITER, action],
            cwd=str(_REPO_ROOT), env=self.env, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def test_completed_and_restarted_mcp_render_preserves_durable_activity(self) -> None:
        # Verify the same interpreter/environment used to launch the real MCP
        # server resolves the patched source tree and canonical SQLite path.
        diagnostic = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import agent_runtime.api.mcp_server as s; "
                "from agent_runtime.persistence.runtime_paths import runtime_database_path; "
                "print(Path(s.__file__).resolve()); print(runtime_database_path().resolve())",
            ],
            cwd=str(_REPO_ROOT), env=self.env, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip().splitlines()
        self.assertEqual(Path(diagnostic[-2]), (_REPO_ROOT / "agent_runtime/api/mcp_server.py").resolve())
        self.assertEqual(Path(diagnostic[-1]), (self.home / "runtime/tp_voyager.db").resolve())

        # Start the MCP process first. Production tasks are dispatched after
        # server startup; seeding a synthetic running task before startup would
        # correctly trigger startup reconciliation and change its state.
        with _StdioMcpProcess(self.env) as mcp:
            self._writer("seed")
            running = mcp.call_panel({"task_id": "mcp-task-a", "limit": 200})
            self.assertIn("tool_activity", _kinds(running))
            self.assertIn("status", _kinds(running))
            self.assertIn("file_change", _kinds(running))

            # Completion is committed by another process using the same SQLite.
            self._writer("complete")
            completed = mcp.call_panel({"task_id": "mcp-task-a", "limit": 200})
            kinds = _kinds(completed)
            self.assertIn("tool_activity", kinds)
            self.assertIn("status", kinds)
            self.assertIn("file_change", kinds)
            self.assertEqual(kinds[-2:], ["final_response", "agent_completed"])

            presentation = mcp.call_panel({"presentation_group_id": "pg-mcp-runtime", "limit": 200})
            explicit = mcp.call_panel({"task_ids": ["mcp-task-a", "mcp-task-b"], "limit": 200})
            for grouped in (presentation, explicit):
                self.assertEqual(grouped.get("task_ids"), ["mcp-task-a", "mcp-task-b"])
                for child in grouped.get("tasks") or []:
                    child_kinds = _kinds(child)
                    self.assertIn("tool_activity", child_kinds)
                    self.assertEqual(child_kinds[-2:], ["final_response", "agent_completed"])

        # A completely new MCP server process has an empty live observation
        # store; terminal activity must still reconstruct from SQLite alone.
        with _StdioMcpProcess(self.env) as restarted:
            after_restart = restarted.call_panel({"task_id": "mcp-task-a", "limit": 200})
            kinds = _kinds(after_restart)
            self.assertIn("tool_activity", kinds)
            self.assertEqual(kinds[-2:], ["final_response", "agent_completed"])


if __name__ == "__main__":
    unittest.main()
