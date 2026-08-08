"""Explicit exception types for the persistence layer.

SQLite errors are never swallowed: they are wrapped (or re-raised) so callers
can distinguish storage failures from task/domain failures.
"""

from __future__ import annotations


class RuntimePersistenceError(RuntimeError):
    """Base class: durable state could not be read or written."""


class TaskNotFoundError(RuntimePersistenceError):
    """A task row expected to exist does not."""


class TaskVersionConflictError(RuntimePersistenceError):
    """Optimistic concurrency check failed (row version mismatch)."""


class IdempotencyConflictError(RuntimePersistenceError):
    """Same idempotency key was already bound to a different request."""


class LeaseLostError(RuntimePersistenceError):
    """The caller no longer owns the session lease (fencing failure).

    Raised when a write requires the lease owner identity + generation but
    the durable row is owned by a different/newer generation (typically a
    stale worker thread writing after restart reconciliation took over).
    """


class TaskAlreadyTerminalError(RuntimePersistenceError):
    """A write targeted a task that already reached a terminal status.

    PR3.1: raised when a fenced write (worker terminal commit, reconciliation
    writer, cancel request) is refused because the durable row is already
    completed/failed/cancelled/lost/orphaned — the newer truth must stand.
    """
