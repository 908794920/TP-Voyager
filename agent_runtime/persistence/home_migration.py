"""Explicit V1 WorkBuddy-home -> V2 Agent Runtime home migration.

The migration is intentionally operator-invoked.  It never deletes the source
V1 database or Artifact CAS.  SQLite is copied with the backup API into a consistent pre-migration snapshot;
that same snapshot is retained as the rollback backup.  The copied database is
then migrated to the current schema, checked, paired with a copied Artifact CAS,
and only then published at the canonical destination.  The operator must stop
the Runtime before invoking this command so the database snapshot and Artifact
CAS represent the same quiescent state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime.persistence.database import Database
from agent_runtime.persistence.runtime_paths import (
    canonical_runtime_database_path,
    legacy_runtime_database_path,
)


class RuntimeHomeMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeHomeMigrationReport:
    source_database: str
    destination_database: str
    backup_database: str
    source_schema_version: int
    destination_schema_version: int
    quick_check: str
    foreign_key_violation_count: int
    artifact_reference_count: int
    artifact_verified_count: int
    artifact_issue_count: int
    source_preserved: bool
    marker_file: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


_COMMON_TABLES = (
    "tasks",
    "sessions",
    "attempts",
    "events",
    "idempotency",
    "evidences",
    "artifacts",
    "task_lineage",
    "workflows",
    "workflow_stages",
    "workflow_approvals",
    "workflow_events",
    "context_manifests",
    "context_entries",
    "tool_invocations",
    "knowledge_collections",
    "knowledge_sources",
    "knowledge_resolutions",
    "planner_plans",
    "planner_steps",
    "planner_dependencies",
    "planner_events",
)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = _table_names(connection)
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in _COMMON_TABLES
        if table in names
    }


def _verify_artifact_store(
    database_path: Path, artifact_root: Path
) -> tuple[int, int, list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    referenced = verified = 0
    connection = sqlite3.connect(database_path)
    try:
        connection.row_factory = sqlite3.Row
        names = _table_names(connection)
        if "artifacts" not in names:
            return 0, 0, issues
        rows = connection.execute(
            "SELECT artifact_id, storage_key, sha256, size_bytes "
            "FROM artifacts WHERE capture_state = 'captured' AND storage_key IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()
    root = artifact_root.resolve()
    for row in rows:
        referenced += 1
        artifact_id = str(row["artifact_id"])
        storage_key = str(row["storage_key"] or "")
        digest = str(row["sha256"] or "")
        parts = storage_key.replace("\\", "/").split("/")
        if (
            len(parts) != 3
            or parts[0] != "sha256"
            or len(parts[1]) != 2
            or len(parts[2]) != 64
            or parts[2] != digest
            or parts[1] != digest[:2]
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            issues.append({"artifact_id": artifact_id, "issue": "unsafe_storage_key"})
            continue
        candidate = (root / storage_key).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            issues.append({"artifact_id": artifact_id, "issue": "unsafe_storage_key"})
            continue
        if not candidate.is_file():
            issues.append({"artifact_id": artifact_id, "issue": "missing_blob"})
            continue
        data = candidate.read_bytes()
        if row["size_bytes"] is not None and len(data) != int(row["size_bytes"]):
            issues.append({"artifact_id": artifact_id, "issue": "size_mismatch"})
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            issues.append({"artifact_id": artifact_id, "issue": "hash_mismatch"})
            continue
        verified += 1
    return referenced, verified, issues


def migrate_legacy_runtime_home(
    *,
    source_database: str | Path | None = None,
    destination_database: str | Path | None = None,
) -> RuntimeHomeMigrationReport:
    source = Path(source_database or legacy_runtime_database_path()).expanduser().resolve()
    destination = Path(
        destination_database or canonical_runtime_database_path()
    ).expanduser().resolve()
    if not source.is_file():
        raise RuntimeHomeMigrationError(f"legacy Runtime database does not exist: {source}")
    if source == destination:
        raise RuntimeHomeMigrationError("source and destination databases are the same")
    if destination.exists():
        raise RuntimeHomeMigrationError(
            "canonical Runtime database already exists; refusing to overwrite it"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:10]
    temp_db = destination.with_name(destination.name + f".migrating-{token}")
    temp_artifacts = destination.parent / f"artifacts.migrating-{token}"
    final_artifacts = destination.parent / "artifacts"
    if final_artifacts.exists():
        raise RuntimeHomeMigrationError(
            "canonical Artifact store already exists; refusing to merge automatically"
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = source.parent / "migration-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_db = backup_dir / f"{source.stem}.pre-v2-{timestamp}-{token}.db"
    source_artifacts = source.parent / "artifacts"

    source_counts: dict[str, int]
    source_schema = 0
    try:
        source_conn = sqlite3.connect(source, timeout=5.0)
        try:
            source_conn.execute("PRAGMA busy_timeout = 5000")
            source_schema = int(source_conn.execute("PRAGMA user_version").fetchone()[0])
            quick = str(source_conn.execute("PRAGMA quick_check").fetchone()[0])
            if quick != "ok":
                raise RuntimeHomeMigrationError(
                    f"legacy Runtime quick_check failed: {quick}"
                )
            fk = source_conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise RuntimeHomeMigrationError(
                    f"legacy Runtime has {len(fk)} foreign-key violations"
                )
            source_counts = _row_counts(source_conn)

            # Do not hold BEGIN EXCLUSIVE while calling Connection.backup():
            # on Python's sqlite3 driver that combination can self-block.
            # backup() itself produces a transactionally consistent database
            # snapshot.  The CLI contract requires the Runtime to be stopped
            # so the sibling Artifact CAS is quiescent at the same time.
            destination_conn = sqlite3.connect(temp_db)
            try:
                source_conn.backup(destination_conn)
            finally:
                destination_conn.close()
        finally:
            source_conn.close()

        # The exact pre-migration SQLite snapshot doubles as the rollback
        # backup, so the backup and migrated copy can never represent two
        # different source revisions.
        shutil.copy2(temp_db, backup_db)
        if source_artifacts.exists():
            shutil.copytree(source_artifacts, temp_artifacts)
        else:
            temp_artifacts.mkdir(parents=True, exist_ok=False)

        # Upgrade the copied DB only.  The original remains untouched.
        migrated = Database(temp_db)
        migrated.initialize()
        connection = sqlite3.connect(temp_db)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            destination_schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            fk_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            destination_counts = _row_counts(connection)
        finally:
            connection.close()
        if quick_check != "ok":
            raise RuntimeHomeMigrationError(
                f"migrated Runtime quick_check failed: {quick_check}"
            )
        if fk_rows:
            raise RuntimeHomeMigrationError(
                f"migrated Runtime has {len(fk_rows)} foreign-key violations"
            )
        for table, expected in source_counts.items():
            actual = destination_counts.get(table)
            if actual != expected:
                raise RuntimeHomeMigrationError(
                    f"row-count mismatch for {table}: source={expected}, destination={actual}"
                )

        referenced, verified, artifact_issues = _verify_artifact_store(
            temp_db, temp_artifacts
        )
        if artifact_issues:
            raise RuntimeHomeMigrationError(
                "migrated Artifact store failed integrity audit: "
                + json.dumps(artifact_issues[:10], ensure_ascii=False)
            )

        # Publish only after every check passes.  The canonical database is
        # published last because path resolution switches to V2 when that DB
        # appears.  If any rename fails, remove only state created by this
        # invocation so callers never observe a half-published V2 home.
        for suffix in ("-wal", "-shm"):
            Path(str(temp_db) + suffix).unlink(missing_ok=True)

        marker = destination.parent / "migration-v2.json"
        temp_marker = destination.parent / f"migration-v2.migrating-{token}.json"
        temp_marker.write_text(
            json.dumps(
                {
                    "schema": "agent-runtime.home_migration/v2",
                    "migrated_at": datetime.now(timezone.utc).isoformat(),
                    "source_database": str(source),
                    "destination_database": str(destination),
                    "backup_database": str(backup_db),
                    "source_preserved": True,
                    "source_artifacts_preserved": source_artifacts.exists(),
                    "destination_schema_version": destination_schema,
                    "quick_check": quick_check,
                    "foreign_key_violation_count": len(fk_rows),
                    "artifact_reference_count": referenced,
                    "artifact_verified_count": verified,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        published_artifacts = False
        published_database = False
        try:
            os.replace(temp_artifacts, final_artifacts)
            published_artifacts = True
            os.replace(temp_db, destination)
            published_database = True
            os.replace(temp_marker, marker)
        except BaseException:
            # Runtime must be stopped during migration, so removing the
            # just-published canonical pair is safe and restores the pre-call
            # state.  The legacy source and rollback backup are untouched.
            if published_database:
                destination.unlink(missing_ok=True)
                Path(str(destination) + "-wal").unlink(missing_ok=True)
                Path(str(destination) + "-shm").unlink(missing_ok=True)
            if published_artifacts and final_artifacts.exists():
                shutil.rmtree(final_artifacts, ignore_errors=True)
            temp_marker.unlink(missing_ok=True)
            raise
        return RuntimeHomeMigrationReport(
            source_database=str(source),
            destination_database=str(destination),
            backup_database=str(backup_db),
            source_schema_version=source_schema,
            destination_schema_version=destination_schema,
            quick_check=quick_check,
            foreign_key_violation_count=len(fk_rows),
            artifact_reference_count=referenced,
            artifact_verified_count=verified,
            artifact_issue_count=0,
            source_preserved=source.exists(),
            marker_file=str(marker),
        )
    except Exception:
        temp_db.unlink(missing_ok=True)
        Path(str(temp_db) + "-wal").unlink(missing_ok=True)
        Path(str(temp_db) + "-shm").unlink(missing_ok=True)
        if temp_artifacts.exists():
            shutil.rmtree(temp_artifacts, ignore_errors=True)
        (destination.parent / f"migration-v2.migrating-{token}.json").unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent_runtime.persistence.home_migration",
        description=(
            "Explicitly migrate a stopped V1 Runtime database and Artifact CAS "
            "to the canonical Agent Runtime home. The source is never deleted."
        ),
    )
    parser.add_argument("--source", default="")
    parser.add_argument("--destination", default="")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Required safety switch; without it no migration is performed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.apply:
        parser.error("--apply is required; stop the Runtime before migration")
    try:
        report = migrate_legacy_runtime_home(
            source_database=args.source or None,
            destination_database=args.destination or None,
        )
    except (RuntimeHomeMigrationError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
