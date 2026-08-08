"""V1.6 deterministic Planner / Intelligence domain models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannerPlan:
    plan_id: str
    name: str
    task_kind: str
    complexity: str
    risk_level: str
    status: str
    requirement_sha256: str
    acceptance_sha256: str
    policy_version: str
    step_count: int
    created_at: float
    updated_at: float
    knowledge_id: str | None = None
    context_id: str | None = None
    runtime: str | None = None
    agent_profile: str | None = None
    version: int = 1


@dataclass(frozen=True)
class PlannerStep:
    step_id: str
    plan_id: str
    step_key: str
    title: str
    position: int
    kind: str
    approval_required: bool
    verification_required: bool
    capabilities_json: str
    reason_code: str
    created_at: float


@dataclass(frozen=True)
class PlannerDependency:
    plan_id: str
    step_id: str
    depends_on_step_id: str


@dataclass(frozen=True)
class PlannerEvent:
    event_id: str
    plan_id: str
    event_type: str
    event_time: float
    status: str
    reason_code: str
    step_count: int
    seq: int | None = None
