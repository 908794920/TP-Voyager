"""Restart reconciliation: classify stale non-terminal tasks.

PR3 startup policy (from the V1 architecture):

    RUNNING from old owner
        ↓
    reconcile
        ├── terminal found        → update terminal
        ├── safely resumable      → PAUSED/WAITING (not implemented in PR3)
        ├── process alive but unbound → ORPHANED
        └── unknown               → LOST

Rules enforced here:

- SQLite is the state truth; the backend only classifies.
- PR3.1: the lease is acquired BEFORE any classification, and the durable
  task + session are re-read under that lease.  A live worker (lease held,
  backend session id not yet persisted) is skipped, never marked failed.
- Every writer is fenced in one transaction on version, non-terminal
  status and the current lease owner+generation+expiry, so an old snapshot
  can never overwrite a newer terminal truth.
- A backend ``terminal_cancelled`` signal goes through a dedicated cancel
  transaction (cancel_confirmed event; never task_failed).
- LOST / ORPHANED are never auto-converted to failed/cancelled.
- No prompt is ever re-dispatched and no backend failover happens here.
- One failing task never kills the pass (per-task exception isolation) and
  event payloads never carry prompt/answer/paths/credentials.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from agent_runtime.backends.base import (
    BackendReconcileRequest,
    SubAgentBackend,
)
from agent_runtime.backends.errors import BackendError
from agent_runtime.domain.enums import (
    TERMINAL_STATUS_VALUES,
    EventType,
    TaskStatus,
)
from agent_runtime.domain.event import TaskEvent
from agent_runtime.domain.ids import new_event_id
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.event_repository import EventRepository
from agent_runtime.persistence.session_repository import (
    DEFAULT_LEASE_DURATION_SECONDS,
    SessionRepository,
)
from agent_runtime.persistence.task_repository import TaskRepository
from agent_runtime.runtime.lease import LeaseService
from agent_runtime.application.task_service import (
    WRITE_ALREADY_TERMINAL,
    WRITE_CONFLICT,
    WRITE_LEASE_LOST,
    WRITE_WRITTEN,
    classify_fenced_write_failure,
)

_TERMINAL_OUTCOME_TO_STATUS = {
    "terminal_completed": TaskStatus.COMPLETED.value,
    "terminal_failed": TaskStatus.FAILED.value,
    "terminal_cancelled": TaskStatus.CANCELLED.value,
}

# PR3.2: bounded retry limit for a fenced writer that hit a non-terminal
# version conflict (e.g. a cancel request bumped the version mid-pass).
_WRITE_RETRY_LIMIT = 3


@dataclass
class ReconcileReport:
    """One task's reconciliation outcome (public-facing summary)."""

    task_id: str
    outcome: str  # completed | failed | cancelled | lost | orphaned | skipped | error
    detail: str = ""


class ReconciliationService:
    """Owns the startup reconciliation pass over stale non-terminal tasks."""

    def __init__(
        self,
        db: Database,
        *,
        instance_id: str | None = None,
        lease_duration_seconds: float = DEFAULT_LEASE_DURATION_SECONDS,
    ) -> None:
        self.db = db
        self.tasks = TaskRepository(db)
        self.events = EventRepository(db)
        self.lease = LeaseService(
            db,
            instance_id=instance_id,
            lease_duration_seconds=lease_duration_seconds,
        )

    def reconcile_all(
        self,
        backend: SubAgentBackend | Callable[[str], SubAgentBackend],
    ) -> list[ReconcileReport]:
        """Classify every non-terminal task; returns one report per task.

        Each task runs in its own try/except: an unexpected exception must
        never terminate the whole pass or vanish without a report.
        """
        reports: list[ReconcileReport] = []
        for task in self.tasks.list_non_terminal():
            try:
                reports.append(self._reconcile_one(task, backend))
            except Exception as exc:  # noqa: BLE001 - per-task isolation
                # Safe diagnostic only: exception type, never content.
                reports.append(
                    ReconcileReport(
                        task.task_id,
                        "error",
                        f"reconcile crashed: {type(exc).__name__}",
                    )
                )
        return reports

    def _reconcile_one(
        self,
        snapshot_task,
        backend_or_resolver: SubAgentBackend | Callable[[str], SubAgentBackend],
    ) -> ReconcileReport:
        task_id = snapshot_task.task_id
        # 1) Lease first: a live worker (or another reconciler) owns the
        # session -> skip before any classification or write.
        lease = self.lease.acquire(task_id)
        if lease is None:
            return ReconcileReport(
                task_id, "skipped",
                "session lease is held by another live owner",
            )
        try:
            # 2) Re-read the durable truth under the lease: the snapshot may
            # be stale by the time the write lock was granted.
            durable = self.tasks.get_by_id(task_id)
            if durable is None:
                return ReconcileReport(task_id, "skipped", "task no longer exists")
            if durable.status in TERMINAL_STATUS_VALUES:
                return ReconcileReport(
                    task_id, "skipped", f"already terminal: {durable.status}",
                )
            session = SessionRepository(self.db).get_by_task_id(task_id)
            # 3) Never dispatched: an explicit, honest failure.  Fenced like
            # every other writer (still under the acquired lease).
            if session is None or not session.backend_session_id:
                return self._write_with_retry(
                    task_id, lease,
                    lambda durable: self._mark_never_dispatched(durable, lease),
                    "failed", "bridge restarted before dispatch",
                )
            # 4) Resolve the durable session's backend and classify (never dispatches).
            try:
                backend = (
                    backend_or_resolver(session.backend)
                    if callable(backend_or_resolver)
                    else backend_or_resolver
                )
            except BackendError as exc:
                # Historical rows may reference a backend that is no longer a
                # supported TP-Voyager Crew.  Preserve durable truth by
                # classifying it LOST rather than leaving startup recovery in
                # an error loop or silently substituting another Crew.
                return self._write_with_retry(
                    task_id, lease,
                    lambda durable: self._mark_lost_or_orphaned(
                        durable, lease, "lost", f"backend unavailable for reconciliation: {exc}",
                    ),
                    "lost", f"backend unavailable for reconciliation: {exc}",
                )
            try:
                result = backend.reconcile(
                    BackendReconcileRequest(
                        task_id=task_id,
                        backend_session_id=session.backend_session_id,
                        route=session.route,
                        started_at=durable.started_at,
                    )
                )
            except BackendError as exc:
                # The probe failed: the backend truth is unknown -> LOST.
                return self._write_with_retry(
                    task_id, lease,
                    lambda durable: self._mark_lost_or_orphaned(
                        durable, lease, "lost", f"reconcile probe failed: {exc}",
                    ),
                    "lost", f"reconcile probe failed: {exc}",
                )
            # 5) Classify.
            status = _TERMINAL_OUTCOME_TO_STATUS.get(result.outcome)
            if status is not None:
                if result.outcome == "terminal_cancelled":
                    # Dedicated cancel transaction: never a task_failed event.
                    return self._write_with_retry(
                        task_id, lease,
                        lambda durable: self._mark_reconciled_cancelled(
                            durable, lease,
                        ),
                        "cancelled", result.error or "backend reported cancelled",
                    )
                return self._write_with_retry(
                    task_id, lease,
                    lambda durable: self._mark_terminal(
                        durable, lease, status, result,
                    ),
                    status, result.error or "backend reported terminal",
                )
            if result.outcome == "orphaned":
                return self._write_with_retry(
                    task_id, lease,
                    lambda durable: self._mark_lost_or_orphaned(
                        durable, lease, "orphaned", result.error,
                    ),
                    "orphaned", result.error or "live local host cannot be rebound",
                )
            return self._write_with_retry(
                task_id, lease,
                lambda durable: self._mark_lost_or_orphaned(
                    durable, lease, "lost", result.error,
                ),
                "lost", result.error or "backend truth cannot be determined",
            )
        finally:
            self.lease.release(task_id, lease)

    # ---------------------------------------------------------- writers

    def _write_with_retry(
        self,
        task_id: str,
        lease,
        writer,
        planned_outcome: str,
        detail: str,
    ) -> ReconcileReport:
        """Run a fenced writer, retrying bounded times on non-terminal conflicts.

        PR3.2: a ``WRITE_CONFLICT`` on a still non-terminal task (e.g. a
        cancel request bumped the version while reconciliation classified)
        is retried with the latest durable version and the SAME backend
        classification — never re-dispatches, never fails over, never loops
        forever.  PR3.3: every writer reads its OWN database clock after
        acquiring the write lock, so a lock wait that crosses the lease
        deadline yields ``WRITE_LEASE_LOST`` instead of a stale commit.
        """
        for attempt in range(_WRITE_RETRY_LIMIT):
            durable = self.tasks.get_by_id(task_id)
            if durable is None:
                return ReconcileReport(
                    task_id, "skipped", "task disappeared during reconcile",
                )
            if durable.status in TERMINAL_STATUS_VALUES:
                return ReconcileReport(
                    task_id, "skipped", f"concurrent terminal: {durable.status}",
                )
            write = writer(durable)
            if write == WRITE_WRITTEN:
                return ReconcileReport(task_id, planned_outcome, detail)
            if write == WRITE_LEASE_LOST:
                return ReconcileReport(
                    task_id, "skipped", "lease lost before write; durable unchanged",
                )
            if write == WRITE_ALREADY_TERMINAL:
                return ReconcileReport(
                    task_id, "skipped", "concurrent terminal: durable already terminal",
                )
            if write != WRITE_CONFLICT:
                # Unknown writer outcome: safe diagnostic, never claimed.
                return ReconcileReport(
                    task_id, "error", f"reconcile write outcome: {write}",
                )
            # Non-terminal version conflict: bounded retry, same classification.
            if attempt + 1 >= _WRITE_RETRY_LIMIT:
                return ReconcileReport(
                    task_id, "error", "reconcile write conflict after bounded retries",
                )
        return ReconcileReport(
            task_id, "error", "reconcile write conflict after bounded retries",
        )

    def _report(
        self, task_id: str, write: str, planned_outcome: str, detail: str,
    ) -> ReconcileReport:
        """Map a writer's real outcome onto the public report.

        A refused write is never reported as the planned outcome: the
        durable truth is re-read and reported as skipped instead.
        """
        if write == WRITE_WRITTEN:
            return ReconcileReport(task_id, planned_outcome, detail)
        durable = self.tasks.get_by_id(task_id)
        if durable is None:
            return ReconcileReport(task_id, "skipped", "task disappeared during reconcile")
        if durable.status in TERMINAL_STATUS_VALUES:
            return ReconcileReport(
                task_id, "skipped", f"concurrent terminal: {durable.status}",
            )
        if write == WRITE_LEASE_LOST:
            return ReconcileReport(
                task_id, "skipped", "lease lost before write; durable unchanged",
            )
        return ReconcileReport(
            task_id, "skipped", f"concurrent update; durable status: {durable.status}",
        )

    def _mark_never_dispatched(self, task, lease) -> str:
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            updated = self.tasks.update_status(
                connection,
                task.task_id,
                status=TaskStatus.FAILED.value,
                version=task.version,
                updated_at=db_now,
                finished_at=db_now,
                error_code="NeverDispatched",
                error_message="bridge restarted before dispatch",
                terminal_reason="never_dispatched",
                lease=(lease.instance_id, lease.generation, db_now),
            )
            if not updated:
                return classify_fenced_write_failure(
                    connection, task.task_id, expected_version=task.version,
                    lease=lease, now=db_now,
                )
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task.task_id,
                    event_type=EventType.TASK_FAILED.value,
                    event_time=db_now,
                    payload_json=json.dumps(
                        {"reason": "never_dispatched", "status": TaskStatus.FAILED.value}, ensure_ascii=False
                    ),
                ),
            )
            return WRITE_WRITTEN

    def _mark_terminal(self, task, lease, status: str, result) -> str:
        event_type = (
            EventType.TASK_COMPLETED.value
            if status == TaskStatus.COMPLETED.value
            else EventType.TASK_FAILED.value
        )
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            updated = self.tasks.update_status(
                connection,
                task.task_id,
                status=status,
                version=task.version,
                updated_at=db_now,
                finished_at=db_now,
                error_code=(None if status == TaskStatus.COMPLETED.value else result.outcome),
                error_message=result.error or None,
                terminal_reason=status,
                lease=(lease.instance_id, lease.generation, db_now),
            )
            if not updated:
                return classify_fenced_write_failure(
                    connection, task.task_id, expected_version=task.version,
                    lease=lease, now=db_now,
                )
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task.task_id,
                    event_type=event_type,
                    event_time=db_now,
                    payload_json=json.dumps(
                        {"reconciled": True, "status": status}, ensure_ascii=False
                    ),
                ),
            )
            return WRITE_WRITTEN

    def _mark_lost_or_orphaned(
        self, task, lease, kind: str, error: str | None,
    ) -> str:
        event_type = (
            EventType.TASK_LOST.value
            if kind == "lost"
            else EventType.TASK_ORPHANED.value
        )
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            updated = self.tasks.mark_reconciled_terminal(
                connection,
                task.task_id,
                status=kind,
                marked_at=db_now,
                updated_at=db_now,
                version=task.version,
                error_message=error or None,
                lease=(lease.instance_id, lease.generation, db_now),
            )
            if not updated:
                return classify_fenced_write_failure(
                    connection, task.task_id, expected_version=task.version,
                    lease=lease, now=db_now,
                )
            self.events.append(
                connection,
                TaskEvent(
                    event_id=new_event_id(),
                    task_id=task.task_id,
                    event_type=event_type,
                    event_time=db_now,
                    payload_json=json.dumps(
                        {"reconciled": True, "status": kind}, ensure_ascii=False
                    ),
                ),
            )
            return WRITE_WRITTEN

    def _mark_reconciled_cancelled(self, task, lease) -> str:
        from agent_runtime.application.task_service import TaskService

        return TaskService(self.db).mark_reconciled_cancelled(
            task.task_id,
            version=task.version,
            lease=lease,
        )
