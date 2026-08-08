from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_runtime import cli, server
from agent_runtime.persistence.database import Database
from agent_runtime.application.tool_service import (
    ToolPolicyError,
    ToolRuntimeService,
)


class ToolRuntimeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()
        self.service = ToolRuntimeService(self.db)
        (self.project / "hello.txt").write_bytes(b"hello runtime\n")
        (self.project / "sub").mkdir()
        (self.project / "sub" / "item.txt").write_text("item\n", encoding="utf-8")

    def tearDown(self) -> None:
        server.configure_runtime_database(None)
        self.tmp.cleanup()

    def test_catalog_is_fixed_read_only_and_does_not_dispatch(self) -> None:
        result = self.service.catalog()
        self.assertTrue(result["ok"])
        self.assertEqual(result["schema"], "workbuddy.tool_catalog/v1")
        self.assertFalse(result["selection_performed"])
        self.assertFalse(result["dispatch_performed"])
        self.assertFalse(result["writes_supported"])
        names = {item["name"] for item in result["tools"]}
        self.assertEqual(
            names,
            {
                "filesystem.list",
                "filesystem.stat",
                "filesystem.sha256",
                "filesystem.read_text",
                "git.status",
                "git.diff",
                "sqlite.query",
            },
        )
        self.assertTrue(all(item["mutability"] == "read_only" for item in result["tools"]))

    def test_filesystem_tools_return_bounded_relative_results(self) -> None:
        listed = self.service.invoke(
            "filesystem.list", str(self.project), arguments={"max_entries": 10}
        )
        self.assertTrue(listed["ok"])
        self.assertEqual(
            {item["path"] for item in listed["result"]["entries"]},
            {"hello.txt", "sub"},
        )
        stat = self.service.invoke(
            "filesystem.stat", str(self.project), arguments={"path": "hello.txt"}
        )
        self.assertTrue(stat["ok"])
        self.assertNotIn("sha256", stat["result"])
        hashed = self.service.invoke(
            "filesystem.sha256",
            str(self.project),
            arguments={"path": "hello.txt", "max_bytes": 100},
        )
        self.assertEqual(
            hashed["result"]["sha256"],
            hashlib.sha256(b"hello runtime\n").hexdigest(),
        )
        read = self.service.invoke(
            "filesystem.read_text",
            str(self.project),
            arguments={"path": "hello.txt", "max_bytes": 100},
        )
        self.assertTrue(read["ok"])
        self.assertEqual(read["result"]["content"], "hello runtime\n")
        self.assertFalse(read["audit"]["raw_output_stored"])
        self.assertNotIn(str(self.project), json.dumps(read))

    def test_unknown_tool_and_hash_limit_are_rejected_and_audited(self) -> None:
        unknown = self.service.invoke("shell.exec", str(self.project), arguments={})
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["error"]["code"], "tool_not_found")
        self.assertEqual(unknown["tool"]["name"], "unknown")
        large = self.project / "large.txt"
        large.write_bytes(b"x" * 32)
        limited = self.service.invoke(
            "filesystem.sha256",
            str(self.project),
            arguments={"path": "large.txt", "max_bytes": 8},
        )
        self.assertFalse(limited["ok"])
        self.assertEqual(limited["status"], "rejected")
        self.assertEqual(len(self.service.history(status="rejected")["invocations"]), 2)


    def test_history_limit_and_git_boolean_contract_are_strict(self) -> None:
        with self.assertRaises(ToolPolicyError):
            self.service.history(limit="not-an-integer")
        result = self.service.invoke(
            "git.diff",
            str(self.project),
            arguments={"cached": "false"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "policy_rejected")

    def test_unexpected_tool_failure_is_content_free_and_audited(self) -> None:
        def broken(_root: Path, _arguments: dict[str, object]) -> dict[str, object]:
            raise ValueError("private implementation detail")

        self.service._handlers["filesystem.stat"] = broken  # test seam
        result = self.service.invoke(
            "filesystem.stat",
            str(self.project),
            arguments={"path": "hello.txt"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "tool_internal_error")
        self.assertNotIn("private implementation detail", json.dumps(result))
        history = self.service.history(limit=1)["invocations"][0]
        self.assertEqual(history["status"], "failed")
        self.assertEqual(history["error_code"], "tool_internal_error")



    def test_invalid_tool_name_is_not_persisted_verbatim(self) -> None:
        private_name = "secret tool name / " + "x" * 200
        result = self.service.invoke(private_name, str(self.project), arguments={})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["tool"]["name"], "unknown")
        history = self.service.history(limit=1)["invocations"][0]
        self.assertEqual(history["tool_name"], "unknown")
        self.assertNotIn(private_name, json.dumps(history))

    def test_json_input_shapes_fail_closed_and_are_audited(self) -> None:
        invalid_arguments = self.service.invoke(
            "filesystem.stat",
            str(self.project),
            arguments=[],  # type: ignore[arg-type]
        )
        self.assertFalse(invalid_arguments["ok"])
        self.assertEqual(invalid_arguments["status"], "rejected")
        invalid_path = self.service.invoke(
            "filesystem.stat",
            str(self.project),
            arguments={"path": ["hello.txt"]},
        )
        self.assertFalse(invalid_path["ok"])
        invalid_count = self.service.invoke(
            "filesystem.list",
            str(self.project),
            arguments={"max_entries": 1.5},
        )
        self.assertFalse(invalid_count["ok"])
        invalid_query = self.service.invoke(
            "sqlite.query",
            str(self.project),
            arguments={"database": "missing.db", "query": ["SELECT 1"]},
        )
        self.assertFalse(invalid_query["ok"])
        self.assertEqual(
            len(self.service.history(status="rejected", limit=10)["invocations"]),
            4,
        )

    def test_path_escape_and_binary_reads_are_rejected_and_audited(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        escape = self.service.invoke(
            "filesystem.read_text",
            str(self.project),
            arguments={"path": "../outside.txt"},
        )
        self.assertFalse(escape["ok"])
        self.assertEqual(escape["status"], "rejected")
        binary = self.project / "binary.bin"
        binary.write_bytes(b"a\x00b")
        binary_result = self.service.invoke(
            "filesystem.read_text",
            str(self.project),
            arguments={"path": "binary.bin"},
        )
        self.assertFalse(binary_result["ok"])
        history = self.service.history(status="rejected", limit=10)
        self.assertEqual(len(history["invocations"]), 2)
        encoded = json.dumps(history)
        self.assertNotIn("outside.txt", encoded)
        self.assertNotIn("binary.bin", encoded)
        self.assertNotIn(str(self.project), encoded)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_external_symlink_is_never_followed(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link = self.project / "external.txt"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        result = self.service.invoke(
            "filesystem.read_text",
            str(self.project),
            arguments={"path": "external.txt"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rejected")

    @unittest.skipUnless(shutil.which("git"), "git unavailable")
    def test_git_status_and_diff_are_read_only(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.project,
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.project, check=True)
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.project, check=True)
        (self.project / "hello.txt").write_text("changed\n", encoding="utf-8")
        status = self.service.invoke("git.status", str(self.project), arguments={})
        self.assertTrue(status["ok"])
        self.assertEqual(status["result"]["entries"][0]["path"], "hello.txt")
        diff = self.service.invoke(
            "git.diff", str(self.project), arguments={"path": "hello.txt"}
        )
        self.assertTrue(diff["ok"])
        self.assertIn("-hello runtime", diff["result"]["content"])
        self.assertIn("+changed", diff["result"]["content"])
        self.assertEqual(
            (self.project / "hello.txt").read_text(encoding="utf-8"), "changed\n"
        )


    @unittest.skipIf(os.name == "nt", "helper script assertion is POSIX-specific")
    @unittest.skipUnless(shutil.which("git"), "git unavailable")
    def test_git_diff_does_not_execute_external_diff_or_textconv(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.project,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.project,
            check=True,
        )
        marker = self.project / "external-helper-ran"
        helper = self.project / "helper.sh"
        helper.write_text(
            '#!/bin/sh\nprintf ran > external-helper-ran\ncat "$1"\n',
            encoding="utf-8",
        )
        helper.chmod(0o755)
        (self.project / ".gitattributes").write_text(
            "hello.txt diff=unsafe\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "config", "diff.unsafe.textconv", str(helper)],
            cwd=self.project,
            check=True,
        )
        subprocess.run(
            ["git", "config", "diff.external", str(helper)],
            cwd=self.project,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "baseline"], cwd=self.project, check=True
        )
        subprocess.run(
            ["git", "config", "core.fsmonitor", str(helper)],
            cwd=self.project,
            check=True,
        )
        marker.unlink(missing_ok=True)
        (self.project / "hello.txt").write_text("changed\n", encoding="utf-8")
        status = self.service.invoke("git.status", str(self.project), arguments={})
        self.assertTrue(status["ok"])
        self.assertFalse(marker.exists())
        result = self.service.invoke(
            "git.diff", str(self.project), arguments={"path": "hello.txt"}
        )
        self.assertTrue(result["ok"])
        self.assertFalse(marker.exists())
        self.assertIn("+changed", result["result"]["content"])

    def test_sqlite_query_uses_read_only_connection_and_bounded_rows(self) -> None:
        app_db = self.project / "app.db"
        with closing(sqlite3.connect(app_db)) as connection:
            connection.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT)")
            connection.executemany(
                "INSERT INTO demo(name) VALUES (?)", [("alpha",), ("beta",)]
            )
            connection.commit()
        query = self.service.invoke(
            "sqlite.query",
            str(self.project),
            arguments={
                "database": "app.db",
                "query": "SELECT id, name FROM demo ORDER BY id",
                "max_rows": 10,
            },
        )
        self.assertTrue(query["ok"])
        self.assertEqual(query["result"]["rows"], [[1, "alpha"], [2, "beta"]])
        rejected = self.service.invoke(
            "sqlite.query",
            str(self.project),
            arguments={
                "database": "app.db",
                "query": "DELETE FROM demo",
            },
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["status"], "rejected")
        with closing(sqlite3.connect(app_db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM demo").fetchone()[0], 2)


    def test_sqlite_absolute_path_pragmas_and_large_values_are_blocked(self) -> None:
        app_db = self.project / "guarded.db"
        with closing(sqlite3.connect(app_db)) as connection:
            connection.execute("CREATE TABLE demo (value BLOB)")
            connection.execute("INSERT INTO demo VALUES (zeroblob(16))")
            connection.commit()
        direct = self.service.invoke(
            "sqlite.query",
            str(self.project),
            arguments={"database": "guarded.db", "query": "PRAGMA database_list"},
        )
        self.assertFalse(direct["ok"])
        self.assertEqual(direct["status"], "rejected")
        table_valued = self.service.invoke(
            "sqlite.query",
            str(self.project),
            arguments={
                "database": "guarded.db",
                "query": "SELECT * FROM pragma_database_list",
            },
        )
        self.assertFalse(table_valued["ok"])
        self.assertNotIn(str(app_db), json.dumps(table_valued))
        oversized = self.service.invoke(
            "sqlite.query",
            str(self.project),
            arguments={
                "database": "guarded.db",
                "query": "SELECT zeroblob(2000000)",
                "max_result_bytes": 1024,
            },
        )
        self.assertFalse(oversized["ok"])
        self.assertIn(oversized["status"], {"failed", "rejected"})

    def test_audit_persists_hashes_not_content_paths_or_query(self) -> None:
        result = self.service.invoke(
            "filesystem.read_text",
            str(self.project),
            arguments={"path": "hello.txt"},
        )
        self.assertTrue(result["ok"])
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_invocations WHERE invocation_id = ?",
                (result["invocation_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        encoded = json.dumps(dict(row))
        self.assertNotIn("hello runtime", encoded)
        self.assertNotIn("hello.txt", encoded)
        self.assertNotIn(str(self.project), encoded)
        self.assertEqual(len(row["workspace_ref"]), 64)
        self.assertEqual(len(row["input_sha256"]), 64)
        self.assertEqual(len(row["output_sha256"]), 64)

    def test_valid_task_and_context_links_are_audited_and_set_null_on_delete(self) -> None:
        with self.db.connect() as connection:
            with connection:
                connection.execute(
                    "INSERT INTO tasks (task_id, task_type, status, route, created_at, updated_at) "
                    "VALUES ('wb-tool-link', 'workbuddy', 'queued', 'gateway', 1.0, 1.0)"
                )
                connection.execute(
                    "INSERT INTO context_manifests "
                    "(context_id, root_hash, file_count, total_bytes, created_at) "
                    "VALUES ('ctx-tool-link', ?, 1, 1, 1.0)",
                    ("a" * 64,),
                )
                connection.execute(
                    "INSERT INTO context_entries (context_id, relpath, sha256, size_bytes) "
                    "VALUES ('ctx-tool-link', 'hello.txt', ?, 1)",
                    ("b" * 64,),
                )
        with self.db.connect() as connection:
            before = connection.execute(
                "SELECT status, version FROM tasks WHERE task_id='wb-tool-link'"
            ).fetchone()
        invoked = self.service.invoke(
            "filesystem.stat",
            str(self.project),
            arguments={"path": "hello.txt"},
            task_id="wb-tool-link",
            context_id="ctx-tool-link",
        )
        self.assertTrue(invoked["ok"])
        with self.db.connect() as connection:
            after = connection.execute(
                "SELECT status, version FROM tasks WHERE task_id='wb-tool-link'"
            ).fetchone()
        self.assertEqual(tuple(before), tuple(after))
        history = self.service.history(task_id="wb-tool-link", limit=10)
        self.assertEqual(history["invocations"][0]["context_id"], "ctx-tool-link")
        with self.db.connect() as connection:
            with connection:
                connection.execute("DELETE FROM context_manifests WHERE context_id='ctx-tool-link'")
                connection.execute("DELETE FROM tasks WHERE task_id='wb-tool-link'")
            row = connection.execute(
                "SELECT task_id, context_id FROM tool_invocations WHERE invocation_id=?",
                (invoked["invocation_id"],),
            ).fetchone()
        self.assertIsNone(row["task_id"])
        self.assertIsNone(row["context_id"])

    def test_sqlite_pragma_assignment_is_rejected(self) -> None:
        app_db = self.project / "pragma.db"
        with closing(sqlite3.connect(app_db)) as connection:
            connection.execute("CREATE TABLE demo (id INTEGER)")
            connection.commit()
        result = self.service.invoke(
            "sqlite.query",
            str(self.project),
            arguments={"database": "pragma.db", "query": "PRAGMA query_only=OFF"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rejected")

    def test_sqlite_with_write_is_denied_by_authorizer(self) -> None:
        app_db = self.project / "write-guard.db"
        with closing(sqlite3.connect(app_db)) as connection:
            connection.execute("CREATE TABLE demo (id INTEGER)")
            connection.execute("INSERT INTO demo VALUES (1)")
            connection.commit()
        result = self.service.invoke(
            "sqlite.query",
            str(self.project),
            arguments={
                "database": "write-guard.db",
                "query": "WITH selected AS (SELECT 1) DELETE FROM demo RETURNING id",
            },
        )
        self.assertFalse(result["ok"])
        self.assertIn(result["status"], {"failed", "rejected"})
        with closing(sqlite3.connect(app_db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM demo").fetchone()[0], 1)

    def test_task_and_context_links_require_existing_durable_records(self) -> None:
        invalid = self.service.invoke(
            "filesystem.stat",
            str(self.project),
            arguments={"path": "hello.txt"},
            task_id="wb-missing",
        )
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error"]["code"], "invalid_reference")
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT task_id FROM tool_invocations WHERE invocation_id = ?",
                (invalid["invocation_id"],),
            ).fetchone()
        self.assertIsNone(row["task_id"])


class ToolRuntimeServerAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "readme.txt").write_text("demo", encoding="utf-8")
        self.db_path = self.root / "runtime.db"
        server.configure_runtime_database(self.db_path)

    def tearDown(self) -> None:
        server.configure_runtime_database(None)
        self.tmp.cleanup()

    def test_server_tools_do_not_dispatch_backend(self) -> None:
        catalog = server.runtime_tool_catalog()
        self.assertTrue(catalog["ok"])
        invoked = server.runtime_tool_invoke(
            "filesystem.stat",
            str(self.project),
            {"path": "readme.txt"},
        )
        self.assertTrue(invoked["ok"])
        history = server.runtime_tool_history(limit=5)
        self.assertEqual(history["invocations"][0]["invocation_id"], invoked["invocation_id"])
        item = server.runtime_tool_invocation(invoked["invocation_id"])
        self.assertTrue(item["ok"])
        self.assertFalse(item["invocation"]["metadata"]["automatic_dispatch"])

    def test_cli_tool_history_is_content_free(self) -> None:
        server.runtime_tool_invoke(
            "filesystem.read_text",
            str(self.project),
            {"path": "readme.txt"},
        )
        import io
        from contextlib import redirect_stdout

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main([
                "--db", str(self.db_path), "tool-history", "--limit", "10"
            ])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("filesystem.read_text", output)
        self.assertNotIn("demo", output)
        self.assertNotIn(str(self.project), output)


if __name__ == "__main__":
    unittest.main()
