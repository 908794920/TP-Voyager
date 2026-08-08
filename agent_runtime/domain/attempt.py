"""Minimal attempt model (PR1: exactly one attempt per task, no retry logic)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Attempt:
    """One dispatch attempt of a task against a backend route.

    PR1 only establishes the minimal table shape.  Retry scheduling, lease,
    fencing, and cross-backend retry are explicitly out of scope.
    """

    attempt_id: str
    task_id: str
    attempt_no: int
    backend: str
    route: str
    status: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error_code: str | None = None
    error_message: str | None = None
