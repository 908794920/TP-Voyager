"""Durable parent/child metadata for generic sub-agent tasks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskLineage:
    child_task_id: str
    parent_task_id: str | None
    root_task_id: str
    context_id: str | None = None
    agent_profile: str | None = None
    execution_mode: str = "background"
    created_at: float = 0.0
