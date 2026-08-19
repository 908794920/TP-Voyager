"""Sub-agent backend contract — minimal protocol for task execution backends.

A Backend is responsible for executing a prompt against an external AI worker and reporting back activity and final results.  It is
NOT responsible for task state, idempotency, persistence, or public projections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent_runtime.backends.errors import BackendError


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------




@dataclass(frozen=True)
class BackendCapabilities:
    """Stable capability declaration used by routing and public health APIs."""

    runtime: str
    routes: tuple[str, ...]
    supports_resume: bool = False
    supports_streaming: bool = False
    supports_cancel: bool = True
    supports_reasoning_effort: bool = False
    observability: str = "standard"

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "routes": list(self.routes),
            "supports_resume": self.supports_resume,
            "supports_streaming": self.supports_streaming,
            "supports_cancel": self.supports_cancel,
            "supports_reasoning_effort": self.supports_reasoning_effort,
            "observability": self.observability,
        }


@dataclass
class BackendStartRequest:
    """Request to start a new task on a backend."""
    task_id: str
    attempt_id: str
    runtime_session_id: str
    prompt: str
    cwd: str
    model: str = ""
    reasoning_effort: str = ""
    context_window_tokens: int | None = None
    identity: str = ""
    idle_timeout_seconds: float = 180.0
    max_task_duration_seconds: float = 1800.0
    # Backend-specific metadata (never prompt, never secret).
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackendResumeRequest:
    """Request to resume/review on an existing backend session."""
    task_id: str
    attempt_id: str
    runtime_session_id: str
    prompt: str
    cwd: str
    model: str = ""
    reasoning_effort: str = ""
    context_window_tokens: int | None = None
    identity: str = ""
    resume_session_id: str = ""
    review_target: str = ""
    resume_review: bool = False
    idle_timeout_seconds: float = 180.0
    max_task_duration_seconds: float = 1800.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackendCancelRequest:
    """Request to cancel a running backend execution."""
    task_id: str
    attempt_id: str
    backend_session_id: str = ""
    cancel_scope: str = ""


@dataclass
class BackendExecution:
    """Live transport handle for one running backend execution.

    Kept in the backend's Live Execution Registry while the execution is
    active so that ``cancel()`` (called from another thread) can reach the
    exact transport (runtime host / ACP client) this execution uses.  It
    holds no task state.
    """
    backend_session_id: str = ""
    # Thread-safe transport cancel callable (captures the live host/client).
    cancel: Callable[[], None] | None = None




@dataclass(frozen=True)
class BackendUsage:
    """Provider-reported usage fact.  Values are recorded, never priced or inferred."""

    provider: str
    model: str = ""
    source: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    credits_used: float | None = None
    reported_cost: float | None = None
    currency: str | None = None
    provider_usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def non_negative(value: int | float | None) -> int | float | None:
            if value is None:
                return None
            return value if value >= 0 else None

        raw: dict[str, Any] = {}
        for key, value in list(self.provider_usage.items())[:32]:
            if isinstance(value, (str, int, float, bool)) or value is None:
                raw[str(key)[:80]] = value
        return {
            "schema": "tp-voyager.usage/v1",
            "provider": str(self.provider or "")[:80],
            "model": str(self.model or "")[:160] or None,
            "source": str(self.source or "")[:120],
            "usage": {
                "input_tokens": non_negative(self.input_tokens),
                "output_tokens": non_negative(self.output_tokens),
                "credits_used": non_negative(self.credits_used),
                "reported_cost": non_negative(self.reported_cost),
                "currency": str(self.currency or "")[:16] or None,
            },
            "provider_usage": raw,
        }


@dataclass
class BackendResult:
    """Final result from a backend execution."""
    backend: str = ""
    stop_reason: str = ""
    answer: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    observability: dict[str, Any] = field(default_factory=dict)
    backend_session_id: str = ""
    title: str = ""


@dataclass
class BackendActivity:
    """Bounded activity event from a backend.

    ``detail`` may carry an allow-listed human-observability projection such
    as provider-visible assistant text or safe tool metadata.  Raw prompts,
    secrets, private chain-of-thought, and raw tool output never belong here.
    Durable TaskEvent persistence remains content-free.
    """
    kind: str = ""
    timestamp: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    # Backend-specific, never projected publicly.
    backend_event: dict[str, Any] | None = None


@dataclass
class BackendCancelResult:
    """Outcome of a backend cancel request.

    ``active_execution_found`` tells the runtime whether a live transport
    was registered for the task; ``transport_requested`` tells whether a
    cancel was actually sent to it (an early cancel may be pending).
    """
    ok: bool = True
    scope: str = ""
    error: str = ""
    active_execution_found: bool = False
    transport_requested: bool = False


@dataclass
class BackendReconcileRequest:
    """PR3: ask a backend to classify a stale task's backend truth.

    The runtime supplies only what is durably known; the backend must
    NEVER dispatch a new prompt or start a new run from here.
    """
    task_id: str
    backend_session_id: str = ""
    route: str = ""
    started_at: float | None = None


@dataclass
class BackendReconcileResult:
    """PR3: what the backend could determine about a stale task.

    ``outcome`` is one of:
    - ``"terminal_completed"`` / ``"terminal_failed"`` / ``"terminal_cancelled"``
      (a definitive terminal signal was found);
    - ``"unknown"`` (cannot determine truth -> runtime marks LOST);
    - ``"orphaned"`` (a live local host exists but cannot be rebound ->
      runtime marks ORPHANED).
    """
    outcome: str = "unknown"
    error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Callback protocol (Runtime -> Backend)
# ---------------------------------------------------------------------------


class BackendCallbacks(Protocol):
    """Callbacks a backend may invoke to notify the runtime of events.

    The runtime decides how to persist these events; the backend only
    produces facts.  In the synchronous contract the final result is
    returned and errors are raised directly, so the callback surface is
    intentionally minimal: only dispatch acceptance (a hard gate before
    any real prompt), typed activity, and the final-result marker that
    closes the runtime's finalization window.
    """

    def on_dispatch_accepted(self, backend_session_id: str) -> None:
        ...

    def on_activity(self, activity: BackendActivity) -> None:
        ...

    def on_usage(self, usage: BackendUsage) -> None:
        """Report provider-returned usage without estimating missing values."""
        ...

    def on_result(self, result: BackendResult) -> None:
        """The backend produced its final result.

        The runtime marks the task execution finished here so cancels
        arriving while the runtime finalizes (history sync + terminal
        persistence) are rejected instead of accepted into a window that
        can no longer be cancelled.
        """
        ...


# ---------------------------------------------------------------------------
# Backend contract
# ---------------------------------------------------------------------------


class SubAgentBackend(Protocol):
    """Contract for a sub-agent execution backend.

    Implementations may include Qoder, CodeBuddy, test fakes, and future official CLI adapters.

    The contract is synchronous: the runtime worker runs the backend on its
    own thread, so ``start`` / ``resume`` execute the full transport
    lifecycle (dispatch, stream, cleanup) and return the final
    ``BackendResult`` (or raise a ``BackendError``).  Cancellation is the
    only cross-thread call: ``cancel`` reaches the live transport through
    the backend's execution registry.
    """

    def start(
        self,
        request: BackendStartRequest,
        callbacks: BackendCallbacks,
    ) -> BackendResult:
        """Execute a new task to completion and return the final result."""
        ...

    def resume(
        self,
        request: BackendResumeRequest,
        callbacks: BackendCallbacks,
    ) -> BackendResult:
        """Resume/review an existing session to completion."""
        ...

    def cancel(
        self,
        request: BackendCancelRequest,
    ) -> BackendCancelResult:
        """Request cancellation of a running execution (cross-thread)."""
        ...

    def reconcile(
        self,
        request: BackendReconcileRequest,
    ) -> BackendReconcileResult:
        """PR3: classify a stale task's backend truth (never dispatches).

        Called by restart reconciliation for non-terminal tasks whose
        original worker is gone.  Implementations must not send prompts or
        start runs; they only inspect durable identifiers / history.
        """
        ...

    def capabilities(self) -> BackendCapabilities:
        """Return a stable declaration; must not probe or dispatch."""
        ...

    def probe(self) -> dict[str, Any]:
        """Check backend connectivity; raise BackendError when unavailable."""
        ...
