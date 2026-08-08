"""Persistence for the optional V1.2 linear workflow control plane."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from agent_runtime.domain.workflow import (
    Workflow,
    WorkflowApproval,
    WorkflowEvent,
    WorkflowStage,
)
from agent_runtime.persistence.database import Database

_WORKFLOW_COLUMNS = (
    "workflow_id, name, context_id, status, created_at, updated_at, version"
)
_STAGE_COLUMNS = (
    "stage_id, workflow_id, stage_key, title, position, status, "
    "approval_required, verification_required, completion_policy, block_reason, "
    "runtime, agent_profile, task_id, created_at, updated_at, started_at, finished_at"
)
_APPROVAL_COLUMNS = (
    "approval_id, workflow_id, stage_id, decision, actor, reason_code, decided_at"
)
_EVENT_COLUMNS = (
    "seq, event_id, workflow_id, stage_id, event_type, event_time, "
    "payload_json, visibility"
)


def _workflow(row: sqlite3.Row) -> Workflow:
    return Workflow(
        workflow_id=str(row["workflow_id"]),
        name=str(row["name"]),
        context_id=row["context_id"],
        status=str(row["status"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        version=int(row["version"]),
    )


def _stage(row: sqlite3.Row) -> WorkflowStage:
    return WorkflowStage(
        stage_id=str(row["stage_id"]),
        workflow_id=str(row["workflow_id"]),
        stage_key=str(row["stage_key"]),
        title=str(row["title"]),
        position=int(row["position"]),
        status=str(row["status"]),
        approval_required=bool(row["approval_required"]),
        verification_required=bool(row["verification_required"]),
        completion_policy=str(row["completion_policy"] or "legacy"),
        block_reason=row["block_reason"],
        runtime=row["runtime"],
        agent_profile=row["agent_profile"],
        task_id=row["task_id"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        started_at=(
            float(row["started_at"]) if row["started_at"] is not None else None
        ),
        finished_at=(
            float(row["finished_at"]) if row["finished_at"] is not None else None
        ),
    )


def _approval(row: sqlite3.Row) -> WorkflowApproval:
    return WorkflowApproval(
        approval_id=str(row["approval_id"]),
        workflow_id=str(row["workflow_id"]),
        stage_id=str(row["stage_id"]),
        decision=str(row["decision"]),
        actor=str(row["actor"]),
        reason_code=row["reason_code"],
        decided_at=float(row["decided_at"]),
    )


def _event(row: sqlite3.Row) -> WorkflowEvent:
    return WorkflowEvent(
        seq=int(row["seq"]),
        event_id=str(row["event_id"]),
        workflow_id=str(row["workflow_id"]),
        stage_id=row["stage_id"],
        event_type=str(row["event_type"]),
        event_time=float(row["event_time"]),
        payload_json=str(row["payload_json"] or "{}"),
        visibility=str(row["visibility"] or "public"),
    )


class WorkflowRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --------------------------------------------------------------- inserts

    def create_workflow(
        self, connection: sqlite3.Connection, workflow: Workflow,
    ) -> None:
        connection.execute(
            f"INSERT INTO workflows ({_WORKFLOW_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                workflow.workflow_id,
                workflow.name,
                workflow.context_id,
                workflow.status,
                workflow.created_at,
                workflow.updated_at,
                workflow.version,
            ),
        )

    def create_stages(
        self, connection: sqlite3.Connection, stages: Iterable[WorkflowStage],
    ) -> None:
        connection.executemany(
            f"""
            INSERT INTO workflow_stages ({_STAGE_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.stage_id,
                    item.workflow_id,
                    item.stage_key,
                    item.title,
                    item.position,
                    item.status,
                    1 if item.approval_required else 0,
                    1 if item.verification_required else 0,
                    item.completion_policy,
                    item.block_reason,
                    item.runtime,
                    item.agent_profile,
                    item.task_id,
                    item.created_at,
                    item.updated_at,
                    item.started_at,
                    item.finished_at,
                )
                for item in stages
            ],
        )

    def append_event(
        self, connection: sqlite3.Connection, event: WorkflowEvent,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO workflow_events (
                event_id, workflow_id, stage_id, event_type, event_time,
                payload_json, visibility
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.workflow_id,
                event.stage_id,
                event.event_type,
                event.event_time,
                event.payload_json,
                event.visibility,
            ),
        )
        return int(cursor.lastrowid or 0)

    def create_approval(
        self, connection: sqlite3.Connection, approval: WorkflowApproval,
    ) -> None:
        connection.execute(
            f"INSERT INTO workflow_approvals ({_APPROVAL_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                approval.approval_id,
                approval.workflow_id,
                approval.stage_id,
                approval.decision,
                approval.actor,
                approval.reason_code,
                approval.decided_at,
            ),
        )

    # ---------------------------------------------------------------- reads

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        with self.db.connect() as connection:
            return self.get_workflow_in_connection(connection, workflow_id)

    def get_workflow_in_connection(
        self, connection: sqlite3.Connection, workflow_id: str,
    ) -> Workflow | None:
        row = connection.execute(
            f"SELECT {_WORKFLOW_COLUMNS} FROM workflows WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        return _workflow(row) if row else None

    def list_workflows(self, status: str = "") -> list[Workflow]:
        with self.db.connect() as connection:
            if status:
                rows = connection.execute(
                    f"SELECT {_WORKFLOW_COLUMNS} FROM workflows "
                    "WHERE status = ? ORDER BY created_at, workflow_id",
                    (status,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {_WORKFLOW_COLUMNS} FROM workflows "
                    "ORDER BY created_at, workflow_id"
                ).fetchall()
        return [_workflow(row) for row in rows]

    def list_stages(self, workflow_id: str) -> list[WorkflowStage]:
        with self.db.connect() as connection:
            return self.list_stages_in_connection(connection, workflow_id)

    def list_stages_in_connection(
        self, connection: sqlite3.Connection, workflow_id: str,
    ) -> list[WorkflowStage]:
        rows = connection.execute(
            f"SELECT {_STAGE_COLUMNS} FROM workflow_stages "
            "WHERE workflow_id = ? ORDER BY position",
            (workflow_id,),
        ).fetchall()
        return [_stage(row) for row in rows]

    def get_stage_by_key_in_connection(
        self,
        connection: sqlite3.Connection,
        workflow_id: str,
        stage_key: str,
    ) -> WorkflowStage | None:
        row = connection.execute(
            f"SELECT {_STAGE_COLUMNS} FROM workflow_stages "
            "WHERE workflow_id = ? AND stage_key = ?",
            (workflow_id, stage_key),
        ).fetchone()
        return _stage(row) if row else None

    def get_stage_by_task_in_connection(
        self, connection: sqlite3.Connection, task_id: str,
    ) -> WorkflowStage | None:
        row = connection.execute(
            f"SELECT {_STAGE_COLUMNS} FROM workflow_stages WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return _stage(row) if row else None

    def get_approval_for_stage_in_connection(
        self, connection: sqlite3.Connection, stage_id: str,
    ) -> WorkflowApproval | None:
        row = connection.execute(
            f"SELECT {_APPROVAL_COLUMNS} FROM workflow_approvals WHERE stage_id = ?",
            (stage_id,),
        ).fetchone()
        return _approval(row) if row else None

    def list_approvals(self, workflow_id: str) -> list[WorkflowApproval]:
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT {_APPROVAL_COLUMNS} FROM workflow_approvals "
                "WHERE workflow_id = ? ORDER BY decided_at, approval_id",
                (workflow_id,),
            ).fetchall()
        return [_approval(row) for row in rows]

    def list_events(self, workflow_id: str) -> list[WorkflowEvent]:
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT {_EVENT_COLUMNS} FROM workflow_events "
                "WHERE workflow_id = ? ORDER BY seq",
                (workflow_id,),
            ).fetchall()
        return [_event(row) for row in rows]

    # --------------------------------------------------------------- updates

    def update_workflow_status(
        self,
        connection: sqlite3.Connection,
        workflow_id: str,
        *,
        status: str,
        updated_at: float,
        expected_version: int,
    ) -> bool:
        cursor = connection.execute(
            """
            UPDATE workflows
            SET status = ?, updated_at = ?, version = version + 1
            WHERE workflow_id = ? AND version = ?
            """,
            (status, updated_at, workflow_id, expected_version),
        )
        return cursor.rowcount == 1

    def update_stage(
        self,
        connection: sqlite3.Connection,
        stage_id: str,
        *,
        status: str,
        updated_at: float,
        task_id: str | None = None,
        bind_task: bool = False,
        started_at: float | None = None,
        finished_at: float | None = None,
        block_reason: str | None = None,
        set_block_reason: bool = False,
    ) -> bool:
        if bind_task:
            cursor = connection.execute(
                """
                UPDATE workflow_stages
                SET status = ?, updated_at = ?, task_id = ?,
                    started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at),
                    block_reason = CASE WHEN ? THEN ? ELSE block_reason END
                WHERE stage_id = ?
                """,
                (
                    status,
                    updated_at,
                    task_id,
                    started_at,
                    finished_at,
                    1 if set_block_reason else 0,
                    block_reason,
                    stage_id,
                ),
            )
        else:
            cursor = connection.execute(
                """
                UPDATE workflow_stages
                SET status = ?, updated_at = ?,
                    started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at),
                    block_reason = CASE WHEN ? THEN ? ELSE block_reason END
                WHERE stage_id = ?
                """,
                (
                    status, updated_at, started_at, finished_at,
                    1 if set_block_reason else 0, block_reason, stage_id,
                ),
            )
        return cursor.rowcount == 1
