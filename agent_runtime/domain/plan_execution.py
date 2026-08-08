"""V2 durable Plan Execution control-plane models.

These rows bind an already-prepared Planner plan to an existing Workflow and
record only execution-control metadata.  Task lifecycle truth remains in the
existing durable Task Runtime; prompt/knowledge-query bodies are represented by
SHA-256 digests only.
"""

from __future__ import annotations

from dataclasses import dataclass


PLAN_EXECUTION_SCHEMA = "agent-runtime.plan_execution/v2"
PLAN_RESULT_SCHEMA = "agent-runtime.plan_result/v2"
EXECUTION_MANIFEST_SCHEMA = "agent-runtime.execution_manifest/v2"


@dataclass(frozen=True)
class PlanExecution:
    execution_id: str
    plan_id: str
    workflow_id: str
    status: str
    input_manifest_sha256: str
    created_at: float
    updated_at: float
    reason_code: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    version: int = 1


@dataclass(frozen=True)
class PlanExecutionStep:
    execution_id: str
    step_id: str
    stage_id: str
    runtime: str
    route: str
    prompt_sha256: str
    verification_required: bool
    verification_plan_json: str
    binding_json: str
    created_at: float
    updated_at: float
    model: str | None = None
    reasoning_effort: str | None = None
    agent_profile: str | None = None
    context_id: str | None = None
    knowledge_id: str | None = None
    knowledge_query_sha256: str | None = None


@dataclass(frozen=True)
class PlanExecutionEvent:
    event_id: str
    execution_id: str
    event_type: str
    event_time: float
    status: str
    step_id: str | None = None
    reason_code: str | None = None
    payload_json: str = "{}"
    seq: int | None = None


@dataclass(frozen=True)
class PlanResult:
    execution_id: str
    schema: str
    result_json: str
    created_at: float
