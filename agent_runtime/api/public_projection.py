"""Stable public projections for MCP callers.

This module is intentionally content-free: prompts, answers, backend session
identifiers, command bodies and local absolute paths must never leak through
status/list/wait projections.
"""
from __future__ import annotations

from typing import Any

from agent_runtime.runtime.handles import TaskState

def result_summary(task: TaskState) -> dict[str, Any] | None:
    """Expose terminal transport facts without returning model content/data."""
    if not isinstance(task.result, dict):
        return None
    result = task.result
    summary = {
        "backend": result.get("backend") or task.route or None,
        "stop_reason": result.get("stopReason") or task.terminal_reason,
        "reasoning_effort_applied": result.get("reasoning_effort_applied"),
    }
    verification = result.get("verification")
    if isinstance(verification, dict):
        summary["verification"] = {
            "status": verification.get("status"),
            "summary": verification.get("summary"),
        }
    observability = result.get("observability")
    if isinstance(observability, dict):
        # Deliberately expose only content-free transport shape. Token values,
        # unknown field names, prompts, answers, and thoughts stay private.
        summary["stream"] = {
            key: observability.get(key)
            for key in (
                "event_count", "status_counts", "type_counts", "has_usage",
                "has_token", "has_thought", "has_reasoning", "thought_event_count",
                "has_stream_chunk", "has_final_content",
                "usage_without_recognized_tokens",
            )
            if key in observability
        }
        if isinstance(observability.get("sse_diagnostics"), dict):
            summary["sse_diagnostics"] = {
                key: int(value)
                for key, value in observability["sse_diagnostics"].items()
                if key in {
                    "raw_line_count", "sse_frame_count", "parsed_event_count",
                    "dropped_event_count", "ignored_event_count",
                } and isinstance(value, int)
            }
    return {key: value for key, value in summary.items() if value is not None}


def safe_public_error(error: str | None, terminal_reason: str | None) -> str | None:
    """Return a diagnostic category without exposing backend exception content."""
    if not error:
        return None
    category = terminal_reason or "runtime_error"
    return f"{category}; inspect the local TP-Voyager activity log using task_id"


def public_task(task: TaskState) -> dict[str, Any]:
    """Return the stable, safe task-state contract for MCP callers."""
    return {
        "task_id": task.task_id,
        "state": task.state,
        "runtime": task.runtime,
        "route": task.route or None,
        "model": task.model or None,
        "agent_profile": task.agent_profile,
        "parent_task_id": task.parent_task_id,
        "root_task_id": task.root_task_id or task.task_id,
        "context_id": task.context_id,
        "execution_mode": task.execution_mode,
        "resumed": task.resumed,
        "started_at": task.started_at,
        "first_prompt_accepted_at": task.first_prompt_accepted_at,
        "updated_at": task.updated_at,
        "finished_at": task.finished_at,
        "activity": list(task.activity),
        "terminal_reason": task.terminal_reason,
        "cancel_requested": task.cancel_requested,
        "cancel_confirmed": task.cancel_confirmed,
        "cancel_scope": task.cancel_scope or None,
        "cancel_initiator": task.cancel_initiator or None,
        "cancellation": {
            "requested_at": task.cancel_requested_at,
            "scope": task.cancel_scope or None,
            "initiator": task.cancel_initiator or None,
            "confirmed_at": task.cancel_confirmed_at,
            "confirmed": task.cancel_confirmed,
        },
        "first_activity_at": task.first_activity_at,
        "last_activity_at": task.last_activity_at,
        "last_activity_kind": task.last_activity_kind,
        "event_count": task.event_count,
        "idle_timeout_seconds": task.idle_timeout_seconds,
        "max_task_duration_seconds": task.max_task_duration_seconds,
        "timeout_reason": task.timeout_reason,
        "lost_at": task.lost_at,
        "orphaned_at": task.orphaned_at,
        "result_summary": result_summary(task) if task.state == "completed" else None,
        "error": safe_public_error(task.error, task.terminal_reason),
    }


