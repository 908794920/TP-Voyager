"""PR4 Artifact Declaration Registry repository.

PR4-B/C only persists declarations: capture_state stays ``declared`` or
``rejected``, ``storage_key`` may be NULL, and no file content is ever
touched.  The capture-state transition (``captured``/``missing``) is
explicitly deferred to PR4-D, so this repository implements no update yet.
"""

from __future__ import annotations

import sqlite3

from agent_runtime.domain.artifact import Artifact
from agent_runtime.persistence.database import Database

_COLUMNS = (
    "artifact_id, task_id, attempt_id, origin, kind, name, workspace_relpath, "
    "storage_key, capture_state, sha256, size_bytes, declared_at, captured_at, "
    "created_at, updated_at, metadata_json"
)


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        artifact_id=str(row["artifact_id"]),
        task_id=str(row["task_id"]),
        attempt_id=str(row["attempt_id"]),
        origin=str(row["origin"]),
        kind=str(row["kind"]),
        name=str(row["name"]),
        workspace_relpath=row["workspace_relpath"],
        storage_key=row["storage_key"],
        capture_state=str(row["capture_state"]),
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        declared_at=float(row["declared_at"]),
        captured_at=row["captured_at"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        metadata_json=str(row["metadata_json"] or "{}"),
    )


class ArtifactRepository:
    """Durable artifact declarations.  Write methods take an explicit
    connection so callers compose transactions (e.g. the fenced Result
    transaction where artifacts are inserted before evidence)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_many(
        self, connection: sqlite3.Connection, artifacts: list[Artifact],
    ) -> None:
        """Insert artifact declarations inside the caller's transaction.

        Duplicate ``artifact_id`` raises ``IntegrityError`` and rolls the
        surrounding transaction back.
        """
        if not artifacts:
            return
        connection.executemany(
            f"""
            INSERT INTO artifacts (
                artifact_id, task_id, attempt_id, origin, kind, name,
                workspace_relpath, storage_key, capture_state, sha256,
                size_bytes, declared_at, captured_at, created_at, updated_at,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.artifact_id,
                    item.task_id,
                    item.attempt_id,
                    item.origin,
                    item.kind,
                    item.name,
                    item.workspace_relpath,
                    item.storage_key,
                    item.capture_state,
                    item.sha256,
                    item.size_bytes,
                    item.declared_at,
                    item.captured_at,
                    item.created_at,
                    item.updated_at,
                    item.metadata_json,
                )
                for item in artifacts
            ],
        )

    def list_for_attempt(self, task_id: str, attempt_id: str) -> list[Artifact]:
        """Artifact declarations of one attempt (stable creation order)."""
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {_COLUMNS} FROM artifacts
                WHERE task_id = ? AND attempt_id = ?
                ORDER BY created_at, artifact_id
                """,
                (task_id, attempt_id),
            ).fetchall()
        return [_row_to_artifact(row) for row in rows]

    def get(self, artifact_id: str) -> Artifact | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return _row_to_artifact(row) if row else None
