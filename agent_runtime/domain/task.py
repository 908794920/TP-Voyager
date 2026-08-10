"""Durable task aggregate (persistence-oriented, no runtime handles)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Task:
    """Durable task state stored in SQLite.

    This object deliberately holds no Thread / Condition / Queue / HTTP handles:
    those live only in the in-process handle cache.  The prompt itself is never
    persisted — it exists only on the handle.  ``result_json`` stores the final
    Result payload (the explicit Result API contract) once available.
    """

    task_id: str
    task_type: str
    status: str
    route: str
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested_at: float | None = None
    cancel_confirmed_at: float | None = None
    session_id: str | None = None
    current_attempt_id: str | None = None
    result_available: bool = False
    result_json: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    version: int = 1
    terminal_reason: str | None = None
    cancel_scope: str | None = None
    cancel_initiator: str | None = None
    timeout_reason: str | None = None
    # PR3 reconciliation bookkeeping: when the bridge could not determine
    # the backend truth (LOST) or found a live host it cannot rebind
    # (ORPHANED), the moment is recorded here.  Never auto-converted.
    lost_at: float | None = None
    orphaned_at: float | None = None
    # v1.0.5 provenance only; these fields never encode workflow state.
    run_id: str | None = None
    step_key: str | None = None

    def has_result(self) -> bool:
        return self.result_available and self.result_json is not None

    def clone_with_version(self, version: int) -> "Task":
        values = self.__dict__.copy()
        values["version"] = version
        return Task(**values)
