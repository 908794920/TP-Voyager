"""Compact, content-free TP-Voyager progress projection.

A Voyage is intentionally a projection over existing Durable Task truth in
this baseline.  T2 adds no Voyage state machine or persistence table.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from agent_runtime.domain.enums import TERMINAL_STATUS_VALUES


_TARGET_CREW = frozenset({"codebuddy", "qoder"})
_ATTENTION_STATES = frozenset({"failed", "lost", "orphaned"})


class VoyageOverviewService:
    def __init__(self, task_service: Any) -> None:
        self._task_service = task_service

    def overview(self, *, limit: int = 5) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 20))
        tasks = list(self._task_service.list_tasks())
        tasks.sort(key=lambda task: task.updated_at or task.created_at, reverse=True)
        counts = Counter(str(task.status) for task in tasks)
        active = [task for task in tasks if str(task.status) not in TERMINAL_STATUS_VALUES]
        attention = [task for task in tasks if str(task.status) in _ATTENTION_STATES]
        completed = [task for task in tasks if str(task.status) == "completed"]

        return {
            "schema": "tp-voyager.overview/v1",
            "scope": "runtime_projection",
            "total_tasks": len(tasks),
            "status_counts": dict(sorted(counts.items())),
            "active_count": len(active),
            "completed_count": len(completed),
            "attention_count": len(attention),
            "captain_action_required": bool(attention),
            "active": [self._task_ref(task) for task in active[:bounded_limit]],
            "attention": [self._task_ref(task, attention=True) for task in attention[:bounded_limit]],
            "recent_completed": [self._task_ref(task) for task in completed[:bounded_limit]],
            "content_included": False,
        }

    @staticmethod
    def _task_ref(task: Any, *, attention: bool = False) -> dict[str, Any]:
        crew = str(task.task_type or "").strip().lower()
        result = {
            "task_id": task.task_id,
            "crew": crew or None,
            "state": str(task.status),
            "updated_at": task.updated_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "result_available": bool(task.result_available),
            "target_crew": crew in _TARGET_CREW,
        }
        if crew and crew not in _TARGET_CREW:
            result["legacy_or_unknown_crew"] = True
        if attention:
            result["reason_code"] = (
                "TASK_NEEDS_REVIEW" if str(task.status) in {"lost", "orphaned"} else "TASK_FAILED"
            )
        return result
