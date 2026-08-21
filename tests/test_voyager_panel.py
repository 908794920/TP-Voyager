from __future__ import annotations

import re
import unittest
from pathlib import Path

from agent_runtime.api.voyager_panel import (
    VOYAGER_PANEL_MIME_TYPE,
    VOYAGER_PANEL_URI,
    render_voyager_panel_html,
)


ROOT = Path(__file__).resolve().parents[1]


class VoyagerPanelHtmlTests(unittest.TestCase):
    def test_panel_is_self_contained_mcp_app_with_read_only_live_refresh(self) -> None:
        html = render_voyager_panel_html()
        self.assertEqual(VOYAGER_PANEL_URI, "ui://tp-voyager/agent-panel/v1.html")
        self.assertEqual(VOYAGER_PANEL_MIME_TYPE, "text/html;profile=mcp-app")
        self.assertIn('<html lang="zh-CN">', html)
        self.assertIn('request("tools/call"', html)
        self.assertIn('name: "render_voyager_panel"', html)

    def test_bridge_requests_have_bounded_timeout_and_cleanup(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("const REQUEST_TIMEOUT_MS", html)
        self.assertIn("function request(method, params, timeoutMs = REQUEST_TIMEOUT_MS)", html)
        self.assertIn("pendingRequests.delete(id);", html)
        self.assertIn('reject(new Error(`MCP bridge request timed out: ${method}`))', html)
        self.assertIn("clearTimeout(pending.timeoutId)", html)

    def test_resume_sync_waits_for_first_verified_snapshot(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("let hasVerifiedSnapshot = false;", html)
        self.assertIn("hasVerifiedSnapshot = true;", html)
        self.assertIn("if (!hasVerifiedSnapshot) return;", html)

    def test_unchanged_projection_can_skip_full_workbench_rebuild(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("let lastRenderedRevision = \"\";", html)
        self.assertIn("function snapshotRevision(data)", html)
        self.assertIn("if (revision === lastRenderedRevision)", html)
        self.assertIn('ui/notifications/tool-result', html)
        self.assertIn('ui/initialize', html)
        self.assertIn('ui/notifications/initialized', html)
        self.assertIn('2026-01-26', html)
        self.assertIn('ui/notifications/tool-input', html)
        self.assertIn('version: "1.0.9.3"', html)
        self.assertIn('setTimeout(refresh', html)
        self.assertNotIn('setTimeout(refresh, 80)', html)
        self.assertNotIn("task_dispatch", html)
        self.assertNotIn("task_resume", html)
        self.assertNotIn("task_cancel", html)
        self.assertNotIn("localStorage", html)
        self.assertNotRegex(html, r"https?://")

    def test_panel_syncs_immediately_on_dispatch_and_host_resume_without_showing_stale_running(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn('data-task-state="unknown"', html)
        self.assertIn('data-sync-state="syncing"', html)
        self.assertNotIn('data-state="syncing"', html)
        self.assertIn('setTaskState(', html)
        self.assertIn('setSyncState(', html)
        self.assertIn('syncing: "正在同步"', html)
        self.assertIn('pending: "等待中"', html)
        self.assertIn('requested: "已请求"', html)
        self.assertIn('tool_activity: "工具活动"', html)
        self.assertIn('function actionLabel(', html)
        self.assertIn('function toolLabel(', html)
        self.assertIn('toolLabel(item.tool)', html)
        self.assertIn('function renderSyncing(', html)
        self.assertIn('renderSyncing(selector', html)
        self.assertIn('void refresh();', html)
        self.assertIn('document.addEventListener("visibilitychange"', html)
        self.assertIn('window.addEventListener("pageshow"', html)
        self.assertIn('window.addEventListener("focus"', html)
        self.assertIn('正在同步最新任务状态…', html)
        self.assertNotIn('state = String(snapshot.status || "connecting")', html)

    def test_completed_panel_is_chinese_result_first_and_process_is_collapsed(self) -> None:
        html = render_voyager_panel_html()
        for label in (
            "状态",
            "执行单元",
            "执行模型",
            "耗时",
            "结论",
            "关键依据",
            "风险",
            "下一步",
            "完整回答",
            "执行活动",
            "文件变更",
            "用量",
            "刷新",
        ):
            self.assertIn(label, html)
        self.assertIn('appendSection("执行活动", timelineRows(latestData.timeline), false)', html)
        self.assertNotIn('["Crew", task?.crew]', html)
        self.assertIn('if (!rows.length) return;', html)
        self.assertNotIn('Conversation', html)
        self.assertNotIn('Current activity', html)
        self.assertNotIn('Agent has not produced conversation output yet.', html)
        self.assertNotIn('lastMessage', html)

    def test_panel_preserves_answer_formatting_and_avoids_nested_log_scroll(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn('white-space: pre-wrap', html)
        self.assertIn('className = "answer markdown"', html)
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML =", html)
        self.assertNotRegex(html, r"\.list\s*\{[^}]*max-height")
        self.assertNotRegex(html, r"\.list\s*\{[^}]*overflow:\s*auto")
        self.assertNotIn("TP_VOYAGER_CREW_OUTCOME_JSON", html)

    def test_panel_has_obvious_status_dot_and_border_states(self) -> None:
        html = render_voyager_panel_html()
        for state in (
            "queued",
            "connecting",
            "running",
            "observing",
            "completed",
            "failed",
            "cancelled",
            "lost",
            "orphaned",
        ):
            self.assertIn(f'data-task-state="{state}"', html)
        self.assertIn('#panel[data-sync-state="syncing"]', html)
        self.assertIn('.wb-task[data-task-state="completed"]', html)
        self.assertNotIn('data-state="', html)
        self.assertIn("status-dot", html)
        self.assertIn("@keyframes pulse", html)
        self.assertIn("border-color", html)

    def test_panel_escapes_dynamic_text_via_text_content(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML =", html)

    def test_group_view_uses_concurrent_workbench_layout(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("function renderGroupBody(", html)
        self.assertIn('node("div", null, "workbench")', html)
        self.assertIn('node("nav", null, "wb-nav")', html)
        self.assertIn('node("div", null, "wb-main")', html)
        self.assertIn('node("div", null, "wb-tabs")', html)
        self.assertIn('node("div", null, "wb-body")', html)
        self.assertIn("renderGroupBody();", html)

    def test_single_task_is_normalized_to_fixed_group_without_sync_blank(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("function normalizePanelGroup(data)", html)
        self.assertIn("tasks: [data]", html)
        self.assertIn("task_ids: [String(task.task_id)]", html)
        self.assertIn("const normalized = normalizePanelGroup(data);", html)

        start = html.index("function renderSyncing(")
        end = html.index("\n  function scheduleRefresh", start)
        syncing_source = html[start:end]
        self.assertNotIn("detailsEl.replaceChildren();", syncing_source)


class VoyagerPanelMcpContractTests(unittest.TestCase):
    def test_captain_surface_includes_read_only_render_tool(self) -> None:
        schemas = (ROOT / "agent_runtime" / "api" / "schemas.py").read_text(encoding="utf-8")
        self.assertIn('"render_voyager_panel"', schemas)

    def test_server_registers_ui_resource_and_presence_metadata_on_dispatch_and_render(self) -> None:
        source = (ROOT / "agent_runtime" / "api" / "mcp_server.py").read_text(encoding="utf-8")
        self.assertIn("VOYAGER_PANEL_URI", source)
        self.assertIn("VOYAGER_PANEL_MIME_TYPE", source)
        self.assertIn('@mcp.resource(', source)
        self.assertIn('"resourceUri": VOYAGER_PANEL_URI', source)
        self.assertIn('def render_voyager_panel(', source)
        self.assertIn('"schema": "tp-voyager.agent_panel/v1"', source)
        self.assertIn('"prefersBorder": True', source)
        self.assertIn('"connectDomains": []', source)
        self.assertIn('"resourceDomains": []', source)
        dispatch_index = source.index("def task_dispatch(")
        dispatch_metadata = source[max(0, dispatch_index - 900):dispatch_index]
        self.assertIn('"resourceUri": VOYAGER_PANEL_URI', dispatch_metadata)
        self.assertIn('structured_output=True', dispatch_metadata)
        self.assertIn('"openai/toolInvocation/invoking"', dispatch_metadata)
        self.assertIn('正在启动 TP-Voyager 任务…', dispatch_metadata)
        render_index = source.index('def render_voyager_panel(')
        render_metadata = source[max(0, render_index - 900):render_index]
        self.assertIn('正在加载 TP-Voyager 任务…', render_metadata)

    def test_server_projects_safe_exception_phase_into_failed_observation(self) -> None:
        source = (ROOT / "agent_runtime" / "api" / "mcp_server.py").read_text(encoding="utf-8")
        self.assertIn('failure_phase = getattr(exc, "phase", None)', source)
        self.assertIn('_AGENT_OBSERVATIONS.failed(task, reason=type(exc).__name__, phase=failure_phase)', source)

    def test_render_tool_accepts_only_explicit_single_or_group_selectors(self) -> None:
        source = (ROOT / "agent_runtime" / "api" / "mcp_server.py").read_text(encoding="utf-8")
        start = source.index("def render_voyager_panel(")
        end = source.index("\n\n@_mcp_tool(", start)
        render_source = source[start:end]
        self.assertIn('presentation_group_id: str = ""', render_source)
        self.assertIn('task_ids: list[str] | None = None', render_source)
        self.assertIn('AMBIGUOUS_PANEL_SELECTOR', render_source)
        self.assertIn('projection.group(', render_source)
        self.assertNotIn("correlation_id", render_source)
        self.assertNotIn("projection.presence(", render_source)

    def test_panel_html_can_keep_an_explicit_group_selector_for_read_only_refresh(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("presentation_group_id", html)
        self.assertIn("task_ids", html)
        self.assertIn('mode === "group"', html)
        self.assertIn("并发任务组", html)
        self.assertIn("子任务", html)

    def test_render_tool_without_task_id_does_not_auto_select_another_runtime_task(self) -> None:
        source = (ROOT / "agent_runtime" / "api" / "mcp_server.py").read_text(encoding="utf-8")
        start = source.index("def render_voyager_panel(")
        end = source.index("\n\n@_mcp_tool(", start)
        render_source = source[start:end]
        self.assertNotIn("projection.presence(", render_source)
        self.assertIn('"mode": "empty"', render_source)
        self.assertIn('"scope": "current_conversation"', render_source)

    def test_mcp_sdk_floor_supports_apps_metadata(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn('"mcp>=1.28,<2"', pyproject)
        self.assertIn("mcp>=1.28,<2", requirements)


if __name__ == "__main__":
    unittest.main()
