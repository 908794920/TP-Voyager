"""Deterministic linear workflow control plane for Runtime V1.2.

The service coordinates existing durable tasks without owning their execution.
It never stores prompts, dispatches a backend, retries a task, cancels a task,
or changes the Task/Attempt state machine.  A caller explicitly starts a normal
``subagent_*`` task and binds it to the one currently-ready workflow stage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from agent_runtime.domain.enums import (
    ApprovalDecision,
    TERMINAL_STATUS_VALUES,
    TaskStatus,
    WorkflowCompletionPolicy,
    WorkflowEventType,
    WorkflowStageStatus,
    WorkflowStatus,
)
from agent_runtime.domain.ids import (
    new_approval_id,
    new_workflow_event_id,
    new_workflow_id,
    new_workflow_stage_id,
)
from agent_runtime.domain.workflow import (
    Workflow,
    WorkflowApproval,
    WorkflowEvent,
    WorkflowStage,
    WorkflowStageSpec,
)
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.workflow_repository import WorkflowRepository
from agent_runtime.application.outcome_service import assess_task_result

_STAGE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_RUNTIME_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")


class WorkflowError(RuntimeError):
    """Base error with a public-safe message."""


class WorkflowNotFoundError(WorkflowError):
    pass


class WorkflowStateError(WorkflowError):
    pass


@dataclass(frozen=True)
class WorkflowMutationResult:
    workflow: dict[str, Any]
    replayed: bool = False


class WorkflowService:
    """Durable, linear, explicit workflow coordination."""

    MAX_STAGES = 64

    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = WorkflowRepository(db)

    # ---------------------------------------------------------------- create

    def create_workflow(
        self,
        *,
        name: str,
        stages: list[WorkflowStageSpec],
        context_id: str | None = None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        canonical_name, canonical_context, specs, identifier = (
            self._normalize_create_request(
                name=name,
                stages=stages,
                context_id=context_id,
                workflow_id=workflow_id,
            )
        )
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            return self.create_workflow_in_connection(
                connection,
                db_now,
                name=canonical_name,
                stages=specs,
                context_id=canonical_context,
                workflow_id=identifier,
                validated=True,
            )

    def create_workflow_in_connection(
        self,
        connection,
        db_now: float,
        *,
        name: str,
        stages: list[WorkflowStageSpec],
        context_id: str | None = None,
        workflow_id: str | None = None,
        validated: bool = False,
    ) -> dict[str, Any]:
        """Create a Workflow inside an existing durable transaction.

        V2 Plan Execution uses this to atomically bind a prepared Plan to its
        Workflow without making Workflow own Planner or Task state.
        """
        if validated:
            canonical_name = name
            canonical_context = context_id
            specs = stages
            identifier = str(workflow_id)
        else:
            canonical_name, canonical_context, specs, identifier = (
                self._normalize_create_request(
                    name=name,
                    stages=stages,
                    context_id=context_id,
                    workflow_id=workflow_id,
                )
            )

        workflow = Workflow(
            workflow_id=identifier,
            name=canonical_name,
            context_id=canonical_context,
            status=WorkflowStatus.ACTIVE.value,
            created_at=db_now,
            updated_at=db_now,
            version=1,
        )
        durable_stages = [
            WorkflowStage(
                stage_id=new_workflow_stage_id(),
                workflow_id=identifier,
                stage_key=spec.stage_key,
                title=spec.title,
                position=index,
                status=(
                    WorkflowStageStatus.READY.value
                    if index == 1
                    else WorkflowStageStatus.PENDING.value
                ),
                approval_required=spec.approval_required,
                verification_required=spec.verification_required,
                completion_policy=spec.completion_policy,
                runtime=spec.runtime,
                agent_profile=spec.agent_profile,
                created_at=db_now,
                updated_at=db_now,
            )
            for index, spec in enumerate(specs, start=1)
        ]
        try:
            self.repo.create_workflow(connection, workflow)
            self.repo.create_stages(connection, durable_stages)
        except Exception as exc:
            raise WorkflowStateError("workflow could not be created") from exc
        self._event(
            connection,
            identifier,
            WorkflowEventType.WORKFLOW_CREATED.value,
            db_now,
            payload={
                "status": workflow.status,
                "stage_count": len(durable_stages),
                "context_present": canonical_context is not None,
            },
        )
        for stage in durable_stages:
            self._event(
                connection,
                identifier,
                WorkflowEventType.STAGE_STATUS_CHANGED.value,
                db_now,
                stage_id=stage.stage_id,
                payload={
                    "stage_key": stage.stage_key,
                    "position": stage.position,
                    "previous_status": None,
                    "status": stage.status,
                    "task_id": None,
                },
            )
        self._event(
            connection,
            identifier,
            WorkflowEventType.STAGE_READY.value,
            db_now,
            stage_id=durable_stages[0].stage_id,
            payload={
                "stage_key": durable_stages[0].stage_key,
                "position": 1,
                "status": WorkflowStageStatus.READY.value,
            },
        )
        return self._projection_in_connection(connection, identifier)

    def _normalize_create_request(
        self,
        *,
        name: str,
        stages: list[WorkflowStageSpec],
        context_id: str | None,
        workflow_id: str | None,
    ) -> tuple[str, str | None, list[WorkflowStageSpec], str]:
        canonical_name = str(name or "").strip()
        if not canonical_name:
            raise ValueError("workflow name is required")
        if len(canonical_name) > 200:
            raise ValueError("workflow name must be at most 200 characters")
        canonical_context = str(context_id or "").strip() or None
        if canonical_context and len(canonical_context) > 128:
            raise ValueError("context_id must be at most 128 characters")
        specs = self._validate_specs(stages)
        identifier = str(workflow_id or "").strip() or new_workflow_id()
        if len(identifier) > 80:
            raise ValueError("workflow_id must be at most 80 characters")
        return canonical_name, canonical_context, specs, identifier

    # --------------------------------------------------------------- binding

    def bind_task(
        self, workflow_id: str, stage_key: str, task_id: str,
    ) -> WorkflowMutationResult:
        wid = self._required(workflow_id, "workflow_id", 80)
        key = self._required(stage_key, "stage_key", 64)
        tid = self._required(task_id, "task_id", 80)
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            workflow = self._require_workflow(connection, wid)
            if workflow.status in {
                WorkflowStatus.COMPLETED.value,
                WorkflowStatus.FAILED.value,
                WorkflowStatus.CANCELLED.value,
            }:
                raise WorkflowStateError("workflow is already terminal")
            stage = self.repo.get_stage_by_key_in_connection(connection, wid, key)
            if stage is None:
                raise WorkflowNotFoundError("workflow stage not found")
            if stage.task_id == tid:
                self._refresh_in_connection(connection, workflow, db_now)
                return WorkflowMutationResult(
                    self._projection_in_connection(connection, wid), replayed=True,
                )
            if stage.task_id:
                raise WorkflowStateError("workflow stage is already bound")
            if stage.status != WorkflowStageStatus.READY.value:
                raise WorkflowStateError("workflow stage is not ready")
            already = self.repo.get_stage_by_task_in_connection(connection, tid)
            if already is not None:
                raise WorkflowStateError("task is already bound to a workflow stage")
            task = connection.execute(
                """
                SELECT task_id, task_type, status, started_at, finished_at,
                       result_available, result_json, terminal_reason, timeout_reason
                FROM tasks WHERE task_id = ?
                """,
                (tid,),
            ).fetchone()
            if task is None:
                raise WorkflowNotFoundError("task not found")
            if stage.runtime and str(task["task_type"]) != stage.runtime:
                raise WorkflowStateError(
                    "task runtime does not match workflow stage runtime"
                )
            lineage = None
            if workflow.context_id or stage.agent_profile:
                lineage = connection.execute(
                    "SELECT context_id, agent_profile FROM task_lineage "
                    "WHERE child_task_id = ?",
                    (tid,),
                ).fetchone()
            if workflow.context_id:
                task_context = lineage["context_id"] if lineage else None
                if task_context != workflow.context_id:
                    raise WorkflowStateError(
                        "task context_id does not match workflow context_id"
                    )
            if stage.agent_profile:
                task_profile = lineage["agent_profile"] if lineage else None
                if task_profile != stage.agent_profile:
                    raise WorkflowStateError(
                        "task agent_profile does not match workflow stage agent_profile"
                    )

            approval = self.repo.get_approval_for_stage_in_connection(
                connection, stage.stage_id,
            )
            new_status, block_reason = self._stage_state_for_task(
                task, stage, approval,
            )
            started_at = (
                float(task["started_at"])
                if task["started_at"] is not None
                else db_now
            )
            finished_at = (
                float(task["finished_at"])
                if task["finished_at"] is not None
                and new_status in self._terminal_stage_statuses()
                else (db_now if new_status in self._terminal_stage_statuses() else None)
            )
            if not self.repo.update_stage(
                connection,
                stage.stage_id,
                status=new_status,
                updated_at=db_now,
                task_id=tid,
                bind_task=True,
                started_at=started_at,
                finished_at=finished_at,
                block_reason=block_reason,
                set_block_reason=True,
            ):
                raise WorkflowStateError("workflow stage binding failed")
            self._event(
                connection,
                wid,
                WorkflowEventType.STAGE_TASK_BOUND.value,
                db_now,
                stage_id=stage.stage_id,
                payload={
                    "stage_key": stage.stage_key,
                    "task_id": tid,
                    "task_status": str(task["status"]),
                },
            )
            self._stage_status_event(
                connection, wid, stage, new_status, db_now,
            )
            latest = self.repo.get_workflow_in_connection(connection, wid)
            assert latest is not None
            self._refresh_in_connection(connection, latest, db_now)
            return WorkflowMutationResult(
                self._projection_in_connection(connection, wid), replayed=False,
            )

    # --------------------------------------------------------------- refresh

    def refresh_workflow(self, workflow_id: str) -> dict[str, Any]:
        wid = self._required(workflow_id, "workflow_id", 80)
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            workflow = self._require_workflow(connection, wid)
            self._refresh_in_connection(connection, workflow, db_now)
            return self._projection_in_connection(connection, wid)

    def _refresh_in_connection(
        self,
        connection,
        workflow: Workflow,
        db_now: float,
    ) -> None:
        # Explicit Workflow cancellation is a control-plane terminal truth.
        # It never rewrites the bound Durable Task and refresh must not revive
        # cancelled Stages from a still-running task.
        if workflow.status == WorkflowStatus.CANCELLED.value:
            return
        stages = self.repo.list_stages_in_connection(
            connection, workflow.workflow_id,
        )
        changed = False
        refreshed: list[WorkflowStage] = []
        for stage in stages:
            target = stage.status
            block_reason = stage.block_reason
            finished_at: float | None = None
            if stage.task_id:
                task = connection.execute(
                    "SELECT task_id, status, started_at, finished_at, result_available, "
                    "result_json, terminal_reason, timeout_reason "
                    "FROM tasks WHERE task_id = ?",
                    (stage.task_id,),
                ).fetchone()
                if task is None:
                    target = WorkflowStageStatus.FAILED.value
                    block_reason = "bound_task_missing"
                    finished_at = db_now
                else:
                    approval = self.repo.get_approval_for_stage_in_connection(
                        connection, stage.stage_id,
                    )
                    target, block_reason = self._stage_state_for_task(
                        task, stage, approval,
                    )
                    if (
                        target in self._terminal_stage_statuses()
                        or target in {
                            WorkflowStageStatus.WAITING_APPROVAL.value,
                            WorkflowStageStatus.NEEDS_REVIEW.value,
                        }
                    ):
                        finished_at = (
                            float(task["finished_at"])
                            if task["finished_at"] is not None
                            else db_now
                        )
            elif stage.status in {
                WorkflowStageStatus.RUNNING.value,
                WorkflowStageStatus.WAITING_APPROVAL.value,
            }:
                target = WorkflowStageStatus.FAILED.value
                block_reason = "stage_task_binding_missing"
                finished_at = db_now

            if target != stage.status or block_reason != stage.block_reason:
                self.repo.update_stage(
                    connection,
                    stage.stage_id,
                    status=target,
                    updated_at=db_now,
                    finished_at=finished_at,
                    block_reason=block_reason,
                    set_block_reason=True,
                )
                self._stage_status_event(
                    connection, workflow.workflow_id, stage, target, db_now,
                )
                changed = True
                stage = WorkflowStage(
                    **{
                        **stage.__dict__,
                        "status": target,
                        "updated_at": db_now,
                        "finished_at": finished_at or stage.finished_at,
                        "block_reason": block_reason,
                    }
                )
            refreshed.append(stage)

        # Linear advancement: the first not-completed stage becomes ready only
        # after every earlier stage is completed or skipped.  No DAG and no
        # implicit backend dispatch are introduced in V1.2.
        prior_complete = True
        advanced: list[WorkflowStage] = []
        for stage in refreshed:
            if (
                prior_complete
                and stage.status == WorkflowStageStatus.PENDING.value
                and stage.task_id is None
            ):
                self.repo.update_stage(
                    connection,
                    stage.stage_id,
                    status=WorkflowStageStatus.READY.value,
                    updated_at=db_now,
                )
                self._event(
                    connection,
                    workflow.workflow_id,
                    WorkflowEventType.STAGE_READY.value,
                    db_now,
                    stage_id=stage.stage_id,
                    payload={
                        "stage_key": stage.stage_key,
                        "position": stage.position,
                        "status": WorkflowStageStatus.READY.value,
                    },
                )
                stage = WorkflowStage(
                    **{
                        **stage.__dict__,
                        "status": WorkflowStageStatus.READY.value,
                        "updated_at": db_now,
                    }
                )
                changed = True
            advanced.append(stage)
            prior_complete = prior_complete and stage.status in {
                WorkflowStageStatus.COMPLETED.value,
                WorkflowStageStatus.SKIPPED.value,
            }

        desired = self._workflow_status(advanced)
        current = self.repo.get_workflow_in_connection(
            connection, workflow.workflow_id,
        )
        if current is None:
            raise WorkflowNotFoundError("workflow not found")
        if changed or desired != current.status:
            if not self.repo.update_workflow_status(
                connection,
                workflow.workflow_id,
                status=desired,
                updated_at=db_now,
                expected_version=current.version,
            ):
                raise WorkflowStateError("workflow changed concurrently")
            if desired != current.status:
                self._event(
                    connection,
                    workflow.workflow_id,
                    WorkflowEventType.WORKFLOW_STATUS_CHANGED.value,
                    db_now,
                    payload={
                        "previous_status": current.status,
                        "status": desired,
                    },
                )

    def cancel_workflow(
        self,
        workflow_id: str,
        *,
        reason_code: str = "operator_cancelled",
    ) -> WorkflowMutationResult:
        """Explicitly cancel Workflow progression without mutating Tasks."""
        wid = self._required(workflow_id, "workflow_id", 80)
        reason = self._safe_code(reason_code or "operator_cancelled", "reason_code")
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            workflow = self._require_workflow(connection, wid)
            if workflow.status == WorkflowStatus.CANCELLED.value:
                return WorkflowMutationResult(
                    self._projection_in_connection(connection, wid), replayed=True,
                )
            if workflow.status in {
                WorkflowStatus.COMPLETED.value,
                WorkflowStatus.FAILED.value,
            }:
                raise WorkflowStateError("workflow is already terminal")
            stages = self.repo.list_stages_in_connection(connection, wid)
            for stage in stages:
                if stage.status in {
                    WorkflowStageStatus.COMPLETED.value,
                    WorkflowStageStatus.SKIPPED.value,
                    WorkflowStageStatus.FAILED.value,
                    WorkflowStageStatus.CANCELLED.value,
                }:
                    continue
                self.repo.update_stage(
                    connection,
                    stage.stage_id,
                    status=WorkflowStageStatus.CANCELLED.value,
                    updated_at=db_now,
                    finished_at=db_now,
                    block_reason="workflow_cancelled",
                    set_block_reason=True,
                )
                self._stage_status_event(
                    connection,
                    wid,
                    stage,
                    WorkflowStageStatus.CANCELLED.value,
                    db_now,
                )
            current = self.repo.get_workflow_in_connection(connection, wid)
            assert current is not None
            if not self.repo.update_workflow_status(
                connection,
                wid,
                status=WorkflowStatus.CANCELLED.value,
                updated_at=db_now,
                expected_version=current.version,
            ):
                raise WorkflowStateError("workflow changed concurrently")
            self._event(
                connection,
                wid,
                WorkflowEventType.WORKFLOW_STATUS_CHANGED.value,
                db_now,
                payload={
                    "previous_status": current.status,
                    "status": WorkflowStatus.CANCELLED.value,
                    "reason_code": reason,
                },
            )
            return WorkflowMutationResult(
                self._projection_in_connection(connection, wid), replayed=False,
            )

    # -------------------------------------------------------------- approval

    def record_approval(
        self,
        workflow_id: str,
        stage_key: str,
        *,
        decision: str = "approved",
        actor: str = "operator",
        reason_code: str = "",
    ) -> WorkflowMutationResult:
        wid = self._required(workflow_id, "workflow_id", 80)
        key = self._required(stage_key, "stage_key", 64)
        canonical_decision = str(decision or "").strip().lower()
        if canonical_decision not in {
            ApprovalDecision.APPROVED.value,
            ApprovalDecision.REJECTED.value,
        }:
            raise ValueError("decision must be approved or rejected")
        canonical_actor = self._safe_code(actor or "operator", "actor")
        canonical_reason = (
            self._safe_code(reason_code, "reason_code") if reason_code else None
        )

        with self.db.immediate_fenced_transaction() as (connection, db_now):
            workflow = self._require_workflow(connection, wid)
            self._refresh_in_connection(connection, workflow, db_now)
            stage = self.repo.get_stage_by_key_in_connection(connection, wid, key)
            if stage is None:
                raise WorkflowNotFoundError("workflow stage not found")
            existing = self.repo.get_approval_for_stage_in_connection(
                connection, stage.stage_id,
            )
            if existing is not None:
                if existing.decision != canonical_decision:
                    raise WorkflowStateError(
                        "workflow stage already has a different approval decision"
                    )
                return WorkflowMutationResult(
                    self._projection_in_connection(connection, wid), replayed=True,
                )
            if not stage.approval_required:
                raise WorkflowStateError("workflow stage does not require approval")
            if stage.status != WorkflowStageStatus.WAITING_APPROVAL.value:
                raise WorkflowStateError("workflow stage is not waiting for approval")

            approval = WorkflowApproval(
                approval_id=new_approval_id(),
                workflow_id=wid,
                stage_id=stage.stage_id,
                decision=canonical_decision,
                actor=canonical_actor,
                reason_code=canonical_reason,
                decided_at=db_now,
            )
            self.repo.create_approval(connection, approval)
            target = (
                WorkflowStageStatus.COMPLETED.value
                if canonical_decision == ApprovalDecision.APPROVED.value
                else WorkflowStageStatus.FAILED.value
            )
            self.repo.update_stage(
                connection,
                stage.stage_id,
                status=target,
                updated_at=db_now,
                finished_at=db_now,
            )
            self._event(
                connection,
                wid,
                WorkflowEventType.APPROVAL_RECORDED.value,
                db_now,
                stage_id=stage.stage_id,
                payload={
                    "stage_key": stage.stage_key,
                    "decision": canonical_decision,
                    "actor": canonical_actor,
                    "reason_code": canonical_reason,
                },
            )
            self._stage_status_event(
                connection, wid, stage, target, db_now,
            )
            latest = self.repo.get_workflow_in_connection(connection, wid)
            assert latest is not None
            self._refresh_in_connection(connection, latest, db_now)
            return WorkflowMutationResult(
                self._projection_in_connection(connection, wid), replayed=False,
            )

    # ---------------------------------------------------------------- reads

    def get_workflow(
        self, workflow_id: str, *, refresh: bool = False,
    ) -> dict[str, Any]:
        if refresh:
            return self.refresh_workflow(workflow_id)
        wid = self._required(workflow_id, "workflow_id", 80)
        workflow = self.repo.get_workflow(wid)
        if workflow is None:
            raise WorkflowNotFoundError("workflow not found")
        with self.db.connect() as connection:
            return self._projection_in_connection(connection, wid)

    def list_workflows(self, status: str = "") -> list[dict[str, Any]]:
        canonical = str(status or "").strip().lower()
        if canonical and canonical not in {item.value for item in WorkflowStatus}:
            raise ValueError("unsupported workflow status")
        workflows = self.repo.list_workflows(canonical)
        result: list[dict[str, Any]] = []
        with self.db.connect() as connection:
            for workflow in workflows:
                result.append(
                    self._projection_in_connection(
                        connection, workflow.workflow_id,
                    )
                )
        return result

    # --------------------------------------------------------------- helpers

    def _projection_in_connection(
        self, connection, workflow_id: str,
    ) -> dict[str, Any]:
        workflow = self.repo.get_workflow_in_connection(connection, workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError("workflow not found")
        stages = self.repo.list_stages_in_connection(connection, workflow_id)
        approvals = {
            item.stage_id: item
            for item in [
                self.repo.get_approval_for_stage_in_connection(connection, stage.stage_id)
                for stage in stages
            ]
            if item is not None
        }
        return {
            "workflow_id": workflow.workflow_id,
            "name": workflow.name,
            "context_id": workflow.context_id,
            "status": workflow.status,
            "created_at": workflow.created_at,
            "updated_at": workflow.updated_at,
            "version": workflow.version,
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "stage_key": stage.stage_key,
                    "title": stage.title,
                    "position": stage.position,
                    "status": stage.status,
                    "approval_required": stage.approval_required,
                    "verification_required": stage.verification_required,
                    "completion_policy": stage.completion_policy,
                    "block_reason": stage.block_reason,
                    "runtime": stage.runtime,
                    "agent_profile": stage.agent_profile,
                    "task_id": stage.task_id,
                    "started_at": stage.started_at,
                    "finished_at": stage.finished_at,
                    "approval": (
                        {
                            "decision": approvals[stage.stage_id].decision,
                            "actor": approvals[stage.stage_id].actor,
                            "reason_code": approvals[stage.stage_id].reason_code,
                            "decided_at": approvals[stage.stage_id].decided_at,
                        }
                        if stage.stage_id in approvals
                        else None
                    ),
                }
                for stage in stages
            ],
        }

    @classmethod
    def _stage_state_for_task(
        cls,
        task,
        stage: WorkflowStage,
        approval: WorkflowApproval | None,
    ) -> tuple[str, str | None]:
        """Resolve one Stage from durable Task truth and its completion policy.

        ``legacy`` reproduces V1.2 behavior exactly.  ``plan_execution_v2``
        fails closed on missing/unreadable Result material and makes Runtime
        verification a real progression gate without mutating Task truth.
        """
        task_status = str(task["status"])
        if stage.completion_policy == WorkflowCompletionPolicy.LEGACY.value:
            return (
                cls._stage_status_for_task(
                    task_status, stage.approval_required, approval,
                ),
                None,
            )

        if task_status not in TERMINAL_STATUS_VALUES:
            return WorkflowStageStatus.RUNNING.value, None
        if task_status == TaskStatus.CANCELLED.value:
            return WorkflowStageStatus.CANCELLED.value, "task_cancelled"
        if task_status == TaskStatus.LOST.value:
            return WorkflowStageStatus.NEEDS_REVIEW.value, "task_lost"
        if task_status == TaskStatus.ORPHANED.value:
            return WorkflowStageStatus.NEEDS_REVIEW.value, "task_orphaned"
        if task_status != TaskStatus.COMPLETED.value:
            return WorkflowStageStatus.FAILED.value, "task_execution_failed"

        assessment = assess_task_result(
            task_id=str(task["task_id"]),
            execution_status=task_status,
            terminal_reason=task["terminal_reason"],
            timeout_reason=task["timeout_reason"],
            result_available=bool(task["result_available"]),
            result_json=task["result_json"],
        )
        work_product = assessment.get("work_product") or {}
        verification_status = str(
            work_product.get("verification_status") or "UNAVAILABLE"
        ).upper()
        work_product_status = str(work_product.get("status") or "unavailable")

        if work_product_status in {"unavailable", "unreadable"}:
            reason = (
                "result_unreadable"
                if work_product_status == "unreadable"
                else "result_unavailable"
            )
            return WorkflowStageStatus.NEEDS_REVIEW.value, reason

        if verification_status == "FAILED":
            return WorkflowStageStatus.FAILED.value, "verification_failed"
        if verification_status == "NEEDS_REVIEW":
            return (
                WorkflowStageStatus.NEEDS_REVIEW.value,
                "verification_needs_review",
            )

        if stage.verification_required and verification_status != "PASSED":
            return (
                WorkflowStageStatus.NEEDS_REVIEW.value,
                "verification_required_but_not_passed",
            )
        if verification_status not in {"PASSED", "NOT_REQUESTED"}:
            return (
                WorkflowStageStatus.NEEDS_REVIEW.value,
                "verification_unavailable",
            )

        if not stage.approval_required:
            return WorkflowStageStatus.COMPLETED.value, None
        if approval is None:
            return WorkflowStageStatus.WAITING_APPROVAL.value, None
        if approval.decision == ApprovalDecision.APPROVED.value:
            return WorkflowStageStatus.COMPLETED.value, None
        return WorkflowStageStatus.FAILED.value, "approval_rejected"

    @staticmethod
    def _stage_status_for_task(
        task_status: str,
        approval_required: bool,
        approval: WorkflowApproval | None,
    ) -> str:
        # Non-terminal Task states always remain a running Stage.  Handling
        # terminal states in one branch avoids duplicated/dead checks and
        # makes future terminal Task statuses fail closed.
        if task_status not in TERMINAL_STATUS_VALUES:
            return WorkflowStageStatus.RUNNING.value
        if task_status == TaskStatus.COMPLETED.value:
            if not approval_required:
                return WorkflowStageStatus.COMPLETED.value
            if approval is None:
                return WorkflowStageStatus.WAITING_APPROVAL.value
            return (
                WorkflowStageStatus.COMPLETED.value
                if approval.decision == ApprovalDecision.APPROVED.value
                else WorkflowStageStatus.FAILED.value
            )
        if task_status == TaskStatus.CANCELLED.value:
            return WorkflowStageStatus.CANCELLED.value
        return WorkflowStageStatus.FAILED.value

    @staticmethod
    def _workflow_status(stages: list[WorkflowStage]) -> str:
        statuses = [item.status for item in stages]
        if statuses and all(
            item in {
                WorkflowStageStatus.COMPLETED.value,
                WorkflowStageStatus.SKIPPED.value,
            }
            for item in statuses
        ):
            return WorkflowStatus.COMPLETED.value
        if WorkflowStageStatus.FAILED.value in statuses:
            return WorkflowStatus.FAILED.value
        if WorkflowStageStatus.CANCELLED.value in statuses:
            return WorkflowStatus.CANCELLED.value
        if (
            WorkflowStageStatus.WAITING_APPROVAL.value in statuses
            or WorkflowStageStatus.NEEDS_REVIEW.value in statuses
        ):
            return WorkflowStatus.BLOCKED.value
        return WorkflowStatus.ACTIVE.value

    @staticmethod
    def _terminal_stage_statuses() -> set[str]:
        return {
            WorkflowStageStatus.COMPLETED.value,
            WorkflowStageStatus.FAILED.value,
            WorkflowStageStatus.CANCELLED.value,
            WorkflowStageStatus.SKIPPED.value,
        }

    def _stage_status_event(
        self,
        connection,
        workflow_id: str,
        stage: WorkflowStage,
        status: str,
        now: float,
    ) -> None:
        self._event(
            connection,
            workflow_id,
            WorkflowEventType.STAGE_STATUS_CHANGED.value,
            now,
            stage_id=stage.stage_id,
            payload={
                "stage_key": stage.stage_key,
                "previous_status": stage.status,
                "status": status,
                "task_id": stage.task_id,
            },
        )

    def _event(
        self,
        connection,
        workflow_id: str,
        event_type: str,
        now: float,
        *,
        stage_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.repo.append_event(
            connection,
            WorkflowEvent(
                event_id=new_workflow_event_id(),
                workflow_id=workflow_id,
                stage_id=stage_id,
                event_type=event_type,
                event_time=now,
                payload_json=json.dumps(
                    payload or {}, ensure_ascii=False, sort_keys=True,
                ),
                visibility="public",
            ),
        )

    def _require_workflow(self, connection, workflow_id: str) -> Workflow:
        workflow = self.repo.get_workflow_in_connection(connection, workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError("workflow not found")
        return workflow

    @classmethod
    def _validate_specs(
        cls, stages: list[WorkflowStageSpec],
    ) -> list[WorkflowStageSpec]:
        if not isinstance(stages, list) or not stages:
            raise ValueError("workflow requires at least one stage")
        if len(stages) > cls.MAX_STAGES:
            raise ValueError(f"workflow supports at most {cls.MAX_STAGES} stages")
        result: list[WorkflowStageSpec] = []
        seen: set[str] = set()
        for item in stages:
            if not isinstance(item, WorkflowStageSpec):
                raise TypeError("stages must contain WorkflowStageSpec values")
            key = str(item.stage_key or "").strip()
            title = str(item.title or "").strip()
            if not _STAGE_KEY.fullmatch(key):
                raise ValueError(
                    "stage_key must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
                )
            if key in seen:
                raise ValueError("stage_key values must be unique")
            seen.add(key)
            if not title:
                raise ValueError("stage title is required")
            if len(title) > 200:
                raise ValueError("stage title must be at most 200 characters")
            runtime = str(item.runtime or "").strip().lower() or None
            if runtime and not _RUNTIME_NAME.fullmatch(runtime):
                raise ValueError("runtime contains unsupported characters")
            profile = str(item.agent_profile or "").strip() or None
            if profile and len(profile) > 80:
                raise ValueError("agent_profile must be at most 80 characters")
            policy = str(item.completion_policy or "legacy").strip().lower()
            if policy not in {item.value for item in WorkflowCompletionPolicy}:
                raise ValueError("unsupported workflow completion_policy")
            result.append(
                WorkflowStageSpec(
                    stage_key=key,
                    title=title,
                    approval_required=bool(item.approval_required),
                    verification_required=bool(item.verification_required),
                    completion_policy=policy,
                    runtime=runtime,
                    agent_profile=profile,
                )
            )
        return result

    @staticmethod
    def _required(value: str, field: str, maximum: int) -> str:
        canonical = str(value or "").strip()
        if not canonical:
            raise ValueError(f"{field} is required")
        if len(canonical) > maximum:
            raise ValueError(f"{field} must be at most {maximum} characters")
        return canonical

    @staticmethod
    def _safe_code(value: str, field: str) -> str:
        canonical = str(value or "").strip()
        if not _SAFE_CODE.fullmatch(canonical):
            raise ValueError(f"{field} contains unsupported characters")
        return canonical
