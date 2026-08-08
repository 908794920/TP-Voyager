from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

from agent_runtime import cli, server
from agent_runtime.runtime.diagnostics import RuntimeInspector
from agent_runtime.persistence.database import Database
from agent_runtime.application.knowledge_service import (
    KnowledgeConflictError,
    KnowledgePolicyError,
    KnowledgeRuntimeService,
)


class KnowledgeRuntimeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "SOUL.md").write_text(
            "# Project Rules\n\nNever guess. Runtime owns durable state.\n",
            encoding="utf-8",
        )
        (self.project / "docs").mkdir()
        (self.project / "docs" / "architecture.md").write_text(
            "# Runtime Architecture\n\nSQLite is the single source of truth.\n"
            "Backends never own task lifecycle state.\n",
            encoding="utf-8",
        )
        (self.project / "docs" / "ADR-001.md").write_text(
            "# ADR-001\n\nDecision: use explicit read-only tools.\n",
            encoding="utf-8",
        )
        (self.project / "docs" / "经验.md").write_text(
            "# 经验\n\n任务超时时先检查当前运行的最终回复。\n",
            encoding="utf-8",
        )
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()
        self.service = KnowledgeRuntimeService(self.db)

    def tearDown(self) -> None:
        server.configure_runtime_database(None)
        self.tmp.cleanup()

    def register(self, knowledge_id: str = "knw-test") -> dict:
        result = self.service.register(
            str(self.project),
            ["SOUL.md", "docs/architecture.md", "docs/ADR-001.md", "docs/经验.md"],
            knowledge_id=knowledge_id,
            name="Runtime Knowledge",
        )
        return result.collection

    def test_register_is_content_free_and_classifies_sources(self) -> None:
        collection = self.register()
        self.assertEqual(collection["schema"], "workbuddy.knowledge_status/v1")
        self.assertFalse(collection["content_stored"])
        self.assertFalse(collection["cwd_stored"])
        kinds = {item["relpath"]: item["kind"] for item in collection["sources"]}
        self.assertEqual(kinds["SOUL.md"], "rules")
        self.assertEqual(kinds["docs/architecture.md"], "architecture")
        self.assertEqual(kinds["docs/ADR-001.md"], "decision")
        with closing(sqlite3.connect(self.db.path)) as connection:
            dump = "\n".join(
                str(value)
                for table in ("knowledge_collections", "knowledge_sources")
                for row in connection.execute(f"SELECT * FROM {table}").fetchall()
                for value in row
            )
            schema = "\n".join(
                str(row[0])
                for row in connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name LIKE 'knowledge_%'"
                ).fetchall()
            )
        self.assertNotIn("Never guess", dump)
        self.assertNotIn(str(self.project), dump)
        self.assertNotIn("content", schema.lower())
        self.assertNotIn("query_text", schema.lower())

    def test_register_is_idempotent_and_conflict_is_explicit(self) -> None:
        first = self.service.register(
            str(self.project), ["SOUL.md"], knowledge_id="knw-idem", name="Rules"
        )
        second = self.service.register(
            str(self.project), ["SOUL.md"], knowledge_id="knw-idem", name="Rules"
        )
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        with self.assertRaises(KnowledgeConflictError):
            self.service.register(
                str(self.project),
                ["SOUL.md"],
                knowledge_id="knw-idem",
                name="Different Name",
            )

    def test_explicit_source_kind_must_reference_registered_source(self) -> None:
        with self.assertRaises(KnowledgePolicyError):
            self.service.register(
                str(self.project),
                ["SOUL.md"],
                knowledge_id="knw-kind",
                source_kinds={"missing.md": "rules"},
            )
        result = self.service.register(
            str(self.project),
            ["SOUL.md"],
            knowledge_id="knw-kind-ok",
            source_kinds={"SOUL.md": "reference"},
        )
        self.assertEqual(result.collection["sources"][0]["kind"], "reference")

    def test_search_returns_verified_citations_and_audits_hashes_only(self) -> None:
        self.register()
        query = "single source of truth"
        result = self.service.search("knw-test", str(self.project), query)
        self.assertTrue(result["ok"])
        self.assertEqual(result["schema"], "workbuddy.knowledge_search/v1")
        self.assertGreaterEqual(result["citation_count"], 1)
        citation = result["citations"][0]
        self.assertEqual(citation["relpath"], "docs/architecture.md")
        self.assertIn("single source of truth", citation["snippet"])
        self.assertFalse(result["injected_into_task"])
        history = self.service.history(knowledge_id="knw-test")["resolutions"]
        self.assertEqual(history[0]["status"], "succeeded")
        encoded = json.dumps(history, ensure_ascii=False)
        self.assertNotIn(query, encoded)
        self.assertNotIn("single source of truth", encoded)
        self.assertNotIn(str(self.project), encoded)
        self.assertEqual(len(history[0]["query_sha256"]), 64)

    def test_chinese_search_and_kind_filter(self) -> None:
        self.register()
        result = self.service.search(
            "knw-test", str(self.project), "任务超时最终回复", kind="reference"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["citations"][0]["relpath"], "docs/经验.md")
        empty = self.service.search(
            "knw-test", str(self.project), "任务超时最终回复", kind="architecture"
        )
        self.assertTrue(empty["ok"])
        self.assertEqual(empty["citation_count"], 0)

    def test_bundle_is_bounded_cited_and_never_auto_injected(self) -> None:
        self.register()
        result = self.service.bundle(
            "knw-test",
            str(self.project),
            "Runtime state explicit tools",
            max_sources=4,
            max_total_bytes=2048,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["schema"], "workbuddy.knowledge_bundle/v1")
        self.assertLessEqual(len(result["content"].encode("utf-8")), 2048)
        self.assertIn("Knowledge Bundle", result["content"])
        self.assertIn("SHA-256", result["content"])
        self.assertFalse(result["injected_into_task"])
        self.assertFalse(result["automatic_writeback"])

    def test_invalid_query_and_task_reference_are_rejected_and_audited(self) -> None:
        self.register()
        invalid = self.service.search("knw-test", str(self.project), "")
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["status"], "rejected")
        task = self.service.search(
            "knw-test", str(self.project), "runtime", task_id="wb-missing"
        )
        self.assertFalse(task["ok"])
        history = self.service.history(status="rejected")["resolutions"]
        self.assertEqual(len(history), 2)
        self.assertTrue(all(item["raw_query_stored"] is False for item in history))

    def test_task_link_is_optional_and_validated(self) -> None:
        self.register()
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at) "
                "VALUES ('wb-knowledge', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
            )
        result = self.service.search(
            "knw-test", str(self.project), "runtime", task_id="wb-knowledge"
        )
        self.assertTrue(result["ok"])
        history = self.service.history(task_id="wb-knowledge")["resolutions"]
        self.assertEqual(history[0]["task_id"], "wb-knowledge")

    def test_drift_never_returns_stale_content_and_is_audited(self) -> None:
        self.register()
        (self.project / "SOUL.md").write_text("changed", encoding="utf-8")
        result = self.service.search("knw-test", str(self.project), "rules")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "knowledge_drift")
        history = self.service.history(status="failed")["resolutions"]
        self.assertEqual(history[0]["error_code"], "knowledge_drift")
        self.assertEqual(history[0]["bytes_returned"], 0)

    def test_verify_and_list_are_content_free(self) -> None:
        collection = self.register()
        verified = self.service.verify("knw-test", str(self.project))
        self.assertTrue(verified["valid"])
        listed = self.service.list()
        self.assertEqual(listed["collections"][0]["knowledge_id"], "knw-test")
        encoded = json.dumps([collection, verified, listed], ensure_ascii=False)
        self.assertNotIn("Never guess", encoded)
        self.assertNotIn(str(self.project), encoded)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_external_symlink_requires_explicit_opt_in_for_register_and_search(self) -> None:
        external = self.root / "external.md"
        external.write_text("external durable rule", encoding="utf-8")
        link = self.project / "linked.md"
        try:
            link.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        with self.assertRaises(KnowledgePolicyError):
            self.service.register(
                str(self.project), ["linked.md"], knowledge_id="knw-link-deny"
            )
        self.service.register(
            str(self.project),
            ["linked.md"],
            knowledge_id="knw-link",
            allow_external_symlinks=True,
        )
        denied = self.service.search("knw-link", str(self.project), "durable")
        self.assertFalse(denied["ok"])
        allowed = self.service.search(
            "knw-link",
            str(self.project),
            "durable",
            allow_external_symlinks=True,
        )
        self.assertTrue(allowed["ok"])

    def test_resolution_lookup_is_content_free(self) -> None:
        self.register()
        search = self.service.search("knw-test", str(self.project), "Runtime")
        value = self.service.get_resolution(search["resolution_id"])
        self.assertTrue(value["ok"])
        encoded = json.dumps(value)
        self.assertNotIn("Runtime owns", encoded)
        self.assertFalse(value["raw_output_stored"])


class KnowledgeRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "README.md").write_text(
            "# Demo\n\nKnowledge runtime integration.\n", encoding="utf-8"
        )
        self.db_path = self.root / "runtime.db"
        server.configure_runtime_database(self.db_path)

    def tearDown(self) -> None:
        server.configure_runtime_database(None)
        self.tmp.cleanup()

    def test_server_register_search_bundle_history(self) -> None:
        registered = server.knowledge_register(
            str(self.project), ["README.md"], "knw-api", "API Knowledge"
        )
        self.assertTrue(registered["ok"], registered)
        status = server.knowledge_status("knw-api")
        self.assertTrue(status["ok"])
        search = server.knowledge_search(
            "knw-api", str(self.project), "integration"
        )
        self.assertTrue(search["ok"], search)
        bundle = server.knowledge_bundle(
            "knw-api", str(self.project), "integration", max_total_bytes=2048
        )
        self.assertTrue(bundle["ok"], bundle)
        history = server.knowledge_history("knw-api")
        self.assertEqual(len(history["resolutions"]), 2)
        one = server.knowledge_resolution(search["resolution_id"])
        self.assertTrue(one["ok"])

    def test_diagnostics_and_cli_are_content_free(self) -> None:
        server.knowledge_register(
            str(self.project), ["README.md"], "knw-cli", "CLI Knowledge"
        )
        server.knowledge_search("knw-cli", str(self.project), "integration")
        inspector = RuntimeInspector(self.db_path)
        overview = inspector.overview().to_dict()
        self.assertEqual(overview["knowledge_collection_count"], 1)
        self.assertEqual(overview["knowledge_resolution_count"], 1)
        collections = inspector.list_knowledge_collections()
        resolutions = inspector.list_knowledge_resolutions()
        encoded = json.dumps([collections, resolutions])
        self.assertNotIn("Knowledge runtime integration", encoded)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(["--db", str(self.db_path), "knowledge-list"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["collections"][0]["knowledge_id"], "knw-cli")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(
                ["--db", str(self.db_path), "knowledge-history", "--knowledge-id", "knw-cli"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(stdout.getvalue())["resolutions"]), 1)


if __name__ == "__main__":
    unittest.main()
