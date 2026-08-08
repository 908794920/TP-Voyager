"""PR4 Evidence repository (append-only).

Evidence rows are immutable: this repository exposes only insert and read.
A future Verifier (PR5) appends ``verified_*`` evidence referencing the
original via ``subject_evidence_id``; nothing ever updates a row in place.
"""

from __future__ import annotations

import sqlite3

from agent_runtime.domain.evidence import Evidence
from agent_runtime.persistence.database import Database

_COLUMNS = (
    "evidence_id, task_id, attempt_id, subject_evidence_id, artifact_id, "
    "evidence_type, trust_state, origin, summary, detail_json, captured_at, "
    "created_at"
)


def _row_to_evidence(row: sqlite3.Row) -> Evidence:
    return Evidence(
        evidence_id=str(row["evidence_id"]),
        task_id=str(row["task_id"]),
        attempt_id=str(row["attempt_id"]),
        subject_evidence_id=row["subject_evidence_id"],
        artifact_id=row["artifact_id"],
        evidence_type=str(row["evidence_type"]),
        trust_state=str(row["trust_state"]),
        origin=str(row["origin"]),
        summary=str(row["summary"] or ""),
        detail_json=str(row["detail_json"] or "{}"),
        captured_at=float(row["captured_at"]),
        created_at=float(row["created_at"]),
    )


class EvidenceRepository:
    """Durable evidence rows.  Write methods take an explicit connection so
    callers compose transactions (e.g. the fenced Result transaction)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_many(
        self, connection: sqlite3.Connection, evidences: list[Evidence],
    ) -> None:
        """Insert evidence rows inside the caller's transaction.

        Append-only: duplicate ``evidence_id`` raises ``IntegrityError`` and
        rolls the surrounding transaction back.
        """
        if not evidences:
            return
        connection.executemany(
            f"""
            INSERT INTO evidences (
                evidence_id, task_id, attempt_id, subject_evidence_id,
                artifact_id, evidence_type, trust_state, origin, summary,
                detail_json, captured_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.evidence_id,
                    item.task_id,
                    item.attempt_id,
                    item.subject_evidence_id,
                    item.artifact_id,
                    item.evidence_type,
                    item.trust_state,
                    item.origin,
                    item.summary,
                    item.detail_json,
                    item.captured_at,
                    item.created_at,
                )
                for item in evidences
            ],
        )

    def list_for_attempt(self, task_id: str, attempt_id: str) -> list[Evidence]:
        """Evidence of one attempt in append order (created_at ascending)."""
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {_COLUMNS} FROM evidences
                WHERE task_id = ? AND attempt_id = ?
                ORDER BY created_at, evidence_id
                """,
                (task_id, attempt_id),
            ).fetchall()
        return [_row_to_evidence(row) for row in rows]

    def has_type_for_attempt(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        attempt_id: str,
        evidence_type: str,
    ) -> bool:
        """Check idempotently inside the caller's fenced transaction."""
        row = connection.execute(
            """
            SELECT 1 FROM evidences
            WHERE task_id = ? AND attempt_id = ? AND evidence_type = ?
            LIMIT 1
            """,
            (task_id, attempt_id, evidence_type),
        ).fetchone()
        return row is not None

    def get(self, evidence_id: str) -> Evidence | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM evidences WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        return _row_to_evidence(row) if row else None
