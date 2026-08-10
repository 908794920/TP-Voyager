"""Task and attempt repositories (parameterized SQL, explicit transactions)."""

from __future__ import annotations

import sqlite3

from agent_runtime.domain.attempt import Attempt
from agent_runtime.domain.enums import TERMINAL_STATUS_VALUES
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database

_TASK_COLUMNS = (
    "task_id, task_type, status, route, created_at, updated_at, started_at, "
    "finished_at, cancel_requested_at, cancel_confirmed_at, session_id, "
    "current_attempt_id, result_available, result_json, error_code, "
    "error_message, version, terminal_reason, cancel_scope, cancel_initiator, "
    "timeout_reason, lost_at, orphaned_at, run_id, step_key"
)

# Lease tuple shape passed to fenced writers: (owner_instance_id,
# owner_generation, now).  When present the write only matches while the
# caller is still the live lease owner (transactional fencing).
LeaseFence = tuple[str, int, float] | None


TERMINAL_NOT_IN_PLACEHOLDERS = ",".join("?" * len(TERMINAL_STATUS_VALUES))


def _lease_fence_clause(lease: LeaseFence) -> tuple[str, tuple]:
    """Build the lease-fencing EXISTS fragment plus its bound parameters."""
    if lease is None:
        return "", ()
    owner, generation, now = lease
    return (
        " AND EXISTS ("
        "   SELECT 1 FROM sessions"
        "   WHERE sessions.task_id = tasks.task_id"
        "     AND sessions.owner_instance_id = ?"
        "     AND sessions.owner_generation = ?"
        "     AND sessions.lease_expires_at > ?"
        " )",
        (owner, generation, now),
    )


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        task_id=str(row["task_id"]),
        task_type=str(row["task_type"]),
        status=str(row["status"]),
        route=str(row["route"] or ""),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        cancel_requested_at=row["cancel_requested_at"],
        cancel_confirmed_at=row["cancel_confirmed_at"],
        session_id=row["session_id"],
        current_attempt_id=row["current_attempt_id"],
        result_available=bool(row["result_available"]),
        result_json=row["result_json"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        version=int(row["version"]),
        terminal_reason=row["terminal_reason"],
        cancel_scope=row["cancel_scope"],
        cancel_initiator=row["cancel_initiator"],
        timeout_reason=row["timeout_reason"],
        lost_at=row["lost_at"],
        orphaned_at=row["orphaned_at"],
        run_id=row["run_id"],
        step_key=row["step_key"],
    )


class TaskRepository:
    """Durable task rows.  Write methods take an explicit connection so callers
    compose transactions; read methods open their own connection."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, connection: sqlite3.Connection, task: Task) -> None:
        connection.execute(
            f"""
            INSERT INTO tasks ({_TASK_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.task_type,
                task.status,
                task.route,
                task.created_at,
                task.updated_at,
                task.started_at,
                task.finished_at,
                task.cancel_requested_at,
                task.cancel_confirmed_at,
                task.session_id,
                task.current_attempt_id,
                int(task.result_available),
                task.result_json,
                task.error_code,
                task.error_message,
                task.version,
                task.terminal_reason,
                task.cancel_scope,
                task.cancel_initiator,
                task.timeout_reason,
                task.lost_at,
                task.orphaned_at,
                task.run_id,
                task.step_key,
            ),
        )

    def get_by_id(self, task_id: str) -> Task | None:
        with self.db.connect() as connection:
            return self.get_by_id_in_connection(connection, task_id)

    def get_by_id_in_connection(
        self, connection: sqlite3.Connection, task_id: str,
    ) -> Task | None:
        """Read one task through the caller's transaction snapshot."""
        row = connection.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return _row_to_task(row) if row else None

    def get_by_run_step(self, run_id: str, step_key: str) -> Task | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_TASK_COLUMNS} FROM tasks WHERE run_id = ? AND step_key = ?",
                (run_id, step_key),
            ).fetchone()
        return _row_to_task(row) if row else None

    def update_status(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        status: str,
        version: int,
        updated_at: float,
        started_at: float | None = None,
        finished_at: float | None = None,
        session_id: str | None = None,
        current_attempt_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        terminal_reason: str | None = None,
        timeout_reason: str | None = None,
        cancel_scope: str | None = None,
        cancel_initiator: str | None = None,
        lease: LeaseFence = None,
    ) -> bool:
        fence_sql, fence_params = _lease_fence_clause(lease)
        cursor = connection.execute(
            f"""
            UPDATE tasks
            SET status = ?, updated_at = ?, version = version + 1,
                started_at = COALESCE(?, started_at),
                finished_at = COALESCE(?, finished_at),
                session_id = COALESCE(?, session_id),
                current_attempt_id = COALESCE(?, current_attempt_id),
                error_code = COALESCE(?, error_code),
                error_message = COALESCE(?, error_message),
                terminal_reason = COALESCE(?, terminal_reason),
                timeout_reason = COALESCE(?, timeout_reason),
                cancel_scope = COALESCE(?, cancel_scope),
                cancel_initiator = COALESCE(?, cancel_initiator)
            WHERE task_id = ? AND version = ?{fence_sql}
            """,
            (
                status,
                updated_at,
                started_at,
                finished_at,
                session_id,
                current_attempt_id,
                error_code,
                error_message,
                terminal_reason,
                timeout_reason,
                cancel_scope,
                cancel_initiator,
                task_id,
                version,
                *fence_params,
            ),
        )
        return cursor.rowcount == 1

    def mark_cancel_requested(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        cancel_requested_at: float,
        updated_at: float,
    ) -> bool:
        """Idempotent: only the first caller records cancel_requested_at."""
        cursor = connection.execute(
            """
            UPDATE tasks
            SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                updated_at = ?
            WHERE task_id = ? AND cancel_requested_at IS NULL
            """,
            (cancel_requested_at, updated_at, task_id),
        )
        return cursor.rowcount == 1

    def mark_cancel_confirmed(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        cancel_confirmed_at: float,
        updated_at: float,
        version: int,
        lease: LeaseFence = None,
    ) -> bool:
        fence_sql, fence_params = _lease_fence_clause(lease)
        cursor = connection.execute(
            f"""
            UPDATE tasks
            SET cancel_confirmed_at = ?, updated_at = ?, version = version + 1
            WHERE task_id = ? AND version = ?{fence_sql}
            """,
            (cancel_confirmed_at, updated_at, task_id, version, *fence_params),
        )
        return cursor.rowcount == 1

    def save_result(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        result_json: str,
        updated_at: float,
        version: int,
        lease: LeaseFence = None,
    ) -> bool:
        fence_sql, fence_params = _lease_fence_clause(lease)
        cursor = connection.execute(
            f"""
            UPDATE tasks
            SET result_json = ?, result_available = 1, updated_at = ?,
                version = version + 1
            WHERE task_id = ? AND version = ?
              AND status NOT IN ({TERMINAL_NOT_IN_PLACEHOLDERS}){fence_sql}
            """,
            (
                result_json,
                updated_at,
                task_id,
                version,
                *sorted(TERMINAL_STATUS_VALUES),
                *fence_params,
            ),
        )
        return cursor.rowcount == 1

    def mark_reconciled_terminal(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        status: str,
        marked_at: float,
        updated_at: float,
        version: int,
        error_message: str | None = None,
        lease: LeaseFence = None,
    ) -> bool:
        """PR3/PR3.1: record a LOST or ORPHANED terminal state.

        Used by restart reconciliation while it holds the session lease;
        ``marked_at`` lands in the matching ``lost_at`` / ``orphaned_at``
        column.  PR3.1: the UPDATE is fenced on version, non-terminal
        status and the current lease owner+generation+expiry, so a stale
        snapshot can never overwrite a newer terminal truth.  Never
        auto-converted to failed/cancelled.
        """
        if status not in {"lost", "orphaned"}:
            raise ValueError(f"mark_reconciled_terminal status: {status}")
        column = "lost_at" if status == "lost" else "orphaned_at"
        fence_sql, fence_params = _lease_fence_clause(lease)
        cursor = connection.execute(
            f"""
            UPDATE tasks
            SET status = ?, {column} = ?, updated_at = ?,
                version = version + 1,
                error_code = COALESCE(?, error_code),
                error_message = COALESCE(?, error_message)
            WHERE task_id = ? AND version = ?
              AND status NOT IN ({TERMINAL_NOT_IN_PLACEHOLDERS}){fence_sql}
            """,
            (
                status,
                marked_at,
                updated_at,
                status.upper(),
                error_message,
                task_id,
                version,
                *sorted(TERMINAL_STATUS_VALUES),
                *fence_params,
            ),
        )
        return cursor.rowcount == 1

    def mark_reconciled_cancelled(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        cancel_confirmed_at: float,
        finished_at: float,
        updated_at: float,
        version: int,
        lease: LeaseFence = None,
    ) -> bool:
        """PR3.1: persist a backend-confirmed ``terminal_cancelled``.

        One fenced transaction writes status=cancelled, the confirmation
        timestamps and the reconciliation initiator — never a ``task_failed``
        event.  Fenced on version, non-terminal status and the current lease.
        """
        fence_sql, fence_params = _lease_fence_clause(lease)
        cursor = connection.execute(
            f"""
            UPDATE tasks
            SET status = ?, cancel_confirmed_at = ?, finished_at = ?,
                updated_at = ?, version = version + 1, terminal_reason = ?,
                cancel_initiator = COALESCE(cancel_initiator, ?)
            WHERE task_id = ? AND version = ?
              AND status NOT IN ({TERMINAL_NOT_IN_PLACEHOLDERS}){fence_sql}
            """,
            (
                "cancelled",
                cancel_confirmed_at,
                finished_at,
                updated_at,
                "cancelled",
                "reconciliation",
                task_id,
                version,
                *sorted(TERMINAL_STATUS_VALUES),
                *fence_params,
            ),
        )
        return cursor.rowcount == 1

    def list_non_terminal(self) -> list[Task]:
        """PR3: tasks still in a non-terminal lifecycle state."""
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {_TASK_COLUMNS} FROM tasks
                WHERE status NOT IN ({TERMINAL_NOT_IN_PLACEHOLDERS})
                ORDER BY created_at
                """,
                tuple(sorted(TERMINAL_STATUS_VALUES)),
            ).fetchall()
        return [_row_to_task(row) for row in rows]

    def list_all(self) -> list[Task]:
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT {_TASK_COLUMNS} FROM tasks ORDER BY created_at"
            ).fetchall()
        return [_row_to_task(row) for row in rows]

    def create_attempt(self, connection: sqlite3.Connection, attempt: Attempt) -> None:
        connection.execute(
            """
            INSERT INTO attempts (
                attempt_id, task_id, attempt_no, backend, route, status,
                created_at, started_at, finished_at, error_code, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.attempt_id,
                attempt.task_id,
                attempt.attempt_no,
                attempt.backend,
                attempt.route,
                attempt.status,
                attempt.created_at,
                attempt.started_at,
                attempt.finished_at,
                attempt.error_code,
                attempt.error_message,
            ),
        )

    def update_attempt_status(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        attempt_no: int,
        status: str,
        started_at: float | None = None,
        finished_at: float | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        cursor = connection.execute(
            """
            UPDATE attempts
            SET status = ?,
                started_at = COALESCE(?, started_at),
                finished_at = COALESCE(?, finished_at),
                error_code = COALESCE(?, error_code),
                error_message = COALESCE(?, error_message)
            WHERE task_id = ? AND attempt_no = ?
            """,
            (
                status,
                started_at,
                finished_at,
                error_code,
                error_message,
                task_id,
                attempt_no,
            ),
        )
        return cursor.rowcount == 1

    def get_attempts(self, task_id: str) -> list[Attempt]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT attempt_id, task_id, attempt_no, backend, route, status,
                       created_at, started_at, finished_at, error_code, error_message
                FROM attempts WHERE task_id = ? ORDER BY attempt_no
                """,
                (task_id,),
            ).fetchall()
        return [Attempt(**dict(row)) for row in rows]

    def get_attempt_for_task(
        self, task_id: str, attempt_id: str,
    ) -> Attempt | None:
        """Return an attempt only when it belongs to the supplied task."""
        with self.db.connect() as connection:
            return self.get_attempt_for_task_in_connection(
                connection, task_id, attempt_id,
            )

    def get_attempt_for_task_in_connection(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        attempt_id: str,
    ) -> Attempt | None:
        """Read a task-bound attempt through the caller's transaction."""
        row = connection.execute(
            """
            SELECT attempt_id, task_id, attempt_no, backend, route, status,
                   created_at, started_at, finished_at, error_code, error_message
            FROM attempts
            WHERE task_id = ? AND attempt_id = ?
            """,
            (task_id, attempt_id),
        ).fetchone()
        return Attempt(**dict(row)) if row else None

    def get_latest_attempt(self, task_id: str) -> Attempt | None:
        """Return the highest attempt_no for legacy rows without a pointer."""
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT attempt_id, task_id, attempt_no, backend, route, status,
                       created_at, started_at, finished_at, error_code, error_message
                FROM attempts
                WHERE task_id = ?
                ORDER BY attempt_no DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return Attempt(**dict(row)) if row else None
