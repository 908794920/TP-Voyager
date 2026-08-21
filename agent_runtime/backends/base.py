"""Sub-agent backend contract — minimal protocol for task execution backends.

A Backend is responsible for executing a prompt against an external AI worker and reporting back activity and final results.  It is
NOT responsible for task state, idempotency, persistence, or public projections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent_runtime.backends.errors import BackendError


_SAFE_PROVIDER_USAGE_KEYS = frozenset({
    "input_tokens", "inputTokens", "prompt_tokens",
    "output_tokens", "outputTokens", "completion_tokens",
    "total_tokens", "totalTokens",
    "cache_read_tokens", "cacheReadTokens", "cache_read_input_tokens",
    "cached_input_tokens", "cachedInputTokens", "cached_tokens", "cachedTokens",
    "cache_miss_tokens", "cacheMissTokens",
    "cache_write_tokens", "cacheWriteTokens", "cache_write_input_tokens",
    "cache_creation_input_tokens", "cacheCreationInputTokens",
    "reasoning_tokens", "reasoningTokens", "thinking_tokens", "thinkingTokens",
    "answer_tokens", "answerTokens", "response_tokens", "responseTokens",
    "credit", "credits", "credits_used", "creditsUsed", "credit_used",
    "total_credits", "totalCredits", "original_credits", "originalCredits",
    "billable",
})


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
    """Provider-reported Token/Credit usage fact.

    Missing values stay ``None``.  TP-Voyager never estimates Credits.  The
    legacy ``credits_used`` field remains accepted as a constructor alias so
    older backend/tests keep working, while the durable/public contract uses
    ``credits`` for one turn/request and ``session_credits`` for a provider
    cumulative session total.
    """

    provider: str
    scope: str = "turn"
    model: str = ""
    source: str = ""
    # Accounting semantics for this provider sample. ``delta`` facts may be
    # added across distinct request/turn identities; ``snapshot`` facts are
    # latest-value observations and must never be cumulatively re-added.
    accounting: str = "delta"
    sample_id: str = ""
    total_tokens: int | None = None
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_miss_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    answer_tokens: int | None = None
    credits: float | None = None
    session_credits: float | None = None
    original_credits: float | None = None
    billable: bool | None = None
    derived_fields: tuple[str, ...] = ()
    model_usage: dict[str, Any] = field(default_factory=dict)
    # Backward-compatible input alias.  New code should use ``credits``.
    credits_used: float | None = None
    # Retained internally for backward compatibility with old result payloads;
    # the Voyager panel does not project monetary billing fields.
    reported_cost: float | None = None
    currency: str | None = None
    provider_usage: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _non_negative(value: int | float | None) -> int | float | None:
        if value is None or isinstance(value, bool):
            return None
        return value if value >= 0 else None

    @classmethod
    def _safe_model_usage(cls, value: Any) -> dict[str, Any]:
        """Keep only bounded model identifiers + scalar Token/Credit facts."""
        if not isinstance(value, dict):
            return {}
        output: dict[str, Any] = {}
        for model, raw in list(value.items())[:32]:
            if not isinstance(raw, dict):
                continue
            safe: dict[str, Any] = {}
            for key, item in list(raw.items())[:32]:
                if isinstance(item, bool):
                    if str(key) == "billable":
                        safe[str(key)[:80]] = item
                elif isinstance(item, (int, float)) and item >= 0:
                    safe[str(key)[:80]] = item
            if safe:
                output[str(model)[:160]] = safe
        return output

    def to_dict(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        for key, value in list(self.provider_usage.items())[:32]:
            # Provider payloads are untrusted and may also contain prompts,
            # replies, command text, raw tool I/O or host paths.  Persist only
            # known usage scalar keys; arbitrary strings never cross this gate.
            safe_key = str(key)[:80]
            if safe_key not in _SAFE_PROVIDER_USAGE_KEYS:
                continue
            if isinstance(value, bool):
                if safe_key == "billable":
                    raw[safe_key] = value
            elif isinstance(value, (int, float)) and value >= 0:
                raw[safe_key] = value

        canonical_credits = self.credits if self.credits is not None else self.credits_used
        usage = {
            "total_tokens": self._non_negative(self.total_tokens),
            "input_tokens": self._non_negative(self.input_tokens),
            "cache_read_tokens": self._non_negative(self.cache_read_tokens),
            "cache_miss_tokens": self._non_negative(self.cache_miss_tokens),
            "cache_write_tokens": self._non_negative(self.cache_write_tokens),
            "output_tokens": self._non_negative(self.output_tokens),
            "reasoning_tokens": self._non_negative(self.reasoning_tokens),
            "answer_tokens": self._non_negative(self.answer_tokens),
            "credits": self._non_negative(canonical_credits),
            "session_credits": self._non_negative(self.session_credits),
            "original_credits": self._non_negative(self.original_credits),
            "billable": self.billable if isinstance(self.billable, bool) else None,
            "derived_fields": sorted({str(item) for item in self.derived_fields if str(item).strip()}),
            # Legacy alias used by v1.0.5 flow-control readers.  It is the same
            # provider-reported turn/request value, never a second quantity.
            "credits_used": self._non_negative(canonical_credits),
            "reported_cost": self._non_negative(self.reported_cost),
            "currency": str(self.currency or "")[:16] or None,
        }
        return {
            "schema": "tp-voyager.usage/v1",
            "provider": str(self.provider or "")[:80],
            "scope": self.scope if self.scope in {"turn", "session"} else "turn",
            "model": str(self.model or "")[:160] or None,
            "source": str(self.source or "")[:120],
            "accounting": self.accounting if self.accounting in {"delta", "snapshot"} else "delta",
            "sample_id": str(self.sample_id or "")[:160] or None,
            "usage": usage,
            "model_usage": self._safe_model_usage(self.model_usage),
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
