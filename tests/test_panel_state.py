"""v1.0.9.3: Panel UI state lifecycle and concurrent workbench tests.

State is kept only in iframe memory (``PanelUIStateStore``) keyed per
presentation group / task, restored after render and on page lifecycle
events, and never persisted to ``localStorage``, the database or the server.
The concurrent group view is a left task-navigation + right detail-tab
workbench with status-based default tab selection.
"""

from __future__ import annotations

import unittest

from agent_runtime.api.voyager_panel import render_voyager_panel_html


class PanelStateTests(unittest.TestCase):
    def test_state_store_is_iframe_memory_only(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("const PanelUIStateStore = new Map();", html)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("indexedDB", html)
        self.assertNotIn("fetch(", html)

    def test_state_captures_tab_section_scroll_and_expanded_details(self) -> None:
        html = render_voyager_panel_html()
        for field in (
            "activeTab",
            "selectedTaskId",
            "section",
            "scrollTop",
            "workbenchScrollTop",
            "workbenchPinnedToBottom",
            "expandedDetails",
            "timestamp",
        ):
            self.assertIn(field, html)
        self.assertIn("function collectExpandedDetails()", html)

    def test_running_activity_tab_can_restore_or_follow_workbench_scroll(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("function captureWorkbenchScrollState()", html)
        self.assertIn("function restoreWorkbenchScrollState(", html)
        self.assertIn("WORKBENCH_BOTTOM_THRESHOLD_PX", html)
        self.assertIn("body.scrollTop = body.scrollHeight", html)

    def test_state_key_is_per_task_and_per_group(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("function panelStateKey(", html)
        self.assertIn("presentation_group_id", html)
        self.assertIn("task_id", html)
        # The composite key keeps Task A independent from Task B and Group A
        # independent from Group B.
        self.assertIn('return `${group || "single"}/${task || "none"}/panel`;', html)

    def test_save_and_restore_lifecycle_hooks_exist(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("function beforeRefresh()", html)
        self.assertIn("function savePanelUIState()", html)
        self.assertIn("function restorePanelUIState()", html)
        self.assertIn("savePanelUIState();", html)
        self.assertIn("restorePanelUIState();", html)

    def test_page_lifecycle_listeners_preserve_state(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn('window.addEventListener("pagehide", savePanelUIState', html)
        self.assertIn('document.addEventListener("visibilitychange"', html)
        self.assertIn('else savePanelUIState();', html)
        self.assertIn('window.addEventListener("pageshow"', html)
        self.assertIn('window.addEventListener("focus"', html)

    def test_workbench_has_five_detail_tabs(self) -> None:
        html = render_voyager_panel_html()
        for label in ("摘要", "完整回答", "执行活动", "文件变更", "用量"):
            self.assertIn(label, html)
        self.assertIn("const GROUP_TABS = [", html)

    def test_workbench_default_tab_follows_task_state(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("function defaultTabForState(", html)
        # running -> execution activity; completed/failed -> summary.
        self.assertIn('return "执行活动";', html)
        self.assertIn('return "摘要";', html)

    def test_workbench_task_navigation_shows_required_fields(self) -> None:
        html = render_voyager_panel_html()
        for marker in (
            "wb-task-state",
            "wb-task-meta",
            "wb-task-id",
            "wb-task-duration",
            "wb-task-failure",
            "task.error_message",
        ):
            self.assertIn(marker, html)

    def test_workbench_responsive_layout_collapses_navigation(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn(".workbench { display: flex;", html)
        self.assertIn("@media (max-width: 520px)", html)


if __name__ == "__main__":
    unittest.main()
