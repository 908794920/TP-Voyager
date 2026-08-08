from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agent_runtime.persistence.home_migration import (
    RuntimeHomeMigrationError,
    migrate_legacy_runtime_home,
)
from agent_runtime.persistence.migrations import SCHEMA_VERSION, _MIGRATIONS
from agent_runtime.persistence.runtime_paths import resolve_runtime_database


class RuntimeHomeMigrationV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.source = self.root / "legacy" / "runtime" / "workbuddy_runtime.db"
        self.destination = self.root / "canonical" / "runtime" / "agent_runtime.db"
        self.source.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_v10_source(self, *, corrupt_blob: bool = False) -> bytes:
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            for version in sorted(_MIGRATIONS):
                if version > 10:
                    break
                with connection:
                    for statement in _MIGRATIONS[version]:
                        try:
                            connection.execute(statement)
                        except sqlite3.OperationalError as exc:
                            if "duplicate column" not in str(exc).lower():
                                raise
                    connection.execute(f"PRAGMA user_version = {version}")

            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, task_type, status, route, created_at, updated_at,
                    result_available, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("wb-migrate", "workbuddy", "completed", "gateway", 1.0, 2.0, 0, 1),
            )
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, task_id, backend, route, created_at, updated_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("ses-migrate", "wb-migrate", "workbuddy", "gateway", 1.0, 2.0, "{}"),
            )
            connection.execute(
                """
                INSERT INTO attempts (
                    attempt_id, task_id, attempt_no, backend, route, status,
                    created_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("att-migrate", "wb-migrate", 1, "workbuddy", "gateway", "completed", 1.0, 1.1, 2.0),
            )

            content = b"V2 migration artifact\n"
            digest = hashlib.sha256(content).hexdigest()
            storage_key = f"sha256/{digest[:2]}/{digest}"
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
                    "art-migrate", "wb-migrate", "att-migrate", "runtime", "file", "result.txt",
                    "result.txt", storage_key, "captured", digest, len(content), 1.2, 1.3, 1.2, 1.3, "{}",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        blob = self.source.parent / "artifacts" / storage_key
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(b"corrupted\n" if corrupt_blob else content)
        return content

    def test_migration_preserves_source_and_publishes_verified_v11_pair(self) -> None:
        content = self._create_v10_source()

        report = migrate_legacy_runtime_home(
            source_database=self.source,
            destination_database=self.destination,
        )

        self.assertTrue(self.source.is_file())
        self.assertTrue(self.destination.is_file())
        self.assertTrue(Path(report.backup_database).is_file())
        self.assertEqual(report.source_schema_version, 10)
        self.assertEqual(report.destination_schema_version, SCHEMA_VERSION)
        self.assertEqual(report.quick_check, "ok")
        self.assertEqual(report.foreign_key_violation_count, 0)
        self.assertEqual(report.artifact_reference_count, 1)
        self.assertEqual(report.artifact_verified_count, 1)
        self.assertEqual(report.artifact_issue_count, 0)
        self.assertTrue(report.source_preserved)

        connection = sqlite3.connect(self.destination)
        try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM tasks WHERE task_id='wb-migrate'").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM plan_executions").fetchone()[0],
                    0,
                )
                row = connection.execute(
                    "SELECT storage_key FROM artifacts WHERE artifact_id='art-migrate'"
                ).fetchone()
    
        finally:
            connection.close()

        migrated_blob = self.destination.parent / "artifacts" / row[0]
        self.assertEqual(migrated_blob.read_bytes(), content)
        marker = json.loads(Path(report.marker_file).read_text(encoding="utf-8"))
        self.assertEqual(marker["schema"], "agent-runtime.home_migration/v2")
        self.assertTrue(marker["source_preserved"])
        self.assertEqual(marker["artifact_verified_count"], 1)

    def test_corrupt_artifact_aborts_without_publishing_destination(self) -> None:
        self._create_v10_source(corrupt_blob=True)

        with self.assertRaisesRegex(RuntimeHomeMigrationError, "Artifact store failed integrity audit"):
            migrate_legacy_runtime_home(
                source_database=self.source,
                destination_database=self.destination,
            )

        self.assertTrue(self.source.is_file())
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.destination.parent / "artifacts").exists())
        self.assertTrue(any((self.source.parent / "migration-backups").glob("*.db")))

    def test_existing_destination_is_never_overwritten(self) -> None:
        self._create_v10_source()
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"do-not-touch")

        with self.assertRaisesRegex(RuntimeHomeMigrationError, "already exists"):
            migrate_legacy_runtime_home(
                source_database=self.source,
                destination_database=self.destination,
            )

        self.assertEqual(self.destination.read_bytes(), b"do-not-touch")
        self.assertTrue(self.source.is_file())

    def test_publish_failure_rolls_back_partial_canonical_state(self) -> None:
        self._create_v10_source()
        real_replace = os.replace

        def fail_database_publish(src, dst):
            if Path(dst).resolve() == self.destination.resolve():
                raise OSError("injected database publish failure")
            return real_replace(src, dst)

        with patch(
            "agent_runtime.persistence.home_migration.os.replace",
            side_effect=fail_database_publish,
        ):
            with self.assertRaisesRegex(OSError, "injected database publish failure"):
                migrate_legacy_runtime_home(
                    source_database=self.source,
                    destination_database=self.destination,
                )

        self.assertTrue(self.source.is_file())
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.destination.parent / "artifacts").exists())
        self.assertFalse((self.destination.parent / "migration-v2.json").exists())
        self.assertTrue(any((self.source.parent / "migration-backups").glob("*.db")))


class RuntimePathResolutionV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.home = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @contextmanager
    def _clean_runtime_env(self, **values: str):
        keys = (
            "AGENT_RUNTIME_HOME",
            "AGENT_RUNTIME_DB",
            "WORKBUDDY_RUNTIME_DB",
            "WORKBUDDY_CONFIG_DIR",
            "CODEBUDDY_CONFIG_DIR",
        )
        env = {key: None for key in keys}
        env.update(values)
        with patch.dict(os.environ, {key: value for key, value in env.items() if value is not None}, clear=False):
            removed: dict[str, str] = {}
            for key, value in env.items():
                if value is None and key in os.environ:
                    removed[key] = os.environ.pop(key)
            try:
                yield
            finally:
                os.environ.update(removed)

    def test_canonical_existing_wins_after_migration_even_if_legacy_remains(self) -> None:
        canonical_home = self.home / ".agent-runtime"
        legacy_home = self.home / ".workbuddy"
        canonical = canonical_home / "runtime" / "agent_runtime.db"
        legacy = legacy_home / "runtime" / "workbuddy_runtime.db"
        canonical.parent.mkdir(parents=True)
        legacy.parent.mkdir(parents=True)
        canonical.touch()
        legacy.touch()
        with self._clean_runtime_env(
            AGENT_RUNTIME_HOME=str(canonical_home),
            WORKBUDDY_CONFIG_DIR=str(legacy_home),
        ):
            resolution = resolve_runtime_database()
        self.assertEqual(resolution.database, canonical.resolve())
        self.assertEqual(resolution.source, "AGENT_RUNTIME_HOME")
        self.assertFalse(resolution.legacy_compat_active)

    def test_legacy_existing_is_compatible_before_migration(self) -> None:
        legacy_home = self.home / ".workbuddy"
        legacy = legacy_home / "runtime" / "workbuddy_runtime.db"
        legacy.parent.mkdir(parents=True)
        legacy.touch()
        fake_home = self.home / "user-home"
        with patch("pathlib.Path.home", return_value=fake_home):
            with self._clean_runtime_env(WORKBUDDY_CONFIG_DIR=str(legacy_home)):
                resolution = resolve_runtime_database()
        self.assertEqual(resolution.database, legacy.resolve())
        self.assertEqual(resolution.source, "legacy_existing")
        self.assertTrue(resolution.legacy_compat_active)

    def test_explicit_new_database_has_highest_priority(self) -> None:
        new_db = self.home / "explicit" / "runtime.db"
        legacy_db = self.home / "legacy-explicit.db"
        with self._clean_runtime_env(
            AGENT_RUNTIME_DB=str(new_db),
            WORKBUDDY_RUNTIME_DB=str(legacy_db),
        ):
            resolution = resolve_runtime_database()
        self.assertEqual(resolution.database, new_db.resolve())
        self.assertEqual(resolution.source, "AGENT_RUNTIME_DB")
        self.assertFalse(resolution.legacy_compat_active)


if __name__ == "__main__":
    unittest.main()
