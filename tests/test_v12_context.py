from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_runtime.persistence.database import Database
from agent_runtime.application.context_service import (
    ContextConflictError,
    ContextDriftError,
    ContextError,
    ProjectContextService,
)


class ProjectContextServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "SOUL.md").write_text("# Rules\nNever guess.\n", encoding="utf-8")
        (self.project / "docs").mkdir()
        (self.project / "docs" / "architecture.md").write_text(
            "# Architecture\nRuntime owns state.\n", encoding="utf-8"
        )
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()
        self.service = ProjectContextService(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_register_persists_only_manifest_metadata(self) -> None:
        result = self.service.register(
            str(self.project), ["SOUL.md", "docs/architecture.md"], context_id="ctx-test"
        )
        self.assertFalse(result.replayed)
        self.assertFalse(result.manifest["content_stored"])
        self.assertFalse(result.manifest["cwd_stored"])
        with sqlite3.connect(self.db.path) as connection:
            sql = "\n".join(
                str(row[0]) for row in connection.execute(
                    "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
                ).fetchall()
            )
            values = "\n".join(
                str(value)
                for row in connection.execute(
                    "SELECT context_id, root_hash FROM context_manifests"
                ).fetchall()
                for value in row
            )
        self.assertNotIn("Never guess", values)
        self.assertNotIn(str(self.project), values)
        self.assertNotIn("content", sql.lower())

    def test_same_context_registration_replays_and_different_conflicts(self) -> None:
        first = self.service.register(str(self.project), ["SOUL.md"], context_id="ctx-idem")
        second = self.service.register(str(self.project), ["SOUL.md"], context_id="ctx-idem")
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        with self.assertRaises(ContextConflictError):
            self.service.register(
                str(self.project), ["docs/architecture.md"], context_id="ctx-idem"
            )

    def test_path_escape_and_absolute_paths_fail_closed(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        for value in ("../outside.md", str(outside), "docs//architecture.md", "docs/./architecture.md"):
            with self.subTest(value=value), self.assertRaises(ContextError):
                self.service.register(str(self.project), [value])

    def test_verify_and_render_require_unchanged_utf8_content(self) -> None:
        result = self.service.register(str(self.project), ["SOUL.md"], context_id="ctx-render")
        verified = self.service.verify("ctx-render", str(self.project))
        self.assertTrue(verified["valid"], verified)
        rendered = self.service.render("ctx-render", str(self.project))
        self.assertTrue(rendered["content_returned"])
        self.assertFalse(rendered["injected_into_task"])
        self.assertIn("Never guess", rendered["content"])
        (self.project / "SOUL.md").write_text("changed", encoding="utf-8")
        drift = self.service.verify("ctx-render", str(self.project))
        self.assertFalse(drift["valid"])
        with self.assertRaises(ContextDriftError):
            self.service.render("ctx-render", str(self.project))
        self.assertEqual(result.manifest["context_id"], "ctx-render")

    def test_render_rejects_binary_and_limit(self) -> None:
        binary = self.project / "binary.dat"
        binary.write_bytes(b"abc\x00def")
        self.service.register(str(self.project), ["binary.dat"], context_id="ctx-binary")
        with self.assertRaises(ContextError):
            self.service.render("ctx-binary", str(self.project))
        self.service.register(str(self.project), ["SOUL.md"], context_id="ctx-limit")
        with self.assertRaises(ContextError):
            self.service.render("ctx-limit", str(self.project), max_total_bytes=1)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_external_symlink_requires_explicit_opt_in(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("external rules", encoding="utf-8")
        link = self.project / "external.md"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        with self.assertRaises(ContextError):
            self.service.register(str(self.project), ["external.md"])
        result = self.service.register(
            str(self.project),
            ["external.md"],
            context_id="ctx-link",
            allow_external_symlinks=True,
        )
        self.assertEqual(result.manifest["file_count"], 1)
        rendered = self.service.render(
            "ctx-link", str(self.project), allow_external_symlinks=True
        )
        self.assertIn("external rules", rendered["content"])


if __name__ == "__main__":
    unittest.main()
