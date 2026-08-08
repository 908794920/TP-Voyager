from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_runtime.persistence.database import Database
from agent_runtime.persistence.errors import RuntimePersistenceError
from agent_runtime.persistence.migrations import (
    SCHEMA_VERSION,
    migrate,
    schema_version,
)

EXPECTED_TABLES = {
    "tasks", "sessions", "attempts", "events", "idempotency",
    "evidences", "artifacts", "task_lineage",
    "workflows", "workflow_stages", "workflow_approvals", "workflow_events",
    "context_manifests", "context_entries", "tool_invocations",
    "knowledge_collections", "knowledge_sources", "knowledge_resolutions",
    "planner_plans", "planner_steps", "planner_dependencies", "planner_events",
    "plan_executions", "plan_execution_steps", "plan_execution_events", "plan_results",
}


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "nested" / "runtime.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_database_initializes_to_current_schema(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        self.assertTrue(self.db_path.is_file())
        self.assertEqual(db.schema_version(), SCHEMA_VERSION)
        with db.connect() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue(EXPECTED_TABLES.issubset(tables))

    def test_repeated_migration_is_idempotent(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        db.initialize()
        db.initialize()
        self.assertEqual(db.schema_version(), SCHEMA_VERSION)
        with db.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
        # Idempotent runs must not duplicate tables.
        self.assertEqual(count, len(EXPECTED_TABLES) + 1)  # + sqlite_sequence

    def test_migrate_is_repeatable_on_existing_connection(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        with db.connect() as connection:
            migrate(connection)
            migrate(connection)
            self.assertEqual(schema_version(connection), SCHEMA_VERSION)

    def test_schema_six_upgrades_to_context_manifest_tables(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        with db.connect() as connection:
            with connection:
                connection.execute(
                    "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at) "
                    "VALUES ('wb-v6-keep', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
                )
                connection.execute("DROP TABLE context_entries")
                connection.execute("DROP TABLE context_manifests")
                connection.execute("PRAGMA user_version = 6")
        db.initialize()
        with db.connect() as connection:
            self.assertEqual(schema_version(connection), SCHEMA_VERSION)
            self.assertIsNotNone(
                connection.execute(
                    "SELECT task_id FROM tasks WHERE task_id = 'wb-v6-keep'"
                ).fetchone()
            )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertIn("context_manifests", tables)
        self.assertIn("context_entries", tables)

    def test_schema_seven_upgrades_to_tool_invocation_audit(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        with db.connect() as connection:
            with connection:
                connection.execute(
                    "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at) "
                    "VALUES ('wb-v7-keep', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
                )
                connection.execute("DROP TABLE tool_invocations")
                connection.execute("PRAGMA user_version = 7")
        db.initialize()
        with db.connect() as connection:
            self.assertEqual(schema_version(connection), SCHEMA_VERSION)
            self.assertIsNotNone(
                connection.execute(
                    "SELECT task_id FROM tasks WHERE task_id = 'wb-v7-keep'"
                ).fetchone()
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_invocations'"
                ).fetchone()
            )

    def test_schema_eight_upgrades_to_knowledge_runtime_tables(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        with db.connect() as connection:
            with connection:
                connection.execute(
                    "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at) "
                    "VALUES ('wb-v8-keep', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
                )
                connection.execute("DROP TABLE knowledge_resolutions")
                connection.execute("DROP TABLE knowledge_sources")
                connection.execute("DROP TABLE knowledge_collections")
                connection.execute("PRAGMA user_version = 8")
        db.initialize()
        with db.connect() as connection:
            self.assertEqual(schema_version(connection), SCHEMA_VERSION)
            self.assertIsNotNone(
                connection.execute(
                    "SELECT task_id FROM tasks WHERE task_id = 'wb-v8-keep'"
                ).fetchone()
            )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue(
            {"knowledge_collections", "knowledge_sources", "knowledge_resolutions"}.issubset(tables)
        )

    def test_schema_nine_upgrades_to_planner_runtime_tables(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        with db.connect() as connection:
            with connection:
                connection.execute(
                    "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at) "
                    "VALUES ('wb-v9-keep', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
                )
                connection.execute("DROP TABLE planner_events")
                connection.execute("DROP TABLE planner_dependencies")
                connection.execute("DROP TABLE planner_steps")
                connection.execute("DROP TABLE planner_plans")
                connection.execute("PRAGMA user_version = 9")
        db.initialize()
        with db.connect() as connection:
            self.assertEqual(schema_version(connection), SCHEMA_VERSION)
            self.assertIsNotNone(
                connection.execute(
                    "SELECT task_id FROM tasks WHERE task_id = 'wb-v9-keep'"
                ).fetchone()
            )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue(
            {"planner_plans", "planner_steps", "planner_dependencies", "planner_events"}.issubset(tables)
        )

    def test_foreign_keys_and_wal_are_enabled(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        with db.connect() as connection:
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
            )
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_invalid_path_fails_explicitly(self) -> None:
        # A file occupying the parent path makes directory creation impossible.
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        db = Database(blocker / "runtime.db")
        with self.assertRaises(RuntimePersistenceError):
            db.initialize()

    def test_initialize_does_not_delete_existing_database(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        with db.connect() as connection:
            with connection:
                connection.execute(
                    "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at)"
                    " VALUES ('wb-keep', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
                )
        db.initialize()
        with db.connect() as connection:
            row = connection.execute(
                "SELECT task_id FROM tasks WHERE task_id = 'wb-keep'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_cascade_delete_cleans_child_rows(self) -> None:
        db = Database(self.db_path)
        db.initialize()
        with db.connect() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at)"
                " VALUES ('wb-c', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
            )
            connection.execute(
                "INSERT INTO sessions (session_id, task_id, backend, route, created_at, updated_at)"
                " VALUES ('rs-c', 'wb-c', 'workbuddy', 'gateway', 1.0, 1.0)"
            )
            connection.execute(
                "INSERT INTO events (event_id, task_id, event_type, event_time)"
                " VALUES ('ev-c', 'wb-c', 'task_created', 1.0)"
            )
            connection.execute(
                "INSERT INTO idempotency (idempotency_key, request_fingerprint, task_id, created_at)"
                " VALUES ('k-c', 'fp', 'wb-c', 1.0)"
            )
            connection.execute("DELETE FROM tasks WHERE task_id = 'wb-c'")
        with db.connect() as connection:
            remaining = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in ("sessions", "events", "idempotency")
            }
        self.assertEqual(remaining, {"sessions": 0, "events": 0, "idempotency": 0})


class UniqueConstraintTests(unittest.TestCase):
    """Database-level constraints required by PR1."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "runtime.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tool_invocation_schema_has_no_raw_content_columns(self) -> None:
        with self.db.connect() as connection:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(tool_invocations)")
            }
        self.assertFalse(
            {"cwd", "arguments_json", "input_json", "output_json", "content"}
            & columns
        )
        self.assertTrue(
            {"workspace_ref", "input_sha256", "output_sha256"}.issubset(columns)
        )

    def test_knowledge_schema_has_no_raw_query_or_content_columns(self) -> None:
        with self.db.connect() as connection:
            resolution_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(knowledge_resolutions)"
                )
            }
            collection_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(knowledge_collections)"
                )
            }
        self.assertFalse(
            {"query", "query_text", "content", "snippet", "cwd", "workspace"}
            & resolution_columns
        )
        self.assertFalse({"content", "cwd", "workspace"} & collection_columns)
        self.assertTrue(
            {"query_sha256", "output_sha256", "bytes_returned"}.issubset(
                resolution_columns
            )
        )

    def test_knowledge_source_must_match_context_entry_identity(self) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO context_manifests "
                "(context_id, root_hash, file_count, total_bytes, created_at) "
                "VALUES ('ctx-k', ?, 1, 3, 1.0)",
                ("a" * 64,),
            )
            connection.execute(
                "INSERT INTO context_entries "
                "(context_id, relpath, sha256, size_bytes) "
                "VALUES ('ctx-k', 'rules.md', ?, 3)",
                ("b" * 64,),
            )
            connection.execute(
                "INSERT INTO knowledge_collections "
                "(knowledge_id, name, context_id, root_hash, source_count, total_bytes, created_at) "
                "VALUES ('knw-k', 'K', 'ctx-k', ?, 1, 3, 1.0)",
                ("a" * 64,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO knowledge_sources "
                    "(knowledge_id, context_id, relpath, sha256, size_bytes, kind, ordinal) "
                    "VALUES ('knw-k', 'ctx-k', 'rules.md', ?, 3, 'rules', 0)",
                    ("c" * 64,),
                )

    def test_idempotency_key_is_unique(self) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at)"
                " VALUES ('wb-u1', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
            )
            connection.execute(
                "INSERT INTO idempotency (idempotency_key, request_fingerprint, task_id, created_at)"
                " VALUES ('same-key', 'fp-a', 'wb-u1', 1.0)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO idempotency (idempotency_key, request_fingerprint, task_id, created_at)"
                    " VALUES ('same-key', 'fp-b', 'wb-u1', 2.0)"
                )

    def test_events_are_append_only_by_event_id(self) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at)"
                " VALUES ('wb-u2', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
            )
            connection.execute(
                "INSERT INTO events (event_id, task_id, event_type, event_time)"
                " VALUES ('ev-u', 'wb-u2', 'task_created', 1.0)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO events (event_id, task_id, event_type, event_time)"
                    " VALUES ('ev-u', 'wb-u2', 'task_started', 2.0)"
                )


if __name__ == "__main__":
    unittest.main()
