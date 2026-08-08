"""Persistence for the V2 Plan Execution control plane."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from agent_runtime.domain.plan_execution import (
    PlanExecution,
    PlanExecutionEvent,
    PlanExecutionStep,
    PlanResult,
)
from agent_runtime.persistence.database import Database

_EXEC_COLUMNS = (
    "execution_id, plan_id, workflow_id, status, reason_code, "
    "input_manifest_sha256, created_at, updated_at, started_at, finished_at, version"
)
_STEP_COLUMNS = (
    "execution_id, step_id, stage_id, runtime, route, model, reasoning_effort, "
    "agent_profile, context_id, knowledge_id, prompt_sha256, knowledge_query_sha256, "
    "verification_required, verification_plan_json, binding_json, created_at, updated_at"
)
_EVENT_COLUMNS = (
    "seq, event_id, execution_id, step_id, event_type, event_time, status, "
    "reason_code, payload_json"
)
_RESULT_COLUMNS = "execution_id, schema, result_json, created_at"


def _execution(row: sqlite3.Row) -> PlanExecution:
    return PlanExecution(
        execution_id=str(row["execution_id"]),
        plan_id=str(row["plan_id"]),
        workflow_id=str(row["workflow_id"]),
        status=str(row["status"]),
        reason_code=row["reason_code"],
        input_manifest_sha256=str(row["input_manifest_sha256"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        started_at=(float(row["started_at"]) if row["started_at"] is not None else None),
        finished_at=(float(row["finished_at"]) if row["finished_at"] is not None else None),
        version=int(row["version"]),
    )


def _step(row: sqlite3.Row) -> PlanExecutionStep:
    return PlanExecutionStep(
        execution_id=str(row["execution_id"]),
        step_id=str(row["step_id"]),
        stage_id=str(row["stage_id"]),
        runtime=str(row["runtime"]),
        route=str(row["route"]),
        model=row["model"],
        reasoning_effort=row["reasoning_effort"],
        agent_profile=row["agent_profile"],
        context_id=row["context_id"],
        knowledge_id=row["knowledge_id"],
        prompt_sha256=str(row["prompt_sha256"]),
        knowledge_query_sha256=row["knowledge_query_sha256"],
        verification_required=bool(row["verification_required"]),
        verification_plan_json=str(row["verification_plan_json"] or "{}"),
        binding_json=str(row["binding_json"] or "{}"),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _event(row: sqlite3.Row) -> PlanExecutionEvent:
    return PlanExecutionEvent(
        seq=int(row["seq"]),
        event_id=str(row["event_id"]),
        execution_id=str(row["execution_id"]),
        step_id=row["step_id"],
        event_type=str(row["event_type"]),
        event_time=float(row["event_time"]),
        status=str(row["status"]),
        reason_code=row["reason_code"],
        payload_json=str(row["payload_json"] or "{}"),
    )


def _result(row: sqlite3.Row) -> PlanResult:
    return PlanResult(
        execution_id=str(row["execution_id"]),
        schema=str(row["schema"]),
        result_json=str(row["result_json"]),
        created_at=float(row["created_at"]),
    )


class PlanExecutionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_execution(
        self,
        connection: sqlite3.Connection,
        execution: PlanExecution,
        steps: Iterable[PlanExecutionStep],
    ) -> None:
        connection.execute(
            f"INSERT INTO plan_executions ({_EXEC_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                execution.execution_id,
                execution.plan_id,
                execution.workflow_id,
                execution.status,
                execution.reason_code,
                execution.input_manifest_sha256,
                execution.created_at,
                execution.updated_at,
                execution.started_at,
                execution.finished_at,
                execution.version,
            ),
        )
        connection.executemany(
            f"INSERT INTO plan_execution_steps ({_STEP_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item.execution_id,
                    item.step_id,
                    item.stage_id,
                    item.runtime,
                    item.route,
                    item.model,
                    item.reasoning_effort,
                    item.agent_profile,
                    item.context_id,
                    item.knowledge_id,
                    item.prompt_sha256,
                    item.knowledge_query_sha256,
                    1 if item.verification_required else 0,
                    item.verification_plan_json,
                    item.binding_json,
                    item.created_at,
                    item.updated_at,
                )
                for item in steps
            ],
        )

    def append_event(
        self, connection: sqlite3.Connection, event: PlanExecutionEvent,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO plan_execution_events (
                event_id, execution_id, step_id, event_type, event_time,
                status, reason_code, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.execution_id,
                event.step_id,
                event.event_type,
                event.event_time,
                event.status,
                event.reason_code,
                event.payload_json,
            ),
        )
        return int(cursor.lastrowid or 0)

    def get_execution(self, execution_id: str) -> PlanExecution | None:
        with self.db.connect() as connection:
            return self.get_execution_in_connection(connection, execution_id)

    @staticmethod
    def get_execution_in_connection(
        connection: sqlite3.Connection, execution_id: str,
    ) -> PlanExecution | None:
        row = connection.execute(
            f"SELECT {_EXEC_COLUMNS} FROM plan_executions WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        return _execution(row) if row else None

    def get_by_plan(self, plan_id: str) -> PlanExecution | None:
        with self.db.connect() as connection:
            return self.get_by_plan_in_connection(connection, plan_id)

    @staticmethod
    def get_by_plan_in_connection(
        connection: sqlite3.Connection, plan_id: str,
    ) -> PlanExecution | None:
        row = connection.execute(
            f"SELECT {_EXEC_COLUMNS} FROM plan_executions WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        return _execution(row) if row else None

    def list_non_terminal(self) -> list[PlanExecution]:
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT {_EXEC_COLUMNS} FROM plan_executions "
                "WHERE status NOT IN ('completed', 'failed', 'cancelled') "
                "ORDER BY created_at, execution_id"
            ).fetchall()
        return [_execution(row) for row in rows]

    def get_by_workflow(self, workflow_id: str) -> PlanExecution | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_EXEC_COLUMNS} FROM plan_executions WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        return _execution(row) if row else None

    def list_steps(self, execution_id: str) -> list[PlanExecutionStep]:
        with self.db.connect() as connection:
            return self.list_steps_in_connection(connection, execution_id)

    @staticmethod
    def list_steps_in_connection(
        connection: sqlite3.Connection, execution_id: str,
    ) -> list[PlanExecutionStep]:
        rows = connection.execute(
            f"SELECT {', '.join('pes.' + part.strip() for part in _STEP_COLUMNS.split(','))} "
            "FROM plan_execution_steps pes "
            "JOIN planner_steps ps ON ps.step_id = pes.step_id "
            "WHERE pes.execution_id = ? ORDER BY ps.position, pes.step_id",
            (execution_id,),
        ).fetchall()
        return [_step(row) for row in rows]

    @staticmethod
    def get_step_by_stage_in_connection(
        connection: sqlite3.Connection, stage_id: str,
    ) -> PlanExecutionStep | None:
        row = connection.execute(
            f"SELECT {_STEP_COLUMNS} FROM plan_execution_steps WHERE stage_id = ?",
            (stage_id,),
        ).fetchone()
        return _step(row) if row else None

    def list_events(self, execution_id: str, *, limit: int = 100) -> list[PlanExecutionEvent]:
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT {_EVENT_COLUMNS} FROM plan_execution_events "
                "WHERE execution_id = ? ORDER BY seq DESC LIMIT ?",
                (execution_id, int(limit)),
            ).fetchall()
        return [_event(row) for row in rows]

    @staticmethod
    def update_status(
        connection: sqlite3.Connection,
        execution_id: str,
        *,
        expected_version: int,
        status: str,
        reason_code: str | None,
        updated_at: float,
        started_at: float | None = None,
        finished_at: float | None = None,
    ) -> bool:
        cursor = connection.execute(
            """
            UPDATE plan_executions
            SET status = ?, reason_code = ?, updated_at = ?,
                started_at = COALESCE(?, started_at),
                finished_at = COALESCE(?, finished_at),
                version = version + 1
            WHERE execution_id = ? AND version = ?
            """,
            (
                status,
                reason_code,
                updated_at,
                started_at,
                finished_at,
                execution_id,
                expected_version,
            ),
        )
        return cursor.rowcount == 1

    def get_result(self, execution_id: str) -> PlanResult | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_RESULT_COLUMNS} FROM plan_results WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        return _result(row) if row else None

    @staticmethod
    def save_result(
        connection: sqlite3.Connection, result: PlanResult,
    ) -> None:
        connection.execute(
            f"INSERT INTO plan_results ({_RESULT_COLUMNS}) VALUES (?, ?, ?, ?)",
            (
                result.execution_id,
                result.schema,
                result.result_json,
                result.created_at,
            ),
        )

    @staticmethod
    def save_result_if_absent(
        connection: sqlite3.Connection, result: PlanResult,
    ) -> bool:
        cursor = connection.execute(
            f"INSERT OR IGNORE INTO plan_results ({_RESULT_COLUMNS}) VALUES (?, ?, ?, ?)",
            (
                result.execution_id,
                result.schema,
                result.result_json,
                result.created_at,
            ),
        )
        return cursor.rowcount == 1
