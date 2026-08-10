"""In-process execution handles.

These objects mirror durable state only for active process resources. SQLite
remains the source of truth; nothing in this module is persisted directly.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

@dataclass
class TaskState:
    task_id: str
    prompt: str
    cwd: str
    runtime: str = ""
    model: str = ""
    reasoning_effort: str = ""
    resume_session_id: str = ""
    resumed: bool = False
    state: str = "queued"
    session_id: str | None = None
    answer: str = ""
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested: bool = False
    # Public, content-free observability.  These fields deliberately never
    # contain prompt/answer text, paths, credentials, token counts, or model
    # reasoning data.
    route: str = ""
    request_ref: str | None = None
    first_prompt_accepted_at: float | None = None
    terminal_reason: str | None = None
    activity: list[dict[str, Any]] = field(default_factory=list)
    idempotency_key: str = ""
    # Safe request fingerprint for idempotency conflict detection.  Computed
    # from non-sensitive routing fields (cwd, model, resume/session routing,
    # reasoning effort and policy metadata) — never from prompt text.
    request_fingerprint: str = ""
    # Cancel tracking: cancel_requested = caller asked; cancel_confirmed = the
    # backend/process acknowledged termination; cancel_scope = what was cancelled.
    cancel_confirmed: bool = False
    cancel_scope: str = ""  # Crew execution/session scope
    cancel_initiator: str = ""  # "user" / "diagnostic"
    cancel_requested_at: float | None = None
    cancel_confirmed_at: float | None = None
    idle_timeout_seconds: float = 180.0
    max_task_duration_seconds: float = 1800.0
    first_activity_at: float | None = None
    last_activity_at: float | None = None
    last_activity_kind: str | None = None
    event_count: int = 0
    timeout_reason: str | None = None
    run_id: str | None = None
    step_key: str | None = None
    parent_task_id: str | None = None
    root_task_id: str | None = None
    context_id: str | None = None
    agent_profile: str | None = None
    execution_mode: str = "background"
    verification_plan: dict[str, Any] = field(default_factory=dict)
    workspace_baseline: dict[str, Any] = field(default_factory=dict)
    # TP-Voyager patch mode uses an isolated runtime-owned Git worktree.
    # These fields are routing/cleanup metadata only; Task status remains
    # durable in SQLite.
    source_cwd: str = ""
    workspace_mode: str = ""
    workspace_base_revision: str = ""
    patch_policy: dict[str, Any] = field(default_factory=dict)
    routing_metadata: dict[str, Any] = field(default_factory=dict)
    condition: threading.Condition = field(default_factory=threading.Condition)
    # Durable-runtime bookkeeping: ``persisted`` marks tasks created through
    # the SQLite path; ``version`` mirrors the optimistic-concurrency counter;
    # ``persist_error`` records an explicit durability failure for diagnostics.
    persisted: bool = False
    version: int = 1
    persist_error: str | None = None
    # Runtime session id (never the backend vendor session id).
    runtime_session_id: str | None = None
    # Backend-side session id owned by the selected Crew adapter.
    backend_session_id: str | None = None
    # Backend cancel function (captured from BackendExecution).
    backend_cancel: Any = None
    current_attempt_id: str | None = None
    # Execution finalization markers: set via ``callbacks.on_result`` once
    # the backend produced its final result but the runtime has not yet
    # committed the terminal state.  the public cancel path rejects cancels
    # while these are set (closes the finalization window).
    execution_finished: bool = False
    finalizing: bool = False
    # Set only when the cancel transport was actually sent successfully
    # (BackendCancelResult.ok and transport_requested).  A failed first
    # attempt leaves this False so the public cancel path may retry the
    # transport; a successful send makes later calls pure replays.
    cancel_transport_requested: bool = False
    # PR3 lease/fencing: the session lease this worker acquired (None for
    # legacy in-process tasks).  Terminal writes are refused when the
    # lease is lost (reconciliation took over).
    lease: Any = None
    # PR3.4: serializes lease acquire/publish with cancel ownership reads,
    # so an immediate cancel never sees a half-published lease; and marks
    # whether the worker finished its acquire attempt (success or not).
    ownership_lock: threading.RLock = field(default_factory=threading.RLock)
    lease_acquire_finished: bool = False
    # PR3.1: durable result availability (reconciled "completed" without a
    # recoverable Result payload stays False so the Result API refuses it).
    result_available: bool = False
    # Persisted Result JSON failed closed parsing (invalid JSON / unknown
    # schema).  Kept private so status/list/wait projections remain unchanged.
    result_parse_error: bool = False
    # PR3 reconciliation bookkeeping (durable projection).
    lost_at: float | None = None
    orphaned_at: float | None = None


TASKS: dict[str, TaskState] = {}
"""Handle cache (NOT the source of truth).

Holds only run handles (thread / condition / queue / backend connection) plus
an in-process mirror of durable fields.  Task status, idempotency, session
relations, cancellation confirmation, and results live in SQLite; readers
project through ``_load_task``.
"""
TASKS_LOCK = threading.Lock()
IDEMPOTENCY_TASKS: dict[str, str] = {}
"""Process-local idempotency mirror; the durable binding lives in SQLite."""
CONTINUATION_SESSION_LOCKS: dict[str, threading.Lock] = {}
CONTINUATION_SESSION_LOCKS_LOCK = threading.Lock()
