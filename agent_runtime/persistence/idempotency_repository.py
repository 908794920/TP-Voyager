"""Idempotency repository: database-atomic key claiming.

Correctness relies on the ``UNIQUE(idempotency_key)`` constraint, never on
process-local locks — concurrent workers cannot create two tasks for one key.
"""

from __future__ import annotations

import sqlite3
from enum import Enum
from typing import NamedTuple

from agent_runtime.persistence.database import Database


class ClaimOutcome(str, Enum):
    """Result of claiming an idempotency key inside a creation transaction."""

    CREATED = "created"      # key freshly bound to this task (caller must create)
    REPLAY = "replay"        # key already bound to same request fingerprint
    CONFLICT = "conflict"    # key already bound to a different request
    NO_KEY = "no_key"        # caller supplied no idempotency key


class ClaimResult(NamedTuple):
    outcome: ClaimOutcome
    task_id: str | None  # existing task id for REPLAY / CONFLICT


class IdempotencyRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def claim(
        self,
        connection: sqlite3.Connection,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        task_id: str,
        created_at: float,
    ) -> ClaimResult:
        """Atomically occupy the key, or return the already-bound task.

        Must run inside the same transaction that creates the task so that a
        failed creation never leaves an orphaned key binding.
        """
        canonical_key = idempotency_key.strip()
        if not canonical_key:
            return ClaimResult(ClaimOutcome.NO_KEY, None)
        cursor = connection.execute(
            """
            INSERT INTO idempotency (idempotency_key, request_fingerprint, task_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (canonical_key, request_fingerprint, task_id, created_at),
        )
        if cursor.rowcount == 1:
            return ClaimResult(ClaimOutcome.CREATED, None)
        row = connection.execute(
            "SELECT request_fingerprint, task_id FROM idempotency WHERE idempotency_key = ?",
            (canonical_key,),
        ).fetchone()
        if row is None:
            # Raced with a concurrent rollback; treat as fresh.
            connection.execute(
                "INSERT INTO idempotency (idempotency_key, request_fingerprint, task_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (canonical_key, request_fingerprint, task_id, created_at),
            )
            return ClaimResult(ClaimOutcome.CREATED, None)
        if str(row["request_fingerprint"]) == request_fingerprint:
            return ClaimResult(ClaimOutcome.REPLAY, str(row["task_id"]))
        return ClaimResult(ClaimOutcome.CONFLICT, str(row["task_id"]))

    def get_by_key(self, idempotency_key: str) -> tuple[str, str] | None:
        """Return (request_fingerprint, task_id) for an existing key."""
        canonical_key = idempotency_key.strip()
        if not canonical_key:
            return None
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT request_fingerprint, task_id FROM idempotency WHERE idempotency_key = ?",
                (canonical_key,),
            ).fetchone()
        if row is None:
            return None
        return (str(row["request_fingerprint"]), str(row["task_id"]))
