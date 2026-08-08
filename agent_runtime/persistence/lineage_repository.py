"""Repository for parent/child task relations."""

from __future__ import annotations

import sqlite3

from agent_runtime.domain.lineage import TaskLineage
from agent_runtime.persistence.database import Database

_COLUMNS = (
    "child_task_id, parent_task_id, root_task_id, context_id, agent_profile, "
    "execution_mode, created_at"
)


def _row(row: sqlite3.Row) -> TaskLineage:
    return TaskLineage(
        child_task_id=str(row["child_task_id"]),
        parent_task_id=row["parent_task_id"],
        root_task_id=str(row["root_task_id"]),
        context_id=row["context_id"],
        agent_profile=row["agent_profile"],
        execution_mode=str(row["execution_mode"]),
        created_at=float(row["created_at"]),
    )


class LineageRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, connection: sqlite3.Connection, lineage: TaskLineage) -> None:
        connection.execute(
            f"INSERT INTO task_lineage ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                lineage.child_task_id,
                lineage.parent_task_id,
                lineage.root_task_id,
                lineage.context_id,
                lineage.agent_profile,
                lineage.execution_mode,
                lineage.created_at,
            ),
        )

    def get(self, child_task_id: str) -> TaskLineage | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM task_lineage WHERE child_task_id = ?",
                (child_task_id,),
            ).fetchone()
        return _row(row) if row else None

    def list_children(self, parent_task_id: str) -> list[TaskLineage]:
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM task_lineage "
                "WHERE parent_task_id = ? ORDER BY created_at, child_task_id",
                (parent_task_id,),
            ).fetchall()
        return [_row(row) for row in rows]

    def list_tree(self, root_task_id: str) -> list[TaskLineage]:
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM task_lineage "
                "WHERE root_task_id = ? ORDER BY created_at, child_task_id",
                (root_task_id,),
            ).fetchall()
        return [_row(row) for row in rows]
