"""Session repository."""

from __future__ import annotations

import sqlite3

from agent_runtime.domain.session import Session
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.errors import RuntimePersistenceError

_COLUMNS = (
    "session_id, task_id, backend, route, backend_session_id, created_at, "
    "updated_at, metadata_json, owner_instance_id, owner_generation, "
    "lease_expires_at"
)


DEFAULT_LEASE_DURATION_SECONDS = 300.0


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        session_id=str(row["session_id"]),
        task_id=str(row["task_id"]),
        backend=str(row["backend"]),
        route=str(row["route"]),
        backend_session_id=row["backend_session_id"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        metadata_json=str(row["metadata_json"] or "{}"),
        owner_instance_id=row["owner_instance_id"],
        owner_generation=int(row["owner_generation"] or 0),
        lease_expires_at=row["lease_expires_at"],
    )


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, connection: sqlite3.Connection, session: Session) -> None:
        connection.execute(
            f"""
            INSERT INTO sessions ({_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.task_id,
                session.backend,
                session.route,
                session.backend_session_id,
                session.created_at,
                session.updated_at,
                session.metadata_json,
                session.owner_instance_id,
                session.owner_generation,
                session.lease_expires_at,
            ),
        )

    def get_by_id(self, session_id: str) -> Session | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return _row_to_session(row) if row else None

    def get_by_task_id(self, task_id: str) -> Session | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM sessions WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchone()
        return _row_to_session(row) if row else None

    def update_backend_session_id(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        backend_session_id: str,
        updated_at: float,
    ) -> bool:
        cursor = connection.execute(
            """
            UPDATE sessions
            SET backend_session_id = COALESCE(backend_session_id, ?),
                updated_at = ?
            WHERE session_id = ?
            """,
            (backend_session_id, updated_at, session_id),
        )
        return cursor.rowcount == 1

    def claim_backend_session_id(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        backend_session_id: str,
        updated_at: float,
    ) -> tuple[str, str]:
        """Atomically claim the backend session id inside the caller's transaction.

        Single-transaction first write: the read and the conditional
        UPDATE run on the same connection inside one transaction, so two
        concurrent first writers cannot both emit a ``session_created``
        event.

        Returns ``(outcome, session_id)`` where outcome is one of:
        - ``"created"``: this call performed the first write;
        - ``"same"``:    the value is already present and identical;
        - ``"kept"``:    an earlier different value is preserved
          (first-write-wins, no duplicate event).
        """
        row = connection.execute(
            "SELECT session_id, backend_session_id FROM sessions "
            "WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchone()
        if row is None:
            raise RuntimePersistenceError(
                f"no runtime session row for task {task_id}"
            )
        session_id = str(row["session_id"])
        if row["backend_session_id"] == backend_session_id:
            return "same", session_id
        if row["backend_session_id"]:
            return "kept", session_id
        cursor = connection.execute(
            "UPDATE sessions SET backend_session_id = ?, updated_at = ? "
            "WHERE session_id = ? AND backend_session_id IS NULL",
            (backend_session_id, updated_at, session_id),
        )
        if cursor.rowcount == 1:
            return "created", session_id
        # A concurrent first-writer won between the read and the update.
        return "kept", session_id

    def list_by_task_ids(self, task_ids: list[str]) -> list[Session]:
        if not task_ids:
            return []
        placeholders = ",".join("?" for _ in task_ids)
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM sessions WHERE task_id IN ({placeholders})",
                task_ids,
            ).fetchall()
        return [_row_to_session(row) for row in rows]

    # ------------------------------------------------------------ lease API

    def acquire_lease(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        instance_id: str,
        db_now: float,
        lease_duration_seconds: float = DEFAULT_LEASE_DURATION_SECONDS,
    ) -> tuple[bool, int, float | None]:
        """Atomically acquire (or take over) the session lease.

        PR3.1: a single conditional UPDATE is the compare-and-swap — the
        ownership decision lives in the WHERE clause, so two concurrent
        instances can never both win.  PR3.2: the generation CASE requires
        a still-valid lease for the same-owner renewal path.  PR3.3: all
        comparisons use ``db_now`` (the database clock read AFTER the
        write lock was granted), and a same-owner live re-acquire returns
        ``(False, gen, None)`` — AlreadyOwned, never a second valid
        ``LeaseInfo`` (lease isolates process instances, not threads).

        Returns ``(acquired, generation, expires_at)`` where ``expires_at``
        is the durable new expiry on success (``db_now + duration``), else
        ``None``.
        """
        cursor = connection.execute(
            """
            UPDATE sessions
            SET owner_instance_id = ?,
                owner_generation =
                    CASE
                        WHEN owner_instance_id = ?
                             AND lease_expires_at > ?
                        THEN owner_generation
                        ELSE owner_generation + 1
                    END,
                lease_expires_at = ?,
                updated_at = ?
            WHERE session_id = ?
              AND (
                  owner_instance_id IS NULL
                  OR lease_expires_at IS NULL
                  OR lease_expires_at <= ?
              )
            """,
            (
                instance_id,
                instance_id,
                db_now,
                db_now + lease_duration_seconds,
                db_now,
                session_id,
                db_now,
            ),
        )
        if cursor.rowcount == 0:
            # Held by the same live owner (AlreadyOwned) or by another live
            # owner: never report "acquired".  Report the current state so
            # callers can diagnose.
            row = connection.execute(
                "SELECT owner_generation FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise RuntimePersistenceError(f"session {session_id} not found")
            return False, int(row["owner_generation"] or 0), None
        row = connection.execute(
            "SELECT owner_generation FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise RuntimePersistenceError(f"session {session_id} not found")
        return (
            True,
            int(row["owner_generation"] or 0),
            db_now + lease_duration_seconds,
        )

    def renew_lease(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        instance_id: str,
        generation: int,
        db_now: float,
        lease_duration_seconds: float = DEFAULT_LEASE_DURATION_SECONDS,
    ) -> float | None:
        """Extend the lease; succeeds only for the current owner+generation.

        PR3.2: the lease must still be unexpired — an expired lease can
        never be revived by its old owner.  PR3.3: ``db_now`` is the
        database clock read AFTER the write lock was granted, so a lock
        wait that crosses the deadline makes ``renew`` fail instead of
        reviving an expired lease.  Returns the durable new expiry
        (``db_now + duration``) on success, else ``None``.
        """
        cursor = connection.execute(
            "UPDATE sessions SET lease_expires_at = ?, updated_at = ? "
            "WHERE session_id = ? AND owner_instance_id = ? "
            "AND owner_generation = ? AND lease_expires_at > ?",
            (
                db_now + lease_duration_seconds,
                db_now,
                session_id,
                instance_id,
                generation,
                db_now,
            ),
        )
        if cursor.rowcount == 1:
            return db_now + lease_duration_seconds
        return None

    def release_lease(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        instance_id: str,
        generation: int,
        now: float,
    ) -> bool:
        """Release ownership; only the current owner+generation may release."""
        cursor = connection.execute(
            "UPDATE sessions SET owner_instance_id = NULL, "
            "owner_generation = owner_generation, lease_expires_at = NULL, "
            "updated_at = ? "
            "WHERE session_id = ? AND owner_instance_id = ? "
            "AND owner_generation = ?",
            (now, session_id, instance_id, generation),
        )
        return cursor.rowcount == 1

    def ensure_lease(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        instance_id: str,
        generation: int,
        now: float,
    ) -> bool:
        """Fencing check: is this caller still the live owner?

        True only when the durable row carries the same instance id and
        generation and the lease has not expired.
        """
        row = connection.execute(
            "SELECT owner_instance_id, owner_generation, lease_expires_at "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return False
        if row["owner_instance_id"] != instance_id:
            return False
        if int(row["owner_generation"] or 0) != generation:
            return False
        expires = row["lease_expires_at"]
        return expires is not None and expires > now
