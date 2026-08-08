"""Append-only audit event model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskEvent:
    """One immutable audit event.  Rows are never updated after insert.

    Payloads must be redacted: no full prompts, answers, thoughts, secrets, or
    raw tool output.  The final answer stays under the explicit Result API and
    never enters public status responses through events.
    """

    event_id: str
    task_id: str
    event_type: str
    event_time: float
    session_id: str | None = None
    attempt_id: str | None = None
    payload_json: str = "{}"
    visibility: str = "public"
    seq: int | None = None
