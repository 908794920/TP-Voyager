from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from agent_runtime import cli
from agent_runtime.runtime.diagnostics import (
    RuntimeDiagnosticsError,
    RuntimeInspector,
    render_task_markdown,
)
from agent_runtime.persistence.database import Database


class RuntimeDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "runtime.db"
        Database(self.db_path).initialize()
        self._seed()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self) -> None:
        content = b"demo artifact\n"
        digest = hashlib.sha256(content).hexdigest()
        blob = self.db_path.parent / "artifacts" / "sha256" / digest[:2] / digest
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
        result = {
            "schema": "workbuddy.result/v1",
            "attempt_id": "att-1",
            "answer": "private final answer",
            "backend": "workbuddy",
            "stopReason": "end_turn",
            "title": "diagnostic task",
            "reasoning_effort_requested": None,
            "reasoning_effort_applied": None,
            "observability": {},
            "output": {"private": "not in safe report"},
            "changed_files": ["src/demo.py"],
            "tests": [{"command": "secret test command", "exit_code": 0, "details": {"stdout": "secret stdout", "duration_ms": 12}}],
            "artifacts": [{"name": "src/demo.py"}],
            "risks": [],
            "claims": ["done"],
            "verification": {"status": "PASSED"},
            "usage": {"input_tokens": 12},
        }
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, task_type, status, route, created_at, updated_at,
                    started_at, finished_at, session_id, current_attempt_id,
                    result_available, result_json, version, terminal_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "wb-1", "workbuddy", "completed", "gateway", 1.0, 3.0,
                    1.5, 3.0, "ses-1", "att-1", 1,
                    json.dumps(result), 3, "end_turn",
                ),
            )
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, task_id, backend, route, backend_session_id,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "ses-1", "wb-1", "workbuddy", "gateway",
                    "private-backend-session", 1.0, 3.0,
                    json.dumps({"cwd": "C:/private/workspace"}),
                ),
            )
            connection.execute(
                """
                INSERT INTO attempts (
                    attempt_id, task_id, attempt_no, backend, route, status,
                    created_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("att-1", "wb-1", 1, "workbuddy", "gateway", "completed", 1.0, 1.5, 3.0),
            )
            connection.execute(
                """
                INSERT INTO task_lineage (
                    child_task_id, parent_task_id, root_task_id, context_id,
                    agent_profile, execution_mode, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("wb-1", None, "wb-1", "ctx-1", "developer", "background", 1.0),
            )
            connection.execute(
                """
                INSERT INTO events (
                    event_id, task_id, attempt_id, event_type, event_time,
                    payload_json, visibility
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("evt-public", "wb-1", "att-1", "task_completed", 3.0, '{"safe": true}', "public"),
            )
            connection.execute(
                """
                INSERT INTO events (
                    event_id, task_id, attempt_id, event_type, event_time,
                    payload_json, visibility
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("evt-private", "wb-1", "att-1", "backend_private", 2.0, '{"secret": true}', "private"),
            )
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, task_id, attempt_id, origin, kind, name,
                    workspace_relpath, storage_key, capture_state, sha256,
                    size_bytes, declared_at, captured_at, created_at, updated_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "art-1", "wb-1", "att-1", "runtime", "file", "demo.py",
                    "src/demo.py", f"sha256/{digest[:2]}/{digest}", "captured", digest,
                    len(content), 2.0, 2.5, 2.0, 2.5, '{}',
                ),
            )
            connection.execute(
                """
                INSERT INTO evidences (
                    evidence_id, task_id, attempt_id, artifact_id,
                    evidence_type, trust_state, origin, summary, detail_json,
                    captured_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evd-1", "wb-1", "att-1", "art-1", "artifact",
                    "observed", "runtime", "Artifact captured", '{"path":"private"}',
                    2.5, 2.5,
                ),
            )
            connection.commit()

    def test_overview_and_list_are_safe(self) -> None:
        inspector = RuntimeInspector(self.db_path)
        overview = inspector.overview().to_dict()
        self.assertEqual(overview["task_count"], 1)
        self.assertEqual(overview["tool_invocation_count"], 0)
        self.assertEqual(overview["knowledge_collection_count"], 0)
        self.assertEqual(overview["knowledge_resolution_count"], 0)
        self.assertEqual(overview["status_counts"], {"completed": 1})
        tasks = inspector.list_tasks(runtime="workbuddy", status="completed")
        self.assertEqual(tasks[0]["task_id"], "wb-1")
        encoded = json.dumps(tasks)
        self.assertNotIn("private-backend-session", encoded)
        self.assertNotIn("C:/private", encoded)


    def test_doctor_can_report_schema_seven_before_server_migration(self) -> None:
        legacy = self.root / "legacy-v7.db"
        Database(legacy).initialize()
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute("DROP TABLE tool_invocations")
            connection.execute("PRAGMA user_version = 7")
            connection.commit()
        overview = RuntimeInspector(legacy).overview().to_dict()
        self.assertEqual(overview["schema_version"], 7)
        self.assertFalse(overview["schema_supported"])
        self.assertEqual(overview["tool_invocation_count"], 0)
        self.assertEqual(overview["knowledge_collection_count"], 0)
        self.assertEqual(overview["knowledge_resolution_count"], 0)
        self.assertTrue(overview["integrity_ok"])

    def test_snapshot_omits_private_material_by_default(self) -> None:
        snapshot = RuntimeInspector(self.db_path).task_snapshot("wb-1")
        encoded = json.dumps(snapshot)
        self.assertNotIn("private final answer", encoded)
        self.assertNotIn("secret test command", encoded)
        self.assertNotIn("secret stdout", encoded)
        self.assertNotIn("private-backend-session", encoded)
        self.assertNotIn("sha256/private/blob", encoded)
        self.assertNotIn("backend_private", encoded)
        self.assertEqual(snapshot["result"]["verification"]["status"], "PASSED")
        self.assertEqual(snapshot["artifacts"][0]["sha256"], hashlib.sha256(b"demo artifact\n").hexdigest())

    def test_assessment_is_content_free_and_accepts_completed_verified_task(self) -> None:
        assessment = RuntimeInspector(self.db_path).task_assessment("wb-1")
        self.assertEqual(assessment["execution"]["status"], "completed")
        self.assertEqual(assessment["work_product"]["status"], "verified")
        self.assertEqual(assessment["recommended_action"], "accept")
        encoded = json.dumps(assessment)
        self.assertNotIn("private final answer", encoded)
        self.assertNotIn("src/demo.py", encoded)
        self.assertNotIn("private-backend-session", encoded)

    def test_artifact_store_audit_reports_integrity_and_orphans(self) -> None:
        inspector = RuntimeInspector(self.db_path)
        audit = inspector.audit_artifact_store().to_dict()
        self.assertTrue(audit["integrity_ok"])
        self.assertEqual(audit["valid_reference_count"], 1)
        self.assertEqual(audit["orphan_blob_count"], 0)

        orphan_digest = "f" * 64
        orphan = self.db_path.parent / "artifacts" / "sha256" / "ff" / orphan_digest
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"orphan")
        audit = inspector.audit_artifact_store().to_dict()
        self.assertTrue(audit["integrity_ok"])
        self.assertEqual(audit["orphan_blob_count"], 1)

    def test_artifact_store_audit_detects_missing_blob(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE artifacts SET storage_key = ?, sha256 = ? WHERE artifact_id = ?",
                ("sha256/aa/" + "a" * 64, "a" * 64, "art-1"),
            )
            connection.commit()
        audit = RuntimeInspector(self.db_path).audit_artifact_store().to_dict()
        self.assertFalse(audit["integrity_ok"])
        self.assertEqual(audit["missing_blob_count"], 1)

    def test_explicit_result_and_markdown_export(self) -> None:
        snapshot = RuntimeInspector(self.db_path).task_snapshot(
            "wb-1", include_result=True
        )
        self.assertEqual(snapshot["result"]["answer"], "private final answer")
        self.assertNotIn("command", snapshot["result"]["tests"][0])
        self.assertNotIn("stdout", snapshot["result"]["tests"][0]["details"])
        self.assertEqual(snapshot["result"]["tests"][0]["details"]["duration_ms"], 12)
        report = render_task_markdown(snapshot)
        self.assertIn("private final answer", report)
        self.assertIn("Artifact captured", report)
        self.assertNotIn("private-backend-session", report)
        self.assertNotIn("sha256/private/blob", report)

    def test_missing_database_fails_without_creating_it(self) -> None:
        missing = self.root / "missing.db"
        with self.assertRaises(RuntimeDiagnosticsError):
            RuntimeInspector(missing).overview()
        self.assertFalse(missing.exists())

    def test_cli_doctor_show_and_export(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(["--db", str(self.db_path), "doctor"])
        self.assertEqual(code, 0)
        doctor = json.loads(stdout.getvalue())
        self.assertEqual(doctor["task_count"], 1)
        self.assertTrue(doctor["integrity_ok"])
        self.assertEqual(doctor["version"], "1.0.9.3")
        self.assertIn("codebuddy", doctor["model_catalog"])
        self.assertIn("qoder", doctor["model_catalog"])
        self.assertFalse(doctor["model_catalog"]["selection_performed"])
        self.assertFalse(doctor["model_catalog"]["pricing_estimated"])
        self.assertFalse(doctor["safety"]["model_invocation_performed"])
        self.assertFalse(doctor["safety"]["credentials_returned"])
        self.assertFalse(doctor["safety"]["task_content_returned"])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(["--db", str(self.db_path), "artifact-audit"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout.getvalue())["integrity_ok"])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(["--db", str(self.db_path), "show", "wb-1"])
        self.assertEqual(code, 0)
        self.assertNotIn("private final answer", stdout.getvalue())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(["--db", str(self.db_path), "assess", "wb-1"])
        self.assertEqual(code, 0)
        assessment = json.loads(stdout.getvalue())
        self.assertEqual(assessment["recommended_action"], "accept")
        self.assertNotIn("private final answer", stdout.getvalue())

        output = self.root / "task-report.md"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(
                [
                    "--db", str(self.db_path), "export", "wb-1",
                    "--output", str(output), "--include-result",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(output.is_file())
        self.assertIn("private final answer", output.read_text(encoding="utf-8"))

    def test_cli_error_is_nonzero(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = cli.main(["--db", str(self.root / "none.db"), "doctor"])
        self.assertEqual(code, 1)
        self.assertIn("does not exist", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
