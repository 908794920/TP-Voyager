"""Append-only event repository."""

from __future__ import annotations

import sqlite3

from agent_runtime.domain.event import TaskEvent
from agent_runtime.persistence.database import Database

_COLUMNS = (
    "seq, event_id, task_id, session_id, attempt_id, event_type, event_time, "
    "payload_json, visibility"
)


def _row_to_event(row: sqlite3.Row) -> TaskEvent:
    return TaskEvent(
        seq=int(row["seq"]),
        event_id=str(row["event_id"]),
        task_id=str(row["task_id"]),
        session_id=row["session_id"],
        attempt_id=row["attempt_id"],
        event_type=str(row["event_type"]),
        event_time=float(row["event_time"]),
        payload_json=str(row["payload_json"] or "{}"),
        visibility=str(row["visibility"] or "public"),
    )


class EventRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def append(self, connection: sqlite3.Connection, event: TaskEvent) -> int:
        """Insert one event; returns its sequence number.  Rows are immutable."""
        cursor = connection.execute(
            f"""
            INSERT INTO events (
                event_id, task_id, session_id, attempt_id, event_type,
                event_time, payload_json, visibility
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.task_id,
                event.session_id,
                event.attempt_id,
                event.event_type,
                event.event_time,
                event.payload_json,
                event.visibility,
            ),
        )
        return int(cursor.lastrowid or 0)

    def get_events(self, task_id: str) -> list[TaskEvent]:
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM events WHERE task_id = ? ORDER BY seq",
                (task_id,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def count(self, task_id: str) -> int:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM events WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row[0] or 0)
