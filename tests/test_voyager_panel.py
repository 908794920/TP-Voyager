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
    def test_panel_is_self_contained_mcp_app_with_live_refresh(self) -> None:
        html = render_voyager_panel_html()
        self.assertEqual(VOYAGER_PANEL_URI, "ui://tp-voyager/agent-panel/v1.html")
        self.assertEqual(VOYAGER_PANEL_MIME_TYPE, "text/html;profile=mcp-app")
        self.assertIn('request("tools/call"', html)
        self.assertIn('name: "render_voyager_panel"', html)
        self.assertIn('ui/notifications/tool-result', html)
        self.assertIn('ui/initialize', html)
        self.assertIn('ui/notifications/initialized', html)
        self.assertIn('2026-01-26', html)
        self.assertIn('ui/notifications/tool-input', html)
        self.assertIn('version: "1.0.9.1"', html)
        self.assertIn('setTimeout(refresh', html)
        self.assertIn('snapshot?.task_id', html)
        self.assertIn('setTimeout(refresh, 80)', html)
        self.assertNotIn("localStorage", html)
        self.assertNotRegex(html, r"https?://")

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
            self.assertIn(f'data-state="{state}"', html)
        self.assertIn("status-dot", html)
        self.assertIn("@keyframes pulse", html)
        self.assertIn("border-color", html)
        self.assertIn("Conversation", html)
        self.assertIn("Timeline", html)
        self.assertIn("Files", html)
        self.assertIn("Usage", html)

    def test_panel_prioritizes_agent_identity_failure_stage_and_live_activity(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("Agent execution failed", html)
        self.assertIn("Stage", html)
        self.assertIn("Crew", html)
        self.assertIn("Model", html)
        self.assertIn("Task", html)
        self.assertIn("Current activity", html)
        self.assertIn("Agent has not produced conversation output yet.", html)
        self.assertIn("Agent did not produce conversation output before the failure.", html)
        self.assertIn('section("Timeline", timelineRows(latestData.timeline), true', html)
        self.assertNotIn('section("Timeline", timelineRows(latestData.timeline), false', html)

    def test_panel_escapes_dynamic_text_via_text_content(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML =", html)


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

    def test_server_projects_safe_exception_phase_into_failed_observation(self) -> None:
        source = (ROOT / "agent_runtime" / "api" / "mcp_server.py").read_text(encoding="utf-8")
        self.assertIn('failure_phase = getattr(exc, "phase", None)', source)
        self.assertIn('_AGENT_OBSERVATIONS.failed(task, reason=type(exc).__name__, phase=failure_phase)', source)

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
