"""Durable audit metadata for explicit Tool Runtime invocations."""

from __future__ import annotations

import sqlite3

from agent_runtime.domain.tool_runtime import ToolInvocation
from agent_runtime.persistence.database import Database


_COLUMNS = """
invocation_id, tool_name, tool_version, task_id, context_id, status,
requested_at, finished_at, workspace_ref, input_sha256, output_sha256,
bytes_returned, item_count, error_code, error_message, metadata_json
"""


class ToolInvocationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def create(connection: sqlite3.Connection, invocation: ToolInvocation) -> None:
        connection.execute(
            """
            INSERT INTO tool_invocations (
                invocation_id, tool_name, tool_version, task_id, context_id,
                status, requested_at, finished_at, workspace_ref, input_sha256,
                output_sha256, bytes_returned, item_count, error_code,
                error_message, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invocation.invocation_id,
                invocation.tool_name,
                invocation.tool_version,
                invocation.task_id,
                invocation.context_id,
                invocation.status,
                invocation.requested_at,
                invocation.finished_at,
                invocation.workspace_ref,
                invocation.input_sha256,
                invocation.output_sha256,
                invocation.bytes_returned,
                invocation.item_count,
                invocation.error_code,
                invocation.error_message,
                invocation.metadata_json,
            ),
        )

    def get(self, invocation_id: str) -> ToolInvocation | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM tool_invocations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(
        self,
        *,
        tool_name: str = "",
        status: str = "",
        task_id: str = "",
        context_id: str = "",
        limit: int = 50,
    ) -> list[ToolInvocation]:
        clauses: list[str] = []
        values: list[object] = []
        if tool_name:
            clauses.append("tool_name = ?")
            values.append(tool_name)
        if status:
            clauses.append("status = ?")
            values.append(status)
        if task_id:
            clauses.append("task_id = ?")
            values.append(task_id)
        if context_id:
            clauses.append("context_id = ?")
            values.append(context_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(int(limit))
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {_COLUMNS}
                FROM tool_invocations
                {where}
                ORDER BY requested_at DESC, invocation_id DESC
                LIMIT ?
                """,
                tuple(values),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ToolInvocation:
        return ToolInvocation(
            invocation_id=str(row["invocation_id"]),
            tool_name=str(row["tool_name"]),
            tool_version=str(row["tool_version"]),
            task_id=str(row["task_id"]) if row["task_id"] is not None else None,
            context_id=(
                str(row["context_id"]) if row["context_id"] is not None else None
            ),
            status=str(row["status"]),
            requested_at=float(row["requested_at"]),
            finished_at=float(row["finished_at"]),
            workspace_ref=str(row["workspace_ref"]),
            input_sha256=str(row["input_sha256"]),
            output_sha256=(
                str(row["output_sha256"])
                if row["output_sha256"] is not None
                else None
            ),
            bytes_returned=int(row["bytes_returned"]),
            item_count=int(row["item_count"]),
            error_code=str(row["error_code"]) if row["error_code"] else None,
            error_message=(
                str(row["error_message"]) if row["error_message"] else None
            ),
            metadata_json=str(row["metadata_json"] or "{}"),
        )
