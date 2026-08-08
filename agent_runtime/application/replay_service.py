"""Read-only audit replay projections for task and workflow event streams.

SQLite task rows remain the source of truth.  Replay is a diagnostic reducer:
it never rewrites state and it reports historical payload gaps explicitly.
New V1.2 status-change events include a content-free ``status`` field, while
older events may only prove that some transition happened.
"""

from __future__ import annotations

import json
from typing import Any

from agent_runtime.domain.enums import (
    EventType,
    TERMINAL_STATUS_VALUES,
    TaskStatus,
    WorkflowEventType,
)
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.event_repository import EventRepository
from agent_runtime.persistence.task_repository import TaskRepository
from agent_runtime.persistence.workflow_repository import WorkflowRepository


class ReplayNotFoundError(RuntimeError):
    pass


class ReplayService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.tasks = TaskRepository(db)
        self.events = EventRepository(db)
        self.workflows = WorkflowRepository(db)

    def replay_task(self, task_id: str) -> dict[str, Any]:
        canonical = str(task_id or "").strip()
        if not canonical:
            raise ValueError("task_id is required")
        task = self.tasks.get_by_id(canonical)
        if task is None:
            raise ReplayNotFoundError("task not found")
        events = self.events.get_events(canonical)
        status: str | None = None
        result_available = False
        cancel_requested = False
        cancel_confirmed = False
        activity_count = 0
        anomalies: list[str] = []
        timeline: list[dict[str, Any]] = []
        last_seq = 0
        terminal_seen = False
        known_statuses = {item.value for item in TaskStatus}

        for event in events:
            if event.seq is None or event.seq <= last_seq:
                anomalies.append("event_sequence_not_strictly_increasing")
            if event.seq is not None:
                last_seq = max(last_seq, event.seq)
            payload: dict[str, Any] = {}
            try:
                decoded = json.loads(event.payload_json or "{}")
                if isinstance(decoded, dict):
                    payload = decoded
                else:
                    anomalies.append(f"event_{event.seq}_payload_not_object")
            except (TypeError, ValueError):
                anomalies.append(f"event_{event.seq}_payload_invalid_json")

            next_status = status
            event_type = event.event_type
            if event_type == EventType.TASK_CREATED.value:
                candidate = str(payload.get("status") or TaskStatus.QUEUED.value)
                next_status = candidate if candidate in known_statuses else TaskStatus.QUEUED.value
            elif event_type == EventType.TASK_STARTED.value:
                next_status = TaskStatus.RUNNING.value
            elif event_type == EventType.TASK_STATUS_CHANGED.value:
                candidate = str(payload.get("status") or "")
                if candidate in known_statuses:
                    next_status = candidate
                else:
                    anomalies.append(
                        f"event_{event.seq}_status_change_missing_status"
                    )
            elif event_type == EventType.CANCEL_REQUESTED.value:
                cancel_requested = True
                next_status = TaskStatus.CANCELLING.value
            elif event_type == EventType.CANCEL_CONFIRMED.value:
                cancel_confirmed = True
                next_status = TaskStatus.CANCELLED.value
            elif event_type == EventType.RESULT_AVAILABLE.value:
                result_available = True
            elif event_type == EventType.TASK_COMPLETED.value:
                next_status = TaskStatus.COMPLETED.value
            elif event_type == EventType.TASK_FAILED.value:
                next_status = TaskStatus.FAILED.value
            elif event_type == EventType.TASK_LOST.value:
                next_status = TaskStatus.LOST.value
            elif event_type == EventType.TASK_ORPHANED.value:
                next_status = TaskStatus.ORPHANED.value
            elif event_type == EventType.ACTIVITY_OBSERVED.value:
                activity_count += 1

            if (
                terminal_seen
                and next_status != status
                and next_status not in TERMINAL_STATUS_VALUES
            ):
                anomalies.append(f"event_{event.seq}_transition_after_terminal")
            status = next_status
            terminal_seen = status in TERMINAL_STATUS_VALUES if status else terminal_seen
            timeline.append(
                {
                    "seq": event.seq,
                    "event_type": event_type,
                    "event_time": event.event_time,
                    "visibility": event.visibility,
                    "status_after": status,
                }
            )

        status_match = status == task.status
        result_match = result_available == bool(task.result_available)
        if not status_match:
            anomalies.append("replayed_status_differs_from_durable_status")
        if not result_match:
            anomalies.append("replayed_result_availability_differs_from_durable_state")
        return {
            "task_id": canonical,
            "event_count": len(events),
            "last_seq": last_seq or None,
            "projected": {
                "status": status,
                "result_available": result_available,
                "cancel_requested": cancel_requested,
                "cancel_confirmed": cancel_confirmed,
                "activity_count": activity_count,
            },
            "durable": {
                "status": task.status,
                "result_available": bool(task.result_available),
                "version": task.version,
            },
            "status_match": status_match,
            "result_available_match": result_match,
            "replay_complete": not any(
                "missing_status" in item or "invalid_json" in item
                for item in anomalies
            ),
            "integrity_ok": not anomalies,
            "anomalies": list(dict.fromkeys(anomalies)),
            "timeline": timeline,
        }

    def replay_workflow(self, workflow_id: str) -> dict[str, Any]:
        canonical = str(workflow_id or "").strip()
        if not canonical:
            raise ValueError("workflow_id is required")
        workflow = self.workflows.get_workflow(canonical)
        if workflow is None:
            raise ReplayNotFoundError("workflow not found")
        events = self.workflows.list_events(canonical)
        durable_stages = self.workflows.list_stages(canonical)
        durable_approvals = self.workflows.list_approvals(canonical)

        status: str | None = None
        stage_state: dict[str, dict[str, Any]] = {}
        approvals: dict[str, str] = {}
        anomalies: list[str] = []
        timeline: list[dict[str, Any]] = []
        last_seq = 0
        for event in events:
            if event.seq is None or event.seq <= last_seq:
                anomalies.append("workflow_event_sequence_not_strictly_increasing")
            if event.seq is not None:
                last_seq = max(last_seq, event.seq)
            try:
                payload = json.loads(event.payload_json or "{}")
                if not isinstance(payload, dict):
                    payload = {}
                    anomalies.append(f"workflow_event_{event.seq}_payload_not_object")
            except (TypeError, ValueError):
                payload = {}
                anomalies.append(f"workflow_event_{event.seq}_payload_invalid_json")

            if event.event_type == WorkflowEventType.WORKFLOW_CREATED.value:
                status = str(payload.get("status") or "active")
            elif event.event_type == WorkflowEventType.WORKFLOW_STATUS_CHANGED.value:
                candidate = str(payload.get("status") or "")
                if candidate:
                    status = candidate
                else:
                    anomalies.append(
                        f"workflow_event_{event.seq}_status_missing"
                    )
            elif event.event_type == WorkflowEventType.STAGE_READY.value:
                if event.stage_id:
                    stage_state.setdefault(event.stage_id, {})["status"] = "ready"
                    stage_state[event.stage_id]["stage_key"] = payload.get("stage_key")
            elif event.event_type == WorkflowEventType.STAGE_TASK_BOUND.value:
                if event.stage_id:
                    stage_state.setdefault(event.stage_id, {})["task_id"] = payload.get("task_id")
                    stage_state[event.stage_id]["stage_key"] = payload.get("stage_key")
            elif event.event_type == WorkflowEventType.STAGE_STATUS_CHANGED.value:
                if event.stage_id:
                    candidate = str(payload.get("status") or "")
                    if candidate:
                        stage_state.setdefault(event.stage_id, {})["status"] = candidate
                        stage_state[event.stage_id]["stage_key"] = payload.get("stage_key")
                    else:
                        anomalies.append(
                            f"workflow_event_{event.seq}_stage_status_missing"
                        )
            elif event.event_type == WorkflowEventType.APPROVAL_RECORDED.value:
                if event.stage_id:
                    decision = str(payload.get("decision") or "")
                    if decision:
                        approvals[event.stage_id] = decision
                    else:
                        anomalies.append(
                            f"workflow_event_{event.seq}_approval_decision_missing"
                        )
            timeline.append(
                {
                    "seq": event.seq,
                    "event_type": event.event_type,
                    "event_time": event.event_time,
                    "stage_id": event.stage_id,
                    "workflow_status_after": status,
                }
            )

        durable_stage_map = {item.stage_id: item for item in durable_stages}
        for stage_id, stage in durable_stage_map.items():
            replayed = stage_state.get(stage_id, {})
            if replayed.get("status") != stage.status:
                anomalies.append(f"stage_{stage.stage_key}_status_mismatch")
            if replayed.get("task_id") != stage.task_id:
                # Unbound stages have no binding event and therefore match None.
                anomalies.append(f"stage_{stage.stage_key}_task_binding_mismatch")
        durable_approval_map = {
            item.stage_id: item.decision for item in durable_approvals
        }
        if approvals != durable_approval_map:
            anomalies.append("workflow_approval_projection_mismatch")
        if status != workflow.status:
            anomalies.append("workflow_status_projection_mismatch")

        return {
            "workflow_id": canonical,
            "event_count": len(events),
            "last_seq": last_seq or None,
            "projected_status": status,
            "durable_status": workflow.status,
            "status_match": status == workflow.status,
            "stage_count": len(durable_stages),
            "approval_count": len(durable_approvals),
            "integrity_ok": not anomalies,
            "anomalies": list(dict.fromkeys(anomalies)),
            "timeline": timeline,
        }
