"""ID generation for durable runtime entities."""

from __future__ import annotations

import uuid


def new_task_id() -> str:
    """Public runtime task id (stable prefix, same shape as legacy tasks)."""
    return f"wb-{uuid.uuid4().hex[:12]}"


def new_runtime_session_id() -> str:
    """Runtime-owned session id; never confused with a backend session id."""
    return f"rs-{uuid.uuid4().hex[:12]}"


def new_attempt_id() -> str:
    return f"at-{uuid.uuid4().hex[:12]}"


def new_event_id() -> str:
    return f"ev-{uuid.uuid4().hex[:16]}"


def new_evidence_id() -> str:
    """PR4 evidence id (stable ``evd-`` prefix, distinct from audit events)."""
    return f"evd-{uuid.uuid4().hex[:16]}"


def new_artifact_id() -> str:
    """PR4 artifact declaration id (``art-`` prefix)."""
    return f"art-{uuid.uuid4().hex[:16]}"


def new_instance_id() -> str:
    """Bridge process instance id (lease owner identity)."""
    return f"wb-inst-{uuid.uuid4().hex[:12]}"


def new_workflow_id() -> str:
    return f"wf-{uuid.uuid4().hex[:12]}"


def new_workflow_stage_id() -> str:
    return f"wfs-{uuid.uuid4().hex[:12]}"


def new_workflow_event_id() -> str:
    return f"wfe-{uuid.uuid4().hex[:16]}"


def new_approval_id() -> str:
    return f"apr-{uuid.uuid4().hex[:16]}"


def new_context_manifest_id() -> str:
    return f"ctxm-{uuid.uuid4().hex[:12]}"


def new_tool_invocation_id() -> str:
    return f"tinv-{uuid.uuid4().hex[:16]}"


def new_knowledge_id() -> str:
    return f"knw-{uuid.uuid4().hex[:12]}"


def new_knowledge_resolution_id() -> str:
    return f"knr-{uuid.uuid4().hex[:16]}"


def new_planner_plan_id() -> str:
    return f"pln-{uuid.uuid4().hex[:12]}"


def new_planner_step_id() -> str:
    return f"pls-{uuid.uuid4().hex[:12]}"


def new_planner_event_id() -> str:
    return f"ple-{uuid.uuid4().hex[:16]}"


def new_plan_execution_id() -> str:
    return f"pex-{uuid.uuid4().hex[:12]}"


def new_plan_execution_event_id() -> str:
    return f"pee-{uuid.uuid4().hex[:16]}"
