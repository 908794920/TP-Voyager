from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_runtime.application.voyage.observability import (
    AgentObservationRecorder,
    AgentObservationStore,
    VoyageAgentProjection,
    observation_event_from_backend_activity,
)
from agent_runtime.domain.crew_outcome import (
    CREW_OUTCOME_MARKER,
    CREW_OUTCOME_SCHEMA,
    strip_crew_outcome_marker,
)


class FakeTaskService:
    def __init__(self, tasks, sessions=None, usage=None, artifacts=None, activity=None):
        self._tasks = {task.task_id: task for task in tasks}
        self._sessions = sessions or {}
        self._usage = usage or {}
        self._artifacts = artifacts or {}
        self._activity = activity or {}

    def get_task(self, task_id):
        return self._tasks.get(task_id)

    def list_tasks(self):
        return list(self._tasks.values())

    def get_session(self, task_id):
        return self._sessions.get(task_id)

    def latest_usage_evidence(self, task_id, attempt_id=None):
        return self._usage.get(task_id, {})

    def list_artifacts(self, task_id, attempt_id=None):
        return self._artifacts.get(task_id, [])

    def activity_from_events(self, task_id):
        return list(self._activity.get(task_id, []))


def task(task_id: str, status: str, *, crew: str = "qoder", updated_at: float = 10.0):
    return SimpleNamespace(
        task_id=task_id,
        task_type=crew,
        status=status,
        route=f"{crew}_read_only",
        created_at=1.0,
        updated_at=updated_at,
        started_at=2.0,
        finished_at=9.0 if status in {"completed", "failed"} else None,
        error_code="BackendError" if status == "failed" else None,
        error_message="provider failed" if status == "failed" else None,
        terminal_reason="backend_error" if status == "failed" else None,
        current_attempt_id="at-1",
        result_available=status == "completed",
        result_json=None,
    )


class AgentObservationStoreTests(unittest.TestCase):
    def test_store_initialization_does_not_require_writable_root(self) -> None:
        store = AgentObservationStore(Path("/dev/null/tp-voyager-observations"))
        self.assertEqual(store.root, Path("/dev/null/tp-voyager-observations"))

    def test_append_keeps_observations_in_runtime_memory_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            store.append("task-1", {"kind": "assistant_message", "text": "transient"})
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(store.read("task-1")[-1]["text"], "transient")

    def test_append_is_bounded_and_drops_non_observation_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            saved = store.append(
                "task-1",
                {
                    "kind": "assistant_message",
                    "timestamp": 12.5,
                    "text": "hello from agent",
                    "prompt": "must not persist",
                    "system_message": "must not persist",
                    "raw_tool_output": "must not persist",
                    "model": "DeepSeek-V4-Flash-0731",
                    "crew": "qoder",
                },
            )

            self.assertEqual(saved["kind"], "assistant_message")
            self.assertEqual(saved["text"], "hello from agent")
            self.assertEqual(saved["model"], "DeepSeek-V4-Flash-0731")
            self.assertNotIn("prompt", saved)
            self.assertNotIn("system_message", saved)
            self.assertNotIn("raw_tool_output", saved)

            encoded = json.dumps(store.read("task-1"), ensure_ascii=False)
            self.assertNotIn("must not persist", encoded)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(store.read("task-1"), [saved])

    def test_tool_path_is_relative_and_traversal_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            safe = store.append(
                "task-1",
                {
                    "kind": "tool_activity",
                    "tool": "Read",
                    "action": "read",
                    "path": "src/service.py",
                    "status": "completed",
                },
            )
            unsafe = store.append(
                "task-1",
                {
                    "kind": "file_change",
                    "path": "../../secret.txt",
                    "action": "modify",
                },
            )

            self.assertEqual(safe["path"], "src/service.py")
            self.assertNotIn("path", unsafe)

    def test_assistant_text_is_clipped_per_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp), max_text_chars=32)
            saved = store.append(
                "task-1", {"kind": "assistant_message", "text": "x" * 200}
            )
            self.assertEqual(len(saved["text"]), 32)

    def test_assistant_text_preserves_stream_whitespace_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            first = store.append(
                "task-1",
                {"kind": "assistant_message", "text": "# 标题\n\n第一段 "},
            )
            second = store.append(
                "task-1",
                {"kind": "assistant_message", "text": " **加粗**\n- 项目\n"},
            )

            self.assertEqual(first["text"], "# 标题\n\n第一段 ")
            self.assertEqual(second["text"], " **加粗**\n- 项目\n")

    def test_conversation_stream_is_independent_from_high_frequency_activity_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp), max_events_per_task=64)
            store.append(
                "task-1",
                {"kind": "assistant_message", "text": "完整开头\n\n"},
            )
            store.append(
                "task-1",
                {"kind": "assistant_message", "text": "  保留缩进和结尾\n"},
            )
            for index in range(300):
                store.append(
                    "task-1",
                    {"kind": "tool_activity", "tool": "Read", "status": "completed", "timestamp": index + 10},
                )

            conversation = store.read_conversation("task-1", limit=20)

            self.assertEqual(
                [item["text"] for item in conversation],
                ["完整开头\n\n", "  保留缩进和结尾\n"],
            )


class AgentObservationRecorderTests(unittest.TestCase):
    def test_recorder_projects_lifecycle_activity_usage_and_terminal_answer(self) -> None:
        from agent_runtime.backends.base import BackendActivity, BackendUsage

        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            recorder = AgentObservationRecorder(store)
            state = SimpleNamespace(
                task_id="task-1", runtime="qoder", model="DeepSeek-V4-Flash-0731"
            )
            recorder.started(state, timestamp=1.0)
            recorder.activity(
                state,
                BackendActivity(
                    kind="stream_activity",
                    timestamp=2.0,
                    detail={"observation_kind": "tool_activity", "tool": "Read", "path": "src/a.py"},
                ),
            )
            recorder.usage(
                state,
                BackendUsage(
                    provider="qoder", model="DeepSeek-V4-Flash-0731", input_tokens=10, output_tokens=4
                ),
                timestamp=3.0,
            )
            recorder.completed(state, answer="Final answer", timestamp=4.0)

            events = store.read("task-1", limit=20)
            self.assertEqual([item["kind"] for item in events], [
                "agent_started", "tool_activity", "usage", "assistant_message", "agent_completed"
            ])
            self.assertEqual(events[0]["model"], "DeepSeek-V4-Flash-0731")
            self.assertEqual(events[2]["usage"]["input_tokens"], 10)
            self.assertEqual(events[3]["text"], "Final answer")

    def test_completed_appends_canonical_answer_even_when_stream_text_exists(self) -> None:
        from agent_runtime.backends.base import BackendActivity

        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            recorder = AgentObservationRecorder(store)
            state = SimpleNamespace(task_id="task-1", runtime="codebuddy", model="GLM-5.3")
            recorder.activity(
                state,
                BackendActivity(
                    kind="stream_activity",
                    timestamp=1.0,
                    detail={"observation_kind": "assistant_message", "text": "streamed"},
                ),
            )
            recorder.completed(state, answer="final", timestamp=2.0)
            events = store.read("task-1", limit=20)
            messages = [item for item in events if item["kind"] == "assistant_message"]
            self.assertEqual([item["text"] for item in messages], ["streamed", "final"])
            self.assertEqual(messages[-1]["source"], "canonical_final")

    def test_failed_records_safe_reason_without_exception_repr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            recorder = AgentObservationRecorder(store)
            state = SimpleNamespace(task_id="task-1", runtime="qoder", model="m")
            recorder.failed(state, reason="BackendTimeoutError", timestamp=2.0)
            event = store.read("task-1")[-1]
            self.assertEqual(event["kind"], "agent_failed")
            self.assertEqual(event["reason"], "BackendTimeoutError")

    def test_failed_records_safe_execution_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            recorder = AgentObservationRecorder(store)
            state = SimpleNamespace(task_id="task-1", runtime="codebuddy", model="hy3")

            recorder.failed(
                state,
                reason="WorkspaceSnapshotError",
                phase="workspace_snapshot",
                timestamp=2.0,
            )

            event = store.read("task-1")[-1]
            self.assertEqual(event["reason"], "WorkspaceSnapshotError")
            self.assertEqual(event["phase"], "workspace_snapshot")

    def test_activity_returns_normalized_event_for_durable_persistence(self) -> None:
        from agent_runtime.backends.base import BackendActivity

        with tempfile.TemporaryDirectory() as tmp:
            recorder = AgentObservationRecorder(AgentObservationStore(Path(tmp)))
            observed = recorder.activity(
                task("task-1", "running"),
                BackendActivity(
                    kind="stream_activity",
                    timestamp=4.0,
                    detail={"observation_kind": "tool_activity", "tool": "Read"},
                ),
            )

        self.assertIsNotNone(observed)
        self.assertEqual(observed["kind"], "tool_activity")
        self.assertEqual(observed["tool"], "Read")


class BackendActivityProjectionTests(unittest.TestCase):
    def test_backend_activity_projection_accepts_only_explicit_observation_fields(self) -> None:
        from agent_runtime.backends.base import BackendActivity

        event = observation_event_from_backend_activity(
            BackendActivity(
                kind="stream_activity",
                timestamp=7.5,
                detail={
                    "observation_kind": "assistant_message",
                    "text": "visible answer",
                    "route": "acp",
                    "raw_tool_output": "secret",
                },
            )
        )
        self.assertEqual(event["kind"], "assistant_message")
        self.assertEqual(event["timestamp"], 7.5)
        self.assertEqual(event["text"], "visible answer")
        self.assertNotIn("route", event)
        self.assertNotIn("raw_tool_output", event)

    def test_backend_activity_without_observation_marker_is_ignored(self) -> None:
        from agent_runtime.backends.base import BackendActivity

        self.assertIsNone(
            observation_event_from_backend_activity(
                BackendActivity(kind="stream_activity", detail={"route": "sdk"})
            )
        )


class VoyageAgentProjectionTests(unittest.TestCase):
    def test_task_identity_prefers_session_runtime_over_task_kind_before_live_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            current = task("task-1", "queued", crew="code_review")
            service = FakeTaskService(
                [current],
                sessions={
                    "task-1": SimpleNamespace(
                        metadata_json=json.dumps({"runtime": "qoder", "model": "GLM-5.3"})
                    )
                },
            )
            projection = VoyageAgentProjection(service, store)

            detail = projection.detail("task-1")

            self.assertEqual(detail["task"]["crew"], "qoder")
            self.assertEqual(detail["task"]["model"], "GLM-5.3")

    def test_detail_combines_durable_truth_observations_usage_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            store.append(
                "task-1",
                {
                    "kind": "agent_started",
                    "timestamp": 2.0,
                    "crew": "qoder",
                    "model": "DeepSeek-V4-Flash-0731",
                    "status": "running",
                },
            )
            store.append(
                "task-1",
                {"kind": "assistant_message", "timestamp": 3.0, "text": "Inspecting login flow."},
            )
            store.append(
                "task-1",
                {"kind": "tool_activity", "timestamp": 4.0, "tool": "Read", "path": "src/login.py"},
            )
            store.append(
                "task-1",
                {"kind": "file_change", "timestamp": 5.0, "path": "src/login.py", "action": "modify"},
            )

            service = FakeTaskService(
                [task("task-1", "running")],
                sessions={
                    "task-1": SimpleNamespace(
                        metadata_json=json.dumps(
                            {"model": "DeepSeek-V4-Flash-0731", "runtime": "qoder"}
                        )
                    )
                },
                usage={
                    "task-1": {
                        "schema": "tp-voyager.usage/v1",
                        "provider": "qoder",
                        "model": "DeepSeek-V4-Flash-0731",
                        "usage": {"input_tokens": 100, "output_tokens": 25},
                    }
                },
                artifacts={
                    "task-1": [
                        SimpleNamespace(
                            artifact_id="art-1",
                            kind="patch",
                            name="login patch",
                            workspace_relpath="src/login.py",
                            capture_state="captured",
                            sha256="abc",
                            size_bytes=123,
                        )
                    ]
                },
            )
            projection = VoyageAgentProjection(service, store)
            detail = projection.detail("task-1", limit=50)

            self.assertTrue(detail["ok"])
            self.assertEqual(detail["schema"], "tp-voyager.agent_detail/v1")
            self.assertEqual(detail["task"]["state"], "running")
            self.assertEqual(detail["task"]["crew"], "qoder")
            self.assertEqual(detail["task"]["model"], "DeepSeek-V4-Flash-0731")
            self.assertEqual(detail["conversation"][0]["content"], "Inspecting login flow.")
            self.assertEqual(detail["timeline"][1]["tool"], "Read")
            self.assertEqual(detail["files"][0]["path"], "src/login.py")
            self.assertEqual(detail["usage"]["usage"]["input_tokens"], 100)

    def test_detail_rebuilds_activity_from_durable_events_when_memory_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            service = FakeTaskService(
                [task("task-1", "completed")],
                activity={
                    "task-1": [
                        {"kind": "agent_started", "at": 2.0},
                        {
                            "kind": "tool_activity",
                            "at": 4.0,
                            "tool": "Read",
                            "path": "agent_runtime/api/mcp_server.py",
                        },
                        {"kind": "agent_completed", "at": 9.0},
                    ]
                },
            )
            detail = VoyageAgentProjection(service, store).detail("task-1")

        self.assertEqual(detail["timeline"][1]["tool"], "Read")
        self.assertEqual(detail["timeline"][1]["path"], "agent_runtime/api/mcp_server.py")
        self.assertEqual(detail["latest_activity"]["kind"], "agent_completed")

    def test_running_conversation_never_exposes_machine_outcome_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            store.append(
                "task-1",
                {
                    "kind": "assistant_message",
                    "timestamp": 3.0,
                    "text": (
                        "正在整理结论。\n\n"
                        + CREW_OUTCOME_MARKER
                        + '{"schema":"tp-voyager.crew_outcome/v1","status":"COMPLETED"}'
                        + "\n"
                    ),
                },
            )
            projection = VoyageAgentProjection(
                FakeTaskService([task("task-1", "running")]),
                store,
            )

            detail = projection.detail("task-1")

            encoded = json.dumps(detail["conversation"], ensure_ascii=False)
            self.assertNotIn(CREW_OUTCOME_MARKER, encoded)
            self.assertEqual(detail["conversation"][0]["content"], "正在整理结论。\n\n")

    def test_terminal_detail_prefers_canonical_full_answer_and_projects_chinese_ready_result_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp), max_events_per_task=64)
            store.append(
                "task-1",
                {"kind": "assistant_message", "timestamp": 3.0, "text": "末尾片段"},
            )
            for index in range(300):
                store.append(
                    "task-1",
                    {"kind": "tool_activity", "timestamp": 10.0 + index, "tool": "Read", "status": "completed"},
                )
            outcome = {
                "schema": CREW_OUTCOME_SCHEMA,
                "status": "COMPLETED",
                "summary": "OpenMontage 最适合通用 Codex 视频制作。",
                "requested_files": [],
                "requested_commands": [],
                "findings": ["覆盖 12 条视频生产流水线。", "存在明确 Codex 入口。"],
                "evidence_refs": ["06-视频与音频生成/OpenMontage_智能体视频生产流水线.md"],
            }
            full_answer = (
                "# 首选分析\n\nOpenMontage 最适合通用 Codex 视频制作。\n\n"
                "## 注意\nAGPL-3.0；对外网络服务前需评估开源义务。\n\n"
                + CREW_OUTCOME_MARKER
                + json.dumps(outcome, ensure_ascii=False)
            )
            current = task("task-1", "completed")
            current.result_json = json.dumps(
                {
                    "answer": full_answer,
                    "risks": ["AGPL-3.0；对外服务前评估开源义务。"],
                    "crew_outcome": {**outcome, "available": True},
                },
                ensure_ascii=False,
            )
            projection = VoyageAgentProjection(FakeTaskService([current]), store)

            detail = projection.detail("task-1", limit=50)

            self.assertEqual(
                detail["full_answer"],
                "# 首选分析\n\nOpenMontage 最适合通用 Codex 视频制作。\n\n## 注意\nAGPL-3.0；对外网络服务前需评估开源义务。\n\n",
            )
            self.assertNotIn(CREW_OUTCOME_MARKER, json.dumps(detail, ensure_ascii=False))
            self.assertEqual(detail["conversation"][0]["content"], detail["full_answer"] )
            self.assertEqual(detail["result_card"]["conclusion"], outcome["summary"] )
            self.assertEqual(detail["result_card"]["key_evidence"], outcome["findings"] )
            self.assertEqual(detail["result_card"]["risks"], ["AGPL-3.0；对外服务前评估开源义务。"] )
            self.assertEqual(detail["task"]["duration_seconds"], 7.0)

    def test_strip_crew_outcome_marker_removes_only_protocol_line(self) -> None:
        marker = CREW_OUTCOME_MARKER + '{"schema":"tp-voyager.crew_outcome/v1"}'
        answer = "前文  保留\n\n```json\n{\"x\": 1}\n```\n" + marker + "\n"

        cleaned = strip_crew_outcome_marker(answer)

        self.assertEqual(cleaned, "前文  保留\n\n```json\n{\"x\": 1}\n```\n")

    def test_detail_lists_safe_modified_path_from_tool_activity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            store.append(
                "task-1",
                {
                    "kind": "tool_activity",
                    "timestamp": 4.0,
                    "tool": "Edit",
                    "path": "src/login.py",
                    "action": "modify",
                    "status": "requested",
                },
            )
            projection = VoyageAgentProjection(FakeTaskService([task("task-1", "running")]), store)

            detail = projection.detail("task-1")

            self.assertEqual(detail["files"], [
                {
                    "path": "src/login.py",
                    "action": "modify",
                    "source": "observation",
                    "timestamp": 4.0,
                }
            ])

    def test_explicit_group_projection_returns_only_exact_members_and_independent_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            tasks = [task("qoder-1", "completed", crew="qoder"), task("codebuddy-1", "running", crew="codebuddy"), task("other-1", "running", crew="qoder")]
            sessions = {
                "qoder-1": SimpleNamespace(metadata_json=json.dumps({"runtime": "qoder", "model": "lite", "routing_metadata": {"presentation_group_id": "grp-1"}})),
                "codebuddy-1": SimpleNamespace(metadata_json=json.dumps({"runtime": "codebuddy", "model": "hy3", "routing_metadata": {"presentation_group_id": "grp-1"}})),
                "other-1": SimpleNamespace(metadata_json=json.dumps({"runtime": "qoder", "model": "lite", "routing_metadata": {"presentation_group_id": "grp-other"}})),
            }
            projection = VoyageAgentProjection(FakeTaskService(tasks, sessions=sessions), store)

            grouped = projection.group(presentation_group_id="grp-1", limit=50)

            self.assertTrue(grouped["ok"])
            self.assertEqual(grouped["presentation_group_id"], "grp-1")
            self.assertEqual({item["task"]["task_id"] for item in grouped["tasks"]}, {"qoder-1", "codebuddy-1"})
            self.assertNotIn("other-1", json.dumps(grouped))

    def test_explicit_task_ids_group_projection_never_auto_selects_other_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            projection = VoyageAgentProjection(
                FakeTaskService([task("a", "running"), task("b", "completed"), task("c", "running")]), store
            )

            grouped = projection.group(task_ids=["b", "a"], limit=20)

            self.assertEqual([item["task"]["task_id"] for item in grouped["tasks"]], ["b", "a"])
            self.assertNotIn('"c"', json.dumps(grouped))

    def test_presence_defaults_to_active_then_recent_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            service = FakeTaskService(
                [
                    task("done", "completed", updated_at=20.0),
                    task("active", "observing", crew="codebuddy", updated_at=30.0),
                ],
                sessions={
                    "active": SimpleNamespace(metadata_json='{"model":"GLM-5.3","runtime":"codebuddy"}'),
                    "done": SimpleNamespace(metadata_json='{"model":"DeepSeek-V4-Pro-0813","runtime":"qoder"}'),
                },
            )
            projection = VoyageAgentProjection(service, store)
            presence = projection.presence(limit=2)

            self.assertTrue(presence["ok"])
            self.assertEqual(presence["schema"], "tp-voyager.agent_presence/v1")
            self.assertEqual(presence["tasks"][0]["task_id"], "active")
            self.assertEqual(presence["tasks"][0]["model"], "GLM-5.3")
            self.assertTrue(presence["tasks"][0]["active"])

    def test_failed_detail_does_not_expose_raw_durable_error_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            store.append(
                "task-1",
                {
                    "kind": "agent_failed",
                    "timestamp": 5.0,
                    "reason": "BackendTimeoutError",
                    "status": "failed",
                },
            )
            failed = task("task-1", "failed")
            failed.error_message = "secret credential at C:/Users/example/.env"
            projection = VoyageAgentProjection(FakeTaskService([failed]), store)

            detail = projection.detail("task-1")
            encoded = json.dumps(detail, ensure_ascii=False)

            self.assertEqual(detail["error"]["message"], "BackendTimeoutError")
            self.assertNotIn("secret credential", encoded)
            self.assertNotIn("C:/Users", encoded)

    def test_failed_detail_projects_safe_failure_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentObservationStore(Path(tmp))
            store.append(
                "task-1",
                {
                    "kind": "agent_failed",
                    "timestamp": 5.0,
                    "reason": "WorkspaceSnapshotError",
                    "phase": "workspace_snapshot",
                    "status": "failed",
                },
            )
            projection = VoyageAgentProjection(FakeTaskService([task("task-1", "failed")]), store)

            detail = projection.detail("task-1")

            self.assertEqual(detail["error"]["message"], "WorkspaceSnapshotError")
            self.assertEqual(detail["error"]["stage"], "workspace_snapshot")

    def test_unknown_task_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projection = VoyageAgentProjection(FakeTaskService([]), AgentObservationStore(Path(tmp)))
            result = projection.detail("missing")
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason_code"], "TASK_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
