"""Persistence for the deterministic V1.6 Planner foundation.

Only content-free planning metadata is stored.  Requirement and acceptance
texts are represented by SHA-256 digests and are never persisted verbatim.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from agent_runtime.domain.planner import (
    PlannerDependency,
    PlannerEvent,
    PlannerPlan,
    PlannerStep,
)
from agent_runtime.persistence.database import Database

_PLAN_COLUMNS = (
    "plan_id, name, task_kind, complexity, risk_level, status, "
    "requirement_sha256, acceptance_sha256, policy_version, step_count, "
    "knowledge_id, context_id, runtime, agent_profile, created_at, updated_at, version"
)
_STEP_COLUMNS = (
    "step_id, plan_id, step_key, title, position, kind, approval_required, "
    "verification_required, capabilities_json, reason_code, created_at"
)
_DEP_COLUMNS = "plan_id, step_id, depends_on_step_id"
_EVENT_COLUMNS = (
    "seq, event_id, plan_id, event_type, event_time, status, reason_code, step_count"
)


def _plan(row: sqlite3.Row) -> PlannerPlan:
    return PlannerPlan(
        plan_id=str(row["plan_id"]),
        name=str(row["name"]),
        task_kind=str(row["task_kind"]),
        complexity=str(row["complexity"]),
        risk_level=str(row["risk_level"]),
        status=str(row["status"]),
        requirement_sha256=str(row["requirement_sha256"]),
        acceptance_sha256=str(row["acceptance_sha256"]),
        policy_version=str(row["policy_version"]),
        step_count=int(row["step_count"]),
        knowledge_id=row["knowledge_id"],
        context_id=row["context_id"],
        runtime=row["runtime"],
        agent_profile=row["agent_profile"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        version=int(row["version"]),
    )


def _step(row: sqlite3.Row) -> PlannerStep:
    return PlannerStep(
        step_id=str(row["step_id"]),
        plan_id=str(row["plan_id"]),
        step_key=str(row["step_key"]),
        title=str(row["title"]),
        position=int(row["position"]),
        kind=str(row["kind"]),
        approval_required=bool(row["approval_required"]),
        verification_required=bool(row["verification_required"]),
        capabilities_json=str(row["capabilities_json"] or "[]"),
        reason_code=str(row["reason_code"]),
        created_at=float(row["created_at"]),
    )


def _dependency(row: sqlite3.Row) -> PlannerDependency:
    return PlannerDependency(
        plan_id=str(row["plan_id"]),
        step_id=str(row["step_id"]),
        depends_on_step_id=str(row["depends_on_step_id"]),
    )


def _event(row: sqlite3.Row) -> PlannerEvent:
    return PlannerEvent(
        seq=int(row["seq"]),
        event_id=str(row["event_id"]),
        plan_id=str(row["plan_id"]),
        event_type=str(row["event_type"]),
        event_time=float(row["event_time"]),
        status=str(row["status"]),
        reason_code=str(row["reason_code"]),
        step_count=int(row["step_count"]),
    )


class PlannerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_plan(
        self,
        connection: sqlite3.Connection,
        plan: PlannerPlan,
        steps: Iterable[PlannerStep],
        dependencies: Iterable[PlannerDependency],
    ) -> None:
        connection.execute(
            f"INSERT INTO planner_plans ({_PLAN_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plan.plan_id,
                plan.name,
                plan.task_kind,
                plan.complexity,
                plan.risk_level,
                plan.status,
                plan.requirement_sha256,
                plan.acceptance_sha256,
                plan.policy_version,
                plan.step_count,
                plan.knowledge_id,
                plan.context_id,
                plan.runtime,
                plan.agent_profile,
                plan.created_at,
                plan.updated_at,
                plan.version,
            ),
        )
        connection.executemany(
            f"INSERT INTO planner_steps ({_STEP_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item.step_id,
                    item.plan_id,
                    item.step_key,
                    item.title,
                    item.position,
                    item.kind,
                    1 if item.approval_required else 0,
                    1 if item.verification_required else 0,
                    item.capabilities_json,
                    item.reason_code,
                    item.created_at,
                )
                for item in steps
            ],
        )
        connection.executemany(
            f"INSERT INTO planner_dependencies ({_DEP_COLUMNS}) VALUES (?, ?, ?)",
            [
                (item.plan_id, item.step_id, item.depends_on_step_id)
                for item in dependencies
            ],
        )

    def append_event(
        self, connection: sqlite3.Connection, event: PlannerEvent,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO planner_events (
                event_id, plan_id, event_type, event_time, status,
                reason_code, step_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.plan_id,
                event.event_type,
                event.event_time,
                event.status,
                event.reason_code,
                event.step_count,
            ),
        )
        return int(cursor.lastrowid or 0)

    def get_plan(self, plan_id: str) -> PlannerPlan | None:
        with self.db.connect() as connection:
            return self.get_plan_in_connection(connection, plan_id)

    @staticmethod
    def get_plan_in_connection(
        connection: sqlite3.Connection, plan_id: str,
    ) -> PlannerPlan | None:
        row = connection.execute(
            f"SELECT {_PLAN_COLUMNS} FROM planner_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        return _plan(row) if row else None

    def list_steps(self, plan_id: str) -> list[PlannerStep]:
        with self.db.connect() as connection:
            return self.list_steps_in_connection(connection, plan_id)

    @staticmethod
    def list_steps_in_connection(
        connection: sqlite3.Connection, plan_id: str,
    ) -> list[PlannerStep]:
        rows = connection.execute(
            f"SELECT {_STEP_COLUMNS} FROM planner_steps "
            "WHERE plan_id = ? ORDER BY position, step_id",
            (plan_id,),
        ).fetchall()
        return [_step(row) for row in rows]

    def list_dependencies(self, plan_id: str) -> list[PlannerDependency]:
        with self.db.connect() as connection:
            return self.list_dependencies_in_connection(connection, plan_id)

    @staticmethod
    def list_dependencies_in_connection(
        connection: sqlite3.Connection, plan_id: str,
    ) -> list[PlannerDependency]:
        rows = connection.execute(
            f"SELECT {_DEP_COLUMNS} FROM planner_dependencies "
            "WHERE plan_id = ? ORDER BY step_id, depends_on_step_id",
            (plan_id,),
        ).fetchall()
        return [_dependency(row) for row in rows]

    @staticmethod
    def update_status(
        connection: sqlite3.Connection,
        plan_id: str,
        *,
        expected_status: str,
        status: str,
        updated_at: float,
    ) -> bool:
        cursor = connection.execute(
            """
            UPDATE planner_plans
            SET status = ?, updated_at = ?, version = version + 1
            WHERE plan_id = ? AND status = ?
            """,
            (status, updated_at, plan_id, expected_status),
        )
        return cursor.rowcount == 1

    def list_plans(self, *, status: str = "", limit: int = 100) -> list[PlannerPlan]:
        with self.db.connect() as connection:
            if status:
                rows = connection.execute(
                    f"SELECT {_PLAN_COLUMNS} FROM planner_plans "
                    "WHERE status = ? ORDER BY created_at DESC, plan_id DESC LIMIT ?",
                    (status, int(limit)),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {_PLAN_COLUMNS} FROM planner_plans "
                    "ORDER BY created_at DESC, plan_id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        return [_plan(row) for row in rows]

    def list_events(self, *, plan_id: str = "", limit: int = 100) -> list[PlannerEvent]:
        with self.db.connect() as connection:
            if plan_id:
                rows = connection.execute(
                    f"SELECT {_EVENT_COLUMNS} FROM planner_events "
                    "WHERE plan_id = ? ORDER BY seq DESC LIMIT ?",
                    (plan_id, int(limit)),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {_EVENT_COLUMNS} FROM planner_events "
                    "ORDER BY seq DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        return [_event(row) for row in rows]
