"""Lease service: session ownership and fencing for the durable runtime.

PR3 introduces a single-writer lease per runtime session: a bridge
instance acquires ownership (bumping ``owner_generation``) before running
a task worker, and restart reconciliation takes ownership over stale
non-terminal tasks the same way.  Every terminal write by a worker is
fenced: if the durable owner/generation no longer matches (a newer owner
took over), the stale write is refused instead of overwriting the newer
truth.

PR3.1 adds ``LeaseHeartbeat``: a worker renews its lease on an interval
independent of backend activity, so long-running tasks (including long
thinking phases) never look stale to restart reconciliation.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from agent_runtime.domain.ids import new_instance_id
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.session_repository import (
    DEFAULT_LEASE_DURATION_SECONDS,
)


@dataclass
class LeaseInfo:
    """One acquired session lease (handle-side mirror)."""

    instance_id: str
    generation: int
    expires_at: float


class LeaseService:
    """Acquire / renew / release / verify session leases."""

    def __init__(
        self,
        db: Database,
        *,
        instance_id: str | None = None,
        lease_duration_seconds: float = DEFAULT_LEASE_DURATION_SECONDS,
    ) -> None:
        self.db = db
        self.instance_id = instance_id or new_instance_id()
        self.lease_duration_seconds = lease_duration_seconds
        from agent_runtime.persistence.session_repository import (
            SessionRepository,
        )

        self.sessions = SessionRepository(db)

    def acquire(self, task_id: str, *, now: float | None = None) -> LeaseInfo | None:
        """Acquire the session lease for ``task_id``.

        Returns ``None`` when the session row is missing, held by another
        live owner, or already owned by this same live instance
        (AlreadyOwned — lease isolates process instances, not threads, so
        a second handle never receives a second valid ``LeaseInfo``).
        PR3.1: the read and the conditional UPDATE run inside one
        ``BEGIN IMMEDIATE`` transaction.  PR3.3: ``db_now`` is the database
        clock read AFTER the write lock was granted, so the returned lease
        is always valid at commit time (never "acquired but immediately
        expired").  The ``now`` parameter is accepted for backward
        compatibility only and is never used for fencing.
        """
        session = self.sessions.get_by_task_id(task_id)
        if session is None:
            return None
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            acquired, generation, expires_at = self.sessions.acquire_lease(
                connection,
                session.session_id,
                instance_id=self.instance_id,
                db_now=db_now,
                lease_duration_seconds=self.lease_duration_seconds,
            )
            if not acquired:
                return None
        return LeaseInfo(
            instance_id=self.instance_id,
            generation=generation,
            expires_at=expires_at,
        )

    def renew(self, task_id: str, lease: LeaseInfo, *, now: float | None = None) -> bool:
        """Extend the lease; False when ownership was lost or expired.

        PR3.3: the expiry check uses the database clock read AFTER the
        write lock was granted — a lock wait that crosses the deadline
        makes ``renew`` fail instead of reviving an expired lease.  On
        success the durable expiry (``db_now + duration``) is written back
        into ``lease.expires_at`` so the Heartbeat mirror never guesses.
        The ``now`` parameter is accepted for backward compatibility only.
        """
        session = self.sessions.get_by_task_id(task_id)
        if session is None:
            return False
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            new_expires_at = self.sessions.renew_lease(
                connection,
                session.session_id,
                instance_id=lease.instance_id,
                generation=lease.generation,
                db_now=db_now,
                lease_duration_seconds=self.lease_duration_seconds,
            )
            if new_expires_at is None:
                return False
            lease.expires_at = new_expires_at
            return True

    def release(self, task_id: str, lease: LeaseInfo, *, now: float | None = None) -> bool:
        """Release ownership; only the current owner+generation may release."""
        from agent_runtime.domain.timeutil import now_epoch

        now = now if now is not None else now_epoch()
        session = self.sessions.get_by_task_id(task_id)
        if session is None:
            return False
        with self.db.transaction() as connection:
            return self.sessions.release_lease(
                connection,
                session.session_id,
                instance_id=lease.instance_id,
                generation=lease.generation,
                now=now,
            )

    def ensure(self, task_id: str, lease: LeaseInfo, *, now: float | None = None) -> bool:
        """Fencing check: is this lease still the live owner?

        False when the durable owner changed (reconciliation took over) or
        the lease expired.  Callers must refuse to write terminal state.
        """
        from agent_runtime.domain.timeutil import now_epoch

        now = now if now is not None else now_epoch()
        session = self.sessions.get_by_task_id(task_id)
        if session is None:
            return False
        with self.db.connect() as connection:
            return self.sessions.ensure_lease(
                connection,
                session.session_id,
                instance_id=lease.instance_id,
                generation=lease.generation,
                now=now,
            )


class LeaseHeartbeat:
    """Renews a worker's session lease while the task executes.

    Independent of backend activity: a prompt in a long thinking phase (or a
    quiet SSE stream) keeps its lease alive.  On renew failure (a newer
    owner took over) or an unexpected renewal error the heartbeat marks
    itself lost and fires the optional callbacks — the worker must then
    refuse terminal writes (transactional fencing is the real guarantee)
    and may best-effort cancel the remote execution.  Never re-dispatches
    a prompt and never triggers backend failover.

    ``stop`` is bounded: the worker's ``finally`` joins the thread with a
    short timeout, so a wedged database never blocks shutdown.
    """

    def __init__(
        self,
        lease_service: LeaseService,
        task_id: str,
        lease: LeaseInfo,
        *,
        interval_seconds: float | None = None,
        on_lost: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.lease_service = lease_service
        self.task_id = task_id
        self.lease = lease
        self.interval_seconds = interval_seconds or max(
            0.05, lease_service.lease_duration_seconds / 3.0
        )
        self._on_lost = on_lost
        self._on_error = on_error
        self.lost = False
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the renew loop on a daemon thread (one per worker)."""
        self._thread = threading.Thread(
            target=self._loop,
            name=f"lease-heartbeat-{self.task_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(self.interval_seconds):
                return
            try:
                renewed = self.lease_service.renew(self.task_id, self.lease)
                # PR3.4: stop() means the runtime no longer needs heartbeat
                # results — a renew outcome that surfaces AFTER stop (e.g.
                # the renew was still blocked on the database) must never
                # fire on_lost/on_error.
                if self._stop.is_set():
                    return
                if not renewed:
                    # Ownership was taken over (or the row vanished): lost.
                    self.lost = True
                    self._fire(self._on_lost)
                    return
                # Success: LeaseService.renew already wrote the durable
                # expiry into the handle mirror — never guess it here.
            except Exception as exc:  # noqa: BLE001 - diagnostic only
                # PR3.4: an exception raised after stop is equally ignored.
                if self._stop.is_set():
                    return
                # Safe diagnostic: never write prompt/answer/paths here.
                self.lost = True
                self.last_error = f"{type(exc).__name__}"
                self._fire(self._on_error, self.last_error)
                return

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the loop to exit and join it (bounded)."""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _fire(self, callback: Callable | None, *args: object) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 - best-effort, never crash the loop
            pass
