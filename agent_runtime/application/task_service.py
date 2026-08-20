"""Task service: composes repositories into atomic runtime transactions.

Every transaction follows the PR1 contract:

- task creation: claim idempotency key + create task + session + attempt +
  ``task_created`` event, committed before any real dispatch may happen;
- status change: update task row + append matching event in one transaction;
- result availability: persist Result + ``result_available`` event.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from agent_runtime.domain.artifact import Artifact
from agent_runtime.domain.attempt import Attempt
from agent_runtime.domain.enums import (
    BackendKind,
    EvidenceOrigin,
    EvidenceType,
    EventType,
    EventVisibility,
    TERMINAL_STATUS_VALUES,
    TaskStatus,
    TrustState,
)
from agent_runtime.domain.event import TaskEvent
from agent_runtime.domain.evidence import Evidence
from agent_runtime.domain.lineage import TaskLineage
from agent_runtime.domain.ids import (
    new_attempt_id,
    new_event_id,
)
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.domain.timeutil import now_epoch
from agent_runtime.domain.run_control import RunControlSpec
from agent_runtime.domain.structured_result import (
    RESULT_SCHEMA,
    StructuredResult,
)
from agent_runtime.persistence.artifact_repository import ArtifactRepository
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.errors import (
    LeaseLostError,
    RuntimePersistenceError,
    TaskAlreadyTerminalError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from agent_runtime.persistence.event_repository import EventRepository
from agent_runtime.persistence.evidence_repository import EvidenceRepository
from agent_runtime.persistence.lineage_repository import LineageRepository


_ACTIVITY_DETAIL_KEYS = (
    "tool",
    "action",
    "path",
    "phase",
    "status",
    "reason",
    "summary",
    "provider",
    "source",
    "currency",
    "input_tokens",
    "output_tokens",
    "duration_ms",
    "turns",
    "files_changed",
)
from agent_runtime.persistence.idempotency_repository import (
    ClaimOutcome,
    IdempotencyRepository,
)
from agent_runtime.persistence.session_repository import SessionRepository
from agent_runtime.persistence.run_control_repository import RunControlError, RunControlRepository
from agent_runtime.persistence.task_repository import (
    TERMINAL_NOT_IN_PLACEHOLDERS,
    TaskRepository,
    _lease_fence_clause,
)
from agent_runtime.runtime.lease import LeaseInfo


@dataclass
class CancelRequestResult:
    """Outcome of an atomic cancel request transaction."""
    created: bool  # True = first request, False = already requested (replayed)
    version: int   # Durable version after the transaction
    status: str    # Durable status after the transaction


# Reconciliation write outcomes: the truth the writer actually achieved.
WRITE_WRITTEN = "written"
WRITE_CONFLICT = "conflict"
WRITE_ALREADY_TERMINAL = "already_terminal"
WRITE_LEASE_LOST = "lease_lost"


def classify_fenced_write_failure(
    connection,
    task_id: str,
    *,
    expected_version: int,
    lease: LeaseInfo | None,
    now: float,
) -> str:
    """Classify why a fenced write was refused (rowcount == 0).

    Returns one of ``WRITE_LEASE_LOST`` / ``WRITE_ALREADY_TERMINAL`` /
    ``WRITE_CONFLICT`` so callers can distinguish fencing failures from
    plain version conflicts instead of folding them into one generic
    persistence error.
    """
    if lease is not None:
        row = connection.execute(
            "SELECT owner_instance_id, owner_generation, lease_expires_at "
            "FROM sessions WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if (
            row is None
            or row["owner_instance_id"] != lease.instance_id
            or int(row["owner_generation"] or 0) != lease.generation
            or row["lease_expires_at"] is None
            or float(row["lease_expires_at"]) <= now
        ):
            return WRITE_LEASE_LOST
    row = connection.execute(
        "SELECT status, version FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return WRITE_CONFLICT
    if row["status"] in TERMINAL_STATUS_VALUES:
        return WRITE_ALREADY_TERMINAL
    if int(row["version"]) != expected_version:
        return WRITE_CONFLICT
    return WRITE_CONFLICT

# Safe routing metadata keys persisted per session (never the prompt).
SAFE_METADATA_KEYS = (
    "cwd",
    "model",
    "identity",
    "reasoning_effort",
    "context_window_tokens",
    "resume_session_id",
    "review_target",
    "resume_review",
    "idle_timeout_seconds",
    "max_task_duration_seconds",
    "runtime",
    "agent_profile",
    "parent_task_id",
    "root_task_id",
    "context_id",
    "execution_mode",
    "verification_plan",
    "workspace_baseline",
    "source_cwd",
    "workspace_mode",
    "workspace_base_revision",
    "patch_policy",
    "routing_metadata",
)


@dataclass
class CreateTaskResult:
    outcome: str  # "created" | "replayed" | "conflict"
    task_id: str | None = None
    # PR4-B1.1: the attempt bound to this creation.  "created" returns the
    # attempt written in the same transaction; "replayed" returns the durable
    # task's current_attempt_id so callers never fall back to an empty id.
    attempt_id: str | None = None
    error: str | None = None
    reason_code: str | None = None


def build_session_metadata(metadata: dict[str, Any]) -> str:
    """Serialize only allow-listed safe routing metadata."""
    safe = {
        key: metadata.get(key)
        for key in SAFE_METADATA_KEYS
        if metadata.get(key) not in (None, "")
    }
    return json.dumps(safe, ensure_ascii=False, sort_keys=True)


def parse_session_metadata(metadata_json: str) -> dict[str, Any]:
    try:
        data = json.loads(metadata_json or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class TaskService:
    """Stateless service; repositories are re-created per call (no shared
    connection, safe across threads)."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.tasks = TaskRepository(db)
        self.sessions = SessionRepository(db)
        self.events = EventRepository(db)
        self.idempotency = IdempotencyRepository(db)
        self.evidence = EvidenceRepository(db)
        self.artifacts = ArtifactRepository(db)
        self.lineage = LineageRepository(db)
        self.run_controls = RunControlRepository()

    # ------------------------------------------------------------------ create

    def create_task(
        self,
        *,
        task: Task,
        session: Session,
        metadata: dict[str, Any],
        idempotency_key: str,
        request_fingerprint: str,
        lineage: TaskLineage | None = None,
        run_control: RunControlSpec | None = None,
        requested_runtime_seconds: float = 0.0,
        now: float | None = None,
    ) -> CreateTaskResult:
        """Atomically claim the key and persist task/session/attempt/event.

        Returns ``replayed``/``conflict`` without persisting anything when the
        key is already bound.  On ``created`` the transaction is committed and
        the caller may dispatch the real prompt.
        """
        now = now if now is not None else now_epoch()
        attempt = Attempt(
            attempt_id=new_attempt_id(),
            task_id=task.task_id,
            attempt_no=1,
            backend=session.backend,
            route=task.route,
            status=TaskStatus.QUEUED.value,
            created_at=now,
        )
        # PR4-B1: tasks.current_attempt_id is the durable truth and must be
        # written in the SAME transaction that inserts the attempt, so the
        # task row never points at a missing attempt (PR4 design ch.7.1).
        durable_task = replace(task, current_attempt_id=attempt.attempt_id)
        session = Session(
            session_id=session.session_id,
            task_id=session.task_id,
            backend=session.backend,
            route=session.route,
            created_at=session.created_at,
            updated_at=session.updated_at,
            backend_session_id=session.backend_session_id,
            metadata_json=build_session_metadata(metadata),
        )
        durable_lineage = lineage or TaskLineage(
            child_task_id=task.task_id,
            parent_task_id=None,
            root_task_id=task.task_id,
            context_id=None,
            agent_profile=None,
            execution_mode="background",
            created_at=now,
        )
        if durable_lineage.child_task_id != task.task_id:
            raise ValueError("lineage child_task_id must match task")
        if durable_lineage.execution_mode not in {"background", "detached"}:
            raise ValueError("unsupported execution_mode")
        created_event = TaskEvent(
            event_id=new_event_id(),
            task_id=task.task_id,
            event_type=EventType.TASK_CREATED.value,
            event_time=now,
            visibility=EventVisibility.PUBLIC.value,
            payload_json=json.dumps(
                {
                    "runtime": task.task_type,
                    "status": TaskStatus.QUEUED.value,
                    "parent_task_id": durable_lineage.parent_task_id,
                    "root_task_id": durable_lineage.root_task_id,
                    "execution_mode": durable_lineage.execution_mode,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        try:
            with self.db.immediate_transaction() as connection:
                if durable_task.run_id and durable_task.step_key:
                    existing_step = connection.execute(
                        "SELECT task_id FROM tasks WHERE run_id=? AND step_key=?",
                        (durable_task.run_id, durable_task.step_key),
                    ).fetchone()
                    if existing_step is not None:
                        existing_task_id = str(existing_step["task_id"])
                        existing_binding = (
                            connection.execute(
                                "SELECT request_fingerprint, task_id FROM idempotency WHERE idempotency_key=?",
                                (idempotency_key,),
                            ).fetchone()
                            if idempotency_key else None
                        )
                        if (
                            existing_binding is not None
                            and str(existing_binding["task_id"]) == existing_task_id
                            and str(existing_binding["request_fingerprint"]) == request_fingerprint
                        ):
                            raise _ReplayOrConflict(ClaimOutcome.REPLAY.value, existing_task_id)
                        raise _StepConflict(existing_task_id)
                # Task row first: the idempotency claim references it via FK, and a
                # REPLAY/CONFLICT outcome rolls the whole transaction back.
                self.tasks.create(connection, durable_task)
                claim = self.idempotency.claim(
                    connection,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    task_id=task.task_id,
                    created_at=now,
                )
                if claim.outcome in {ClaimOutcome.REPLAY, ClaimOutcome.CONFLICT}:
                    raise _ReplayOrConflict(claim.outcome.value, claim.task_id)
                if run_control is not None:
                    try:
                        self.run_controls.admit_current_task(
                            connection, run_control,
                            requested_runtime_seconds=requested_runtime_seconds,
                            now=now,
                        )
                    except RunControlError as exc:
                        raise _RunBudgetRejected(exc.code, exc.detail) from exc
                self.sessions.create(connection, session)
                self.tasks.create_attempt(connection, attempt)
                self.lineage.create(connection, durable_lineage)
                self.events.append(connection, created_event)
                if durable_lineage.parent_task_id:
                    self.events.append(
                        connection,
                        TaskEvent(
                            event_id=new_event_id(),
                            task_id=task.task_id,
                            event_type=EventType.TASK_CHILD_LINKED.value,
                            event_time=now,
                            attempt_id=attempt.attempt_id,
                            payload_json=json.dumps(
                                {
                                    "parent_task_id": durable_lineage.parent_task_id,
                                    "root_task_id": durable_lineage.root_task_id,
                                },
                                ensure_ascii=False, sort_keys=True,
                            ),
                        ),
                    )
        except _RunBudgetRejected as exc:
            return CreateTaskResult(outcome="budget_rejected", error=exc.detail, reason_code=exc.code)
        except _StepConflict as exc:
            return CreateTaskResult(
                outcome="step_conflict", task_id=exc.task_id,
                error="run_id + step_key is already bound to another durable Task",
                reason_code="STEP_IDEMPOTENCY_CONFLICT",
            )
        except _ReplayOrConflict as exc:
            result = _handle_replay_or_conflict(exc)
            if result.outcome == "replayed" and exc.task_id:
                # Replay compatibility: surface the durable current attempt
                # so the live handle and backend requests stay attempt-bound.
                durable = self.tasks.get_by_id(exc.task_id)
                if durable is not None:
                    result.attempt_id = durable.current_attempt_id
            return result
        return CreateTaskResult(
            outcome="created",
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
        )

    def resolve_idempotent(self, idempotency_key: str) -> tuple[str, str] | None:
        """Return (fingerprint, task_id) for an existing key, or None."""
        return self.idempotency.get_by_key(idempotency_key)

    # ------------------------------------------------------------------ read

    def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get_by_id(task_id)

    def get_task_by_run_step(self, run_id: str, step_key: str) -> Task | None:
        return self.tasks.get_by_run_step(run_id, step_key)

    def get_run_control(self, run_id: str):
        with self.db.immediate_transaction() as connection:
            return self.run_controls.get(connection, run_id, now=now_epoch())

    def list_tasks(self) -> list[Task]:
        return self.tasks.list_all()

    def get_session(self, task_id: str) -> Session | None:
        return self.sessions.get_by_task_id(task_id)

    def get_events(self, task_id: str) -> list[TaskEvent]:
        return self.events.get_events(task_id)

    def get_lineage(self, task_id: str) -> TaskLineage | None:
        return self.lineage.get(task_id)

    def list_children(self, task_id: str) -> list[TaskLineage]:
        return self.lineage.list_children(task_id)

    def list_tree(self, task_id: str) -> list[TaskLineage]:
        lineage = self.lineage.get(task_id)
        root_id = lineage.root_task_id if lineage else task_id
        return self.lineage.list_tree(root_id)

    # ------------------------------------------------------------ PR4 reads

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        return self.evidence.get(evidence_id)

    def resolve_attempt(
        self, task_id: str, attempt_id: str | None = None,
    ) -> Attempt:
        """Resolve one task-bound attempt without ever mixing attempts.

        Explicit ids must belong to ``task_id``.  The default is the durable
        ``tasks.current_attempt_id``; legacy rows without that pointer fall
        back to the highest ``attempt_no``.
        """
        task = self.tasks.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError("Task not found")
        requested = (attempt_id or "").strip()
        if requested:
            attempt = self.tasks.get_attempt_for_task(task_id, requested)
            if attempt is None:
                raise ValueError("Attempt does not belong to task")
            return attempt
        if task.current_attempt_id:
            attempt = self.tasks.get_attempt_for_task(
                task_id, task.current_attempt_id,
            )
            if attempt is not None:
                return attempt
        attempt = self.tasks.get_latest_attempt(task_id)
        if attempt is None:
            raise ValueError("Task has no attempt")
        return attempt

    def list_evidence(
        self, task_id: str, attempt_id: str | None = None,
    ) -> list[Evidence]:
        """Evidence of the resolved attempt (never mixed across attempts)."""
        attempt = self.resolve_attempt(task_id, attempt_id)
        return self.evidence.list_for_attempt(task_id, attempt.attempt_id)

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        return self.artifacts.get(artifact_id)

    def list_artifacts(
        self, task_id: str, attempt_id: str | None = None,
    ) -> list[Artifact]:
        """Artifact declarations of the resolved attempt."""
        attempt = self.resolve_attempt(task_id, attempt_id)
        return self.artifacts.list_for_attempt(task_id, attempt.attempt_id)

    # ------------------------------------------------------------- lifecycle

    def update_status(
        self,
        task_id: str,
        *,
        status: str,
        event_type: str,
        version: int,
        now: float | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
        session_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        terminal_reason: str | None = None,
        timeout_reason: str | None = None,
        cancel_scope: str | None = None,
        cancel_initiator: str | None = None,
        lease: LeaseInfo | None = None,
    ) -> None:
        """Update task status + append event atomically (optimistic version).

        PR3.1: when ``lease`` is given, the UPDATE additionally requires the
        caller to still be the live lease owner (transactional fencing).
        PR3.3: the fence and all timestamps use the database clock read
        AFTER the write lock was granted — a lock wait that crosses the
        lease deadline refuses the write instead of committing with a
        stale timestamp.  The ``now`` parameter is accepted for backward
        compatibility only.
        """
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            updated = self.tasks.update_status(
                connection,
                task_id,
                status=status,
                version=version,
                updated_at=db_now,
                started_at=started_at,
                finished_at=finished_at,
                session_id=session_id,
                error_code=error_code,
                error_message=error_message,
                terminal_reason=terminal_reason,
                timeout_reason=timeout_reason,
                cancel_scope=cancel_scope,
                cancel_initiator=cancel_initiator,
                lease=self._lease_fence(lease, db_now),
            )
            if not updated:
                self._raise_write_failure(
                    connection, task_id, expected_version=version,
                    lease=lease, now=db_now,
                )
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task_id,
                    event_type=event_type,
                    event_time=db_now,
                    payload_json=json.dumps(
                        {"status": status}, ensure_ascii=False, sort_keys=True,
                    ),
                ),
            )

    def request_cancel(
        self,
        task_id: str,
        *,
        cancel_scope: str = "",
        cancel_initiator: str = "user",
        now: float | None = None,
        lease: LeaseInfo | None = None,
        allow_if_unowned: bool = False,
    ) -> CancelRequestResult:
        """Atomically request cancellation for a non-terminal task.

        Returns ``CancelRequestResult`` with ``created``, ``version`` and
        ``status``.  Raises ``RuntimePersistenceError`` if the task is
        already terminal.  Idempotent: a second call with the same task
        returns ``created=False`` with the current version and status,
        and never writes a duplicate event.

        PR3.2: when ``lease`` is given (a live persisted worker handle),
        the UPDATE is fenced on the session lease inside the same
        transaction — a stale worker whose lease was taken over by
        reconciliation can never write ``cancel_requested_at`` /
        ``cancelling``; the refusal raises ``LeaseLostError``.

        PR3.3: (a) even a REPLAY (``cancel_requested_at`` already set) must
        verify the caller's lease (or the unowned guard) at database
        execution time before returning ``created=False``; (b) a persisted
        pre-acquire early cancel (``lease=None``) must pass
        ``allow_if_unowned=True``, which only permits the write when the
        session is unowned or its lease already expired — a live owner
        (e.g. a reconciler) always refuses the cancel.
        """
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            # Check current status — refuse if terminal.  PR3.4: a terminal
            # seen at the initial read is a FINISHED task, not a persistence
            # failure: report it as the current durable state so the public
            # cancel path returns ok=false + "Task already finished" instead
            # of "runtime persistence failed".  The UPDATE below repeats the
            # terminal guard so a concurrent reconciliation write can never
            # be overwritten between read and write.
            row = connection.execute(
                "SELECT status, cancel_requested_at, version FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise RuntimePersistenceError(f"task {task_id} not found")
            if row["status"] in TERMINAL_STATUS_VALUES:
                return CancelRequestResult(
                    created=False,
                    version=int(row["version"]),
                    status=str(row["status"]),
                )
            if row["cancel_requested_at"] is not None:
                # Already requested — a replay must still prove ownership:
                # only the current live owner may retry the transport.
                self._verify_cancel_ownership(
                    connection, task_id, lease, allow_if_unowned, db_now,
                )
                return CancelRequestResult(
                    created=False,
                    version=int(row["version"]),
                    status=str(row["status"]),
                )
            fence_sql, fence_params = (
                _lease_fence_clause(self._lease_fence(lease, db_now))
                if lease is not None
                else ("", ())
            )
            guard_sql, guard_params = (
                self._unowned_guard_clause(db_now)
                if lease is None and allow_if_unowned
                else ("", ())
            )
            # Write cancel_requested_at + status=cancelling + scope/initiator.
            cursor = connection.execute(
                f"""
                UPDATE tasks
                SET cancel_requested_at = ?,
                    status = ?,
                    updated_at = ?,
                    version = version + 1,
                    cancel_scope = COALESCE(cancel_scope, ?),
                    cancel_initiator = COALESCE(cancel_initiator, ?)
                WHERE task_id = ? AND cancel_requested_at IS NULL
                  AND status NOT IN ({TERMINAL_NOT_IN_PLACEHOLDERS}){fence_sql}{guard_sql}
                """,
                (
                    db_now, TaskStatus.CANCELLING.value, db_now, cancel_scope,
                    cancel_initiator, task_id, *sorted(TERMINAL_STATUS_VALUES),
                    *fence_params, *guard_params,
                ),
            )
            if cursor.rowcount == 0:
                # Raced with another writer.  A fenced caller whose lease
                # was taken over must learn the truth explicitly.
                if lease is not None:
                    failure = classify_fenced_write_failure(
                        connection, task_id,
                        expected_version=int(row["version"]),
                        lease=lease, now=db_now,
                    )
                    if failure == WRITE_LEASE_LOST:
                        raise LeaseLostError(
                            f"task {task_id} lease lost; cancel refused"
                        )
                elif allow_if_unowned and self._session_live_owned(
                    connection, task_id, db_now,
                ):
                    # A live owner appeared between the read and the write
                    # (e.g. a reconciler took over): refuse the early cancel.
                    raise LeaseLostError(
                        f"task {task_id} is owned by a live owner; early cancel refused"
                    )
                # Re-read current state (terminal outcome or another cancel
                # won): report it truthfully, never overwrite it.
                row = connection.execute(
                    "SELECT version, status FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                return CancelRequestResult(
                    created=False,
                    version=int(row["version"]),
                    status=str(row["status"]),
                )
            row = connection.execute(
                "SELECT version FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            new_version = int(row["version"])
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task_id,
                    event_type=EventType.CANCEL_REQUESTED.value,
                    event_time=db_now,
                    payload_json=__import__("json").dumps(
                        {"scope": cancel_scope, "initiator": cancel_initiator},
                        ensure_ascii=False,
                    ),
                ),
            )
        return CancelRequestResult(
            created=True,
            version=new_version,
            status=TaskStatus.CANCELLING.value,
        )

    def mark_cancel_requested(self, task_id: str, now: float | None = None) -> None:
        """Record caller intent atomically with the ``cancel_requested`` event.

        Idempotent: only the first writer wins (matching the in-process flag),
        and only that first write emits the audit event.
        """
        now = now if now is not None else now_epoch()
        with self.db.transaction() as connection:
            first_write = self.tasks.mark_cancel_requested(
                connection,
                task_id,
                cancel_requested_at=now,
                updated_at=now,
            )
            if not first_write:
                return
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task_id,
                    event_type=EventType.CANCEL_REQUESTED.value,
                    event_time=now,
                ),
            )

    def mark_cancel_confirmed(
        self,
        task_id: str,
        *,
        status: str,
        version: int,
        now: float | None = None,
        terminal_reason: str | None = "cancelled",
        lease: LeaseInfo | None = None,
    ) -> None:
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            confirmed = self.tasks.mark_cancel_confirmed(
                connection,
                task_id,
                cancel_confirmed_at=db_now,
                updated_at=db_now,
                version=version,
                lease=self._lease_fence(lease, db_now),
            )
            if not confirmed:
                self._raise_write_failure(
                    connection, task_id, expected_version=version,
                    lease=lease, now=db_now,
                )
            self.tasks.update_status(
                connection,
                task_id,
                status=status,
                version=version + 1,
                updated_at=db_now,
                finished_at=db_now,
                terminal_reason=terminal_reason,
            )
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task_id,
                    event_type=EventType.CANCEL_CONFIRMED.value,
                    event_time=db_now,
                    payload_json=json.dumps(
                        {"status": TaskStatus.CANCELLED.value},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )

    def append_usage_evidence(
        self,
        task_id: str,
        *,
        usage: dict[str, Any],
        lease: LeaseInfo | None = None,
    ) -> bool:
        """Append one immutable provider-reported Usage Evidence per Attempt.

        The Runtime never calculates price or fills missing usage values.  A
        repeated callback for the same Attempt is an idempotent no-op.
        """
        payload = dict(usage or {})
        if payload.get("schema") != "tp-voyager.usage/v1":
            raise ValueError("usage evidence must use tp-voyager.usage/v1")
        try:
            detail_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("usage evidence must be JSON serializable") from exc
        if len(detail_json.encode("utf-8")) > 32 * 1024:
            raise ValueError("usage evidence exceeds the bounded detail limit")

        with self.db.immediate_fenced_transaction() as (connection, db_now):
            durable = self.tasks.get_by_id_in_connection(connection, task_id)
            if durable is None:
                raise TaskNotFoundError("Task not found")
            if lease is not None and not self._lease_still_valid(
                connection, task_id, lease, db_now,
            ):
                raise LeaseLostError(
                    f"task {task_id} lease lost (owner/generation/expiry mismatch)"
                )
            attempt_id = durable.current_attempt_id
            if not attempt_id:
                raise RuntimePersistenceError(
                    f"task {task_id} has no durable current_attempt_id"
                )
            if self.evidence.has_type_for_attempt(
                connection,
                task_id=task_id,
                attempt_id=attempt_id,
                evidence_type=EvidenceType.USAGE.value,
            ):
                return False
            digest = hashlib.sha256(
                f"{task_id}\0{attempt_id}\0usage".encode("utf-8")
            ).hexdigest()[:16]
            self.evidence.insert_many(
                connection,
                [
                    Evidence(
                        evidence_id=f"evd-{digest}",
                        task_id=task_id,
                        attempt_id=attempt_id,
                        evidence_type=EvidenceType.USAGE.value,
                        trust_state=TrustState.OBSERVED.value,
                        origin=EvidenceOrigin.BACKEND.value,
                        summary="Provider-reported usage observed",
                        detail_json=detail_json,
                        captured_at=db_now,
                        created_at=db_now,
                    )
                ],
            )
            return True

    def latest_usage_evidence(
        self, task_id: str, attempt_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the bounded Usage Evidence payload for one Attempt, if any."""
        attempt = self.resolve_attempt(task_id, attempt_id)
        items = self.evidence.list_for_attempt(task_id, attempt.attempt_id)
        for item in reversed(items):
            if item.evidence_type != EvidenceType.USAGE.value:
                continue
            try:
                payload = json.loads(item.detail_json or "{}")
            except json.JSONDecodeError:
                return {}
            return payload if isinstance(payload, dict) else {}
        return {}

    def save_result(
        self,
        task_id: str,
        *,
        result: dict[str, Any] | None = None,
        structured_result: StructuredResult | None = None,
        initial_evidence: list[Evidence] | None = None,
        artifact_declarations: list[Artifact] | None = None,
        status: str,
        version: int,
        now: float | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        terminal_reason: str | None = None,
        timeout_reason: str | None = None,
        lease: LeaseInfo | None = None,
        metadata_rejected_count: int = 0,
    ) -> None:
        """Persist Result, declarations, evidence and terminal events atomically.

        Legacy callers may still pass ``result``.  The production PR4-B2
        finalization path passes ``structured_result`` and receives an
        attempt-bound ``agent_claim`` when completing successfully.  Every
        write uses one ``BEGIN IMMEDIATE`` fenced transaction, so any JSON,
        Artifact, Evidence, Event, lease, version, or terminal failure rolls
        the entire operation back.
        """
        if structured_result is None and result is None:
            raise ValueError("result or structured_result is required")
        if structured_result is not None and result is not None:
            raise ValueError("pass result or structured_result, not both")
        if metadata_rejected_count < 0:
            raise ValueError("metadata_rejected_count must be non-negative")

        with self.db.immediate_fenced_transaction() as (connection, db_now):
            durable = self.tasks.get_by_id_in_connection(connection, task_id)
            if durable is None:
                raise TaskNotFoundError("Task not found")
            if lease is not None and not self._lease_still_valid(
                connection, task_id, lease, db_now,
            ):
                raise LeaseLostError(
                    f"task {task_id} lease lost (owner/generation/expiry mismatch)"
                )
            if durable.status in TERMINAL_STATUS_VALUES:
                raise TaskAlreadyTerminalError(
                    f"task {task_id} is already terminal; newer truth stands"
                )
            if durable.version != version:
                raise TaskVersionConflictError(
                    f"task {task_id} changed concurrently (version {version})"
                )
            attempt_id = durable.current_attempt_id
            if not attempt_id:
                raise RuntimePersistenceError(
                    f"task {task_id} has no durable current_attempt_id"
                )
            if self.tasks.get_attempt_for_task_in_connection(
                connection, task_id, attempt_id,
            ) is None:
                raise RuntimePersistenceError(
                    f"task {task_id} current_attempt_id is not a valid attempt"
                )

            result_schema = "legacy"
            if structured_result is not None:
                if structured_result.attempt_id not in ("", attempt_id):
                    raise ValueError(
                        "StructuredResult attempt_id does not match durable task"
                    )
                bound_result = replace(
                    structured_result,
                    attempt_id=attempt_id,
                    schema=RESULT_SCHEMA,
                )
                result_payload = bound_result.to_dict()
                result_schema = RESULT_SCHEMA
            else:
                result_payload = dict(result or {})

            try:
                result_json = json.dumps(result_payload, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise RuntimePersistenceError(
                    "Result payload is not JSON serializable"
                ) from exc

            artifacts = [
                self._bind_artifact(item, task_id, attempt_id)
                for item in (artifact_declarations or [])
            ]
            evidences = [
                self._bind_evidence(item, task_id, attempt_id)
                for item in (initial_evidence or [])
            ]
            if (
                structured_result is not None
                and status == TaskStatus.COMPLETED.value
                and not any(
                    item.evidence_type == EvidenceType.AGENT_CLAIM.value
                    for item in evidences
                )
                and not self.evidence.has_type_for_attempt(
                    connection,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    evidence_type=EvidenceType.AGENT_CLAIM.value,
                )
            ):
                evidences.append(
                    Evidence(
                        evidence_id=self._agent_claim_id(task_id, attempt_id),
                        task_id=task_id,
                        attempt_id=attempt_id,
                        evidence_type=EvidenceType.AGENT_CLAIM.value,
                        trust_state=TrustState.DECLARED.value,
                        origin=EvidenceOrigin.AGENT.value,
                        summary="Agent returned final task material",
                        detail_json="{}",
                        captured_at=db_now,
                        created_at=db_now,
                    )
                )

            saved = self.tasks.save_result(
                connection,
                task_id,
                result_json=result_json,
                updated_at=db_now,
                version=version,
                lease=self._lease_fence(lease, db_now),
            )
            if not saved:
                self._raise_write_failure(
                    connection, task_id, expected_version=version,
                    lease=lease, now=db_now,
                )
            status_saved = self.tasks.update_status(
                connection,
                task_id,
                status=status,
                version=version + 1,
                updated_at=db_now,
                finished_at=db_now,
                error_code=error_code,
                error_message=error_message,
                terminal_reason=terminal_reason,
                timeout_reason=timeout_reason,
            )
            if not status_saved:
                raise RuntimePersistenceError(
                    f"task {task_id} status update failed during finalization"
                )

            # Artifact first: Evidence may reference an Artifact declaration.
            self.artifacts.insert_many(connection, artifacts)
            self.evidence.insert_many(connection, evidences)

            event_payload = {
                "result_schema": result_schema,
                "evidence_count": len(evidences),
                "artifact_count": len(artifacts),
                "metadata_rejected_count": metadata_rejected_count,
            }
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task_id,
                    event_type=EventType.RESULT_AVAILABLE.value,
                    event_time=db_now,
                    attempt_id=attempt_id,
                    payload_json=json.dumps(
                        event_payload, ensure_ascii=False, sort_keys=True,
                    ),
                ),
            )
            terminal_event_type = {
                TaskStatus.COMPLETED.value: EventType.TASK_COMPLETED.value,
                TaskStatus.FAILED.value: EventType.TASK_FAILED.value,
            }.get(status)
            if terminal_event_type is not None:
                self.events.append(
                    connection,
                    TaskEvent(
                        event_id=new_event_id(),
                        task_id=task_id,
                        event_type=terminal_event_type,
                        event_time=db_now,
                        attempt_id=attempt_id,
                        payload_json=json.dumps(
                            {"status": status}, ensure_ascii=False, sort_keys=True,
                        ),
                    ),
                )

    @staticmethod
    def _agent_claim_id(task_id: str, attempt_id: str) -> str:
        digest = hashlib.sha256(
            f"{task_id}\0{attempt_id}\0agent_claim".encode("utf-8")
        ).hexdigest()[:16]
        return f"evd-{digest}"

    @staticmethod
    def _bind_evidence(
        evidence: Evidence, task_id: str, attempt_id: str,
    ) -> Evidence:
        if evidence.task_id not in ("", task_id):
            raise ValueError("Evidence task_id does not match durable task")
        if evidence.attempt_id not in ("", attempt_id):
            raise ValueError("Evidence attempt_id does not match durable task")
        return replace(evidence, task_id=task_id, attempt_id=attempt_id)

    @staticmethod
    def _bind_artifact(
        artifact: Artifact, task_id: str, attempt_id: str,
    ) -> Artifact:
        if artifact.task_id not in ("", task_id):
            raise ValueError("Artifact task_id does not match durable task")
        if artifact.attempt_id not in ("", attempt_id):
            raise ValueError("Artifact attempt_id does not match durable task")
        return replace(artifact, task_id=task_id, attempt_id=attempt_id)

    def append_activity(
        self,
        task_id: str,
        kind: str,
        now: float | None = None,
        *,
        lease: LeaseInfo | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append an allow-listed ``activity_observed`` audit event.

        Worker-owned activity is lease fenced; pre-acquire activity may pass
        ``lease=None``.  This keeps stale workers from polluting the durable
        audit trail after ownership has moved.  ``details`` contains only the
        already-sanitized public observation fields; prompt text and raw tool
        output are never accepted here.
        """
        payload: dict[str, Any] = {"kind": kind}
        if isinstance(details, dict):
            for key in _ACTIVITY_DETAIL_KEYS:
                value = details.get(key)
                if isinstance(value, (str, int, float, bool)) and not isinstance(value, bool):
                    payload[key] = value
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            if lease is not None and not self._lease_still_valid(connection, task_id, lease, db_now):
                raise LeaseLostError(
                    f"task {task_id} lease lost (owner/generation/expiry mismatch)"
                )
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task_id,
                    event_type=EventType.ACTIVITY_OBSERVED.value,
                    event_time=now if now is not None else db_now,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                ),
            )

    def accept_backend_dispatch(
        self,
        task_id: str,
        *,
        backend_session_id: str,
        version: int,
        lease: LeaseInfo,
    ) -> None:
        """Hard gate before any real Provider prompt may be sent.

        The lease owner/generation/DB-time expiry, task version, terminal
        state, backend-session claim, and BACKEND_DISPATCH_ACCEPTED audit
        event are checked/written in one ``BEGIN IMMEDIATE`` transaction.
        A stale worker therefore cannot regain the right to dispatch after a
        newer owner has acquired the task.  Replaying the same accepted
        session is idempotent; a different session fails closed.
        """
        backend_session_id = str(backend_session_id or "").strip()
        if not backend_session_id:
            raise ValueError("backend_session_id is required")
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            durable = self.tasks.get_by_id_in_connection(connection, task_id)
            if durable is None:
                raise TaskNotFoundError("Task not found")
            if not self._lease_still_valid(connection, task_id, lease, db_now):
                raise LeaseLostError(
                    f"task {task_id} lease lost (owner/generation/expiry mismatch)"
                )
            if durable.status in TERMINAL_STATUS_VALUES:
                raise TaskAlreadyTerminalError(
                    f"task {task_id} is already terminal; dispatch refused"
                )
            if durable.version != version:
                raise TaskVersionConflictError(
                    f"task {task_id} changed concurrently (version {version})"
                )

            existing = connection.execute(
                "SELECT session_id, backend_session_id FROM sessions "
                "WHERE task_id = ? ORDER BY created_at LIMIT 1",
                (task_id,),
            ).fetchone()
            if existing is None:
                raise RuntimePersistenceError(
                    f"no runtime session row for task {task_id}"
                )
            current_backend_id = str(existing["backend_session_id"] or "")
            if current_backend_id and current_backend_id != backend_session_id:
                raise RuntimePersistenceError(
                    f"task {task_id} backend session already claimed by another dispatch"
                )

            outcome, session_id = self.sessions.claim_backend_session_id(
                connection,
                task_id,
                backend_session_id=backend_session_id,
                updated_at=db_now,
            )
            if outcome == "kept":
                raise RuntimePersistenceError(
                    f"task {task_id} backend session claim lost concurrently"
                )
            if outcome == "created":
                self.events.append(
                    connection,
                    TaskEvent(
                        event_id=new_event_id(),
                        task_id=task_id,
                        session_id=session_id,
                        event_type=EventType.SESSION_CREATED.value,
                        event_time=db_now,
                    ),
                )

            accepted = connection.execute(
                "SELECT payload_json FROM events "
                "WHERE task_id = ? AND event_type = ? ORDER BY seq LIMIT 1",
                (task_id, EventType.BACKEND_DISPATCH_ACCEPTED.value),
            ).fetchone()
            if accepted is not None:
                try:
                    payload = json.loads(str(accepted["payload_json"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if str(payload.get("backend_session_id") or "") != backend_session_id:
                    raise RuntimePersistenceError(
                        f"task {task_id} dispatch acceptance conflicts with durable session"
                    )
                return

            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task_id,
                    session_id=session_id,
                    event_type=EventType.BACKEND_DISPATCH_ACCEPTED.value,
                    event_time=db_now,
                    payload_json=json.dumps(
                        {"backend_session_id": backend_session_id},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    visibility=EventVisibility.INTERNAL.value,
                ),
            )

    def mark_reconciled_cancelled(
        self,
        task_id: str,
        *,
        version: int,
        lease: LeaseInfo,
        now: float | None = None,
    ) -> str:
        """PR3.1: persist a backend-confirmed ``terminal_cancelled``.

        One fenced transaction writes status=cancelled, the confirmation
        timestamps and a ``cancel_confirmed`` event — never a
        ``task_failed`` event.  PR3.3: the fence and timestamps use the
        database clock read AFTER the write lock was granted.  Returns a
        ``WRITE_*`` outcome string so the caller can report the real
        durable result.
        """
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            confirmed = self.tasks.mark_reconciled_cancelled(
                connection,
                task_id,
                cancel_confirmed_at=db_now,
                finished_at=db_now,
                updated_at=db_now,
                version=version,
                lease=self._lease_fence(lease, db_now),
            )
            if not confirmed:
                return classify_fenced_write_failure(
                    connection, task_id, expected_version=version,
                    lease=lease, now=db_now,
                )
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task_id,
                    event_type=EventType.CANCEL_CONFIRMED.value,
                    event_time=db_now,
                    payload_json=json.dumps(
                        {
                            "reconciled": True,
                            "status": TaskStatus.CANCELLED.value,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
            return WRITE_WRITTEN

    # ------------------------------------------------------- fencing helpers

    @staticmethod
    def _lease_fence(lease: LeaseInfo | None, now: float):
        """Convert a ``LeaseInfo`` into the repository fence tuple."""
        if lease is None:
            return None
        return (lease.instance_id, lease.generation, now)

    @staticmethod
    def _lease_still_valid(connection, task_id: str, lease: LeaseInfo, db_now: float) -> bool:
        """Is this caller still the live owner at database execution time?"""
        row = connection.execute(
            "SELECT owner_instance_id, owner_generation, lease_expires_at "
            "FROM sessions WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return False
        if row["owner_instance_id"] != lease.instance_id:
            return False
        if int(row["owner_generation"] or 0) != lease.generation:
            return False
        return (
            row["lease_expires_at"] is not None
            and float(row["lease_expires_at"]) > db_now
        )

    @staticmethod
    def _session_live_owned(connection, task_id: str, db_now: float) -> bool:
        """Is the session held by ANY live owner at database execution time?"""
        row = connection.execute(
            "SELECT owner_instance_id, lease_expires_at FROM sessions WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return False
        return (
            row["owner_instance_id"] is not None
            and row["lease_expires_at"] is not None
            and float(row["lease_expires_at"]) > db_now
        )

    @classmethod
    def _verify_cancel_ownership(
        cls, connection, task_id: str, lease, allow_if_unowned: bool, db_now: float,
    ) -> None:
        """PR3.3: a cancel replay must be owned by the caller.

        With a lease: only the current live owner may retry the transport.
        Without a lease (pre-acquire early cancel): only an unowned or
        expired session may be cancelled.  Refusals raise ``LeaseLostError``.
        """
        if lease is not None:
            if not cls._lease_still_valid(connection, task_id, lease, db_now):
                raise LeaseLostError(
                    f"task {task_id} lease lost; cancel replay refused"
                )
        elif allow_if_unowned and cls._session_live_owned(connection, task_id, db_now):
            raise LeaseLostError(
                f"task {task_id} is owned by a live owner; cancel replay refused"
            )

    @staticmethod
    def _unowned_guard_clause(db_now: float) -> tuple[str, tuple]:
        """Allow a pre-acquire early cancel only on an unowned/expired session."""
        return (
            " AND NOT EXISTS ("
            "   SELECT 1 FROM sessions"
            "   WHERE sessions.task_id = tasks.task_id"
            "     AND sessions.owner_instance_id IS NOT NULL"
            "     AND sessions.lease_expires_at IS NOT NULL"
            "     AND sessions.lease_expires_at > ?"
            " )",
            (db_now,),
        )

    @staticmethod
    def _raise_write_failure(
        connection,
        task_id: str,
        *,
        expected_version: int,
        lease: LeaseInfo | None,
        now: float,
    ) -> None:
        """Classify and raise a refused fenced write explicitly."""
        failure = classify_fenced_write_failure(
            connection, task_id, expected_version=expected_version,
            lease=lease, now=now,
        )
        if failure == WRITE_LEASE_LOST:
            raise LeaseLostError(
                f"task {task_id} lease lost (owner/generation/expiry mismatch)"
            )
        if failure == WRITE_ALREADY_TERMINAL:
            raise TaskAlreadyTerminalError(
                f"task {task_id} is already terminal; newer truth stands"
            )
        raise TaskVersionConflictError(
            f"task {task_id} changed concurrently (version {expected_version})"
        )

    # -------------------------------------------------------------- activity

    def activity_from_events(self, task_id: str) -> list[dict[str, Any]]:
        """Rebuild the public activity list from audit events.

        Streaming activity details are persisted only after the observation
        layer has reduced them to the public allow-list, so a restart can
        rebuild the safe execution/file timeline without exposing raw output.
        """
        activities: list[dict[str, Any]] = []
        for event in self.get_events(task_id):
            if event.event_type == EventType.ACTIVITY_OBSERVED.value:
                payload = parse_session_metadata(event.payload_json)
                kind = payload.get("kind")
                if isinstance(kind, str) and kind:
                    activity = {"kind": kind, "at": event.event_time}
                    for key in _ACTIVITY_DETAIL_KEYS:
                        if key in payload:
                            activity[key] = payload[key]
                    activities.append(activity)
        return activities


class _StepConflict(Exception):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"run step already bound to {task_id}")
        self.task_id = task_id


class _RunBudgetRejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class _ReplayOrConflict(Exception):
    """Internal control flow: key already bound; carry outcome + task id."""

    def __init__(self, outcome: str, task_id: str | None) -> None:
        super().__init__(f"idempotency {outcome} for {task_id}")
        self.outcome = outcome
        self.task_id = task_id


def _handle_replay_or_conflict(exc: _ReplayOrConflict) -> CreateTaskResult:
    if exc.outcome == ClaimOutcome.REPLAY.value:
        return CreateTaskResult(outcome="replayed", task_id=exc.task_id)
    return CreateTaskResult(
        outcome="conflict",
        error=(
            "idempotency_key 冲突：该 key 已绑定另一个请求"
            "（指纹不匹配）。不得用同一 key 派发不同请求。"
        ),
    )
