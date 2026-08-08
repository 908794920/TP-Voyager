"""Durable linear workflow control-plane models for Runtime V1.2.

The workflow layer coordinates existing durable tasks.  It deliberately does
not own prompts, backend handles, retries, or task lifecycle state.  A workflow
is an ordered sequence of stages; each stage may bind to at most one existing
Runtime task and may optionally require an explicit local operator checkpoint
before the next stage becomes ready.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Workflow:
    workflow_id: str
    name: str
    status: str
    created_at: float
    updated_at: float
    context_id: str | None = None
    version: int = 1


@dataclass(frozen=True)
class WorkflowStage:
    stage_id: str
    workflow_id: str
    stage_key: str
    title: str
    position: int
    status: str
    approval_required: bool
    created_at: float
    updated_at: float
    verification_required: bool = False
    completion_policy: str = "legacy"
    block_reason: str | None = None
    runtime: str | None = None
    agent_profile: str | None = None
    task_id: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


@dataclass(frozen=True)
class WorkflowStageSpec:
    """Validated caller intent used when creating a workflow.

    ``runtime`` and ``agent_profile`` are binding constraints.  V1.2 never
    dispatches a backend automatically and never switches backend silently.
    """

    stage_key: str
    title: str
    approval_required: bool = False
    verification_required: bool = False
    completion_policy: str = "legacy"
    runtime: str | None = None
    agent_profile: str | None = None


@dataclass(frozen=True)
class WorkflowApproval:
    approval_id: str
    workflow_id: str
    stage_id: str
    decision: str
    actor: str
    reason_code: str | None
    decided_at: float


@dataclass(frozen=True)
class WorkflowEvent:
    event_id: str
    workflow_id: str
    event_type: str
    event_time: float
    stage_id: str | None = None
    payload_json: str = "{}"
    visibility: str = "public"
    seq: int | None = None
