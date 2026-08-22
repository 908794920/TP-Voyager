"""v1.0.9: Markdown safe-reader security tests.

The panel renders model output through an escape-first minimal Markdown
renderer.  These tests assert the renderer's whitelist/escape structure in the
self-contained iframe HTML; there is no JS engine in the test process, so the
assertions mirror the same string-contract style used by ``test_voyager_panel``.
"""

from __future__ import annotations

import unittest

from agent_runtime.api.voyager_panel import render_voyager_panel_html


class MarkdownSecurityTests(unittest.TestCase):
    def test_safe_markdown_renderer_supports_required_features(self) -> None:
        html = render_voyager_panel_html()
        for fragment in (
            "function renderSafeMarkdown(",
            "function escapeHtml(",
            "<strong>$1</strong>",       # bold
            "<em>$2</em>",               # italic
            "<ul>$1</ul>",               # list
            "<pre><code>",               # fenced code block
            "<blockquote>$1</blockquote>",  # blockquote
            "<table>",                   # table
            "safeLinkHref(",             # link whitelist
            "<a href=",
            "<h3>$1</h3>", "<h2>$1</h2>", "<h1>$1</h1>",  # headings
        ):
            self.assertIn(fragment, html)

    def test_input_is_escaped_before_any_tag_emission(self) -> None:
        html = render_voyager_panel_html()
        # Escape-first pipeline: every value goes through escapeHtml before
        # whitelisted tags are emitted, so "<script>alert(1)</script>" cannot
        # survive as markup and never executes.
        self.assertIn("let text = escapeHtml(value);", html)
        self.assertIn('.replaceAll("<", "&lt;")', html)
        self.assertIn('.replaceAll("&", "&amp;")', html)

    def test_no_inner_html_assignment_and_scriptless_node_build(self) -> None:
        html = render_voyager_panel_html()
        # No innerHTML assignment anywhere; answer rows are built via DOMParser
        # nodes, which do not execute scripts.
        self.assertNotIn("innerHTML =", html)
        self.assertIn("new DOMParser()", html)
        self.assertIn("renderSafeMarkdown(markdown)", html)

    def test_active_markup_and_unsafe_sinks_are_absent(self) -> None:
        html = render_voyager_panel_html()
        self.assertNotIn("<iframe", html)
        self.assertNotIn("onclick", html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("{=html}", html)
        self.assertNotIn("localStorage", html)

    def test_links_are_protocol_allowlisted(self) -> None:
        html = render_voyager_panel_html()
        self.assertIn("/^(https?:|mailto:)/i.test(raw)", html)
        self.assertIn("safeLinkHref(href)", html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_renderer_keeps_no_external_network_dependency(self) -> None:
        html = render_voyager_panel_html()
        self.assertNotRegex(html, r"https?://")


if __name__ == "__main__":
    unittest.main()
