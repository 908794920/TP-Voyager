"""Production ``BackendCallbacks`` adapter for the durable runtime.

The backend produces only typed facts (``BackendActivity``,
``BackendResult``); this adapter is the runtime's side of that boundary.
It is constructed by the MCP entry (``server.py``) with persistence
closures so that the runtime — not the backend — decides how to write
SQLite, events, and public projections.

Synchronous contract notes:
- ``on_dispatch_accepted`` is the only callback that must complete before
  the backend may send a real prompt/stream; a persistence failure raises
  so the route aborts the dispatch.
- ``on_activity`` carries typed, bounded activity; optional observation detail may
  include provider-visible assistant text for the human-facing projection.
- ``on_result`` marks the task execution finished so the runtime closes
  its finalization window (rejects cancels while it finalizes).
- ``on_raw_event`` is an optional private diagnostic sink (activity log);
  it never drives runtime state.
"""

from __future__ import annotations

from typing import Any, Callable

from agent_runtime.backends.base import (
    BackendActivity,
    BackendResult,
    BackendUsage,
)


class RuntimeBackendCallbacks:
    """Runtime-side implementation of ``BackendCallbacks``."""

    def __init__(
        self,
        *,
        on_dispatch_accepted: Callable[[str], None],
        on_activity: Callable[[BackendActivity], None],
        on_usage: Callable[[BackendUsage], None] | None = None,
        on_result: Callable[[BackendResult], None] | None = None,
        on_raw_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._dispatch_accepted = on_dispatch_accepted
        self._activity = on_activity
        self._usage = on_usage
        self._result = on_result
        self._raw_event = on_raw_event

    def on_dispatch_accepted(self, backend_session_id: str) -> None:
        """Persist the backend session/run id; raises on durability failure.

        The backend must not send a real prompt/stream until this returns.
        """
        self._dispatch_accepted(backend_session_id)

    def on_activity(self, activity: BackendActivity) -> None:
        self._activity(activity)

    def on_usage(self, usage: BackendUsage) -> None:
        """Persist a provider-reported usage fact through the Runtime-owned sink."""
        if self._usage is not None:
            self._usage(usage)

    def on_result(self, result: BackendResult) -> None:
        """Mark the execution finished; the runtime closes its finalization window."""
        if self._result is not None:
            self._result(result)

    def on_raw_event(self, event: dict[str, Any]) -> None:
        """Private diagnostic sink (activity log only, never state)."""
        if self._raw_event is not None:
            self._raw_event(event)
