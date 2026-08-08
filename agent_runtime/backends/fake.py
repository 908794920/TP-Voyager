"""Fake backend for testing the SubAgentBackend contract.

A FakeBackend exercises the shared Runtime/Application backend contract so
logic can be verified without a real desktop server.  It is fully headless:
no discovery, no transport, no host lifecycle.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.backends.base import (
    BackendActivity,
    BackendCallbacks,
    BackendCancelRequest,
    BackendCancelResult,
    BackendCapabilities,
    BackendReconcileRequest,
    BackendReconcileResult,
    BackendResumeRequest,
    BackendResult,
    BackendStartRequest,
)


class FakeBackend:
    """Deterministic fake backend (SubAgentBackend contract).

    ``start`` / ``resume`` execute synchronously and return a configured
    ``BackendResult`` (or raise a configured error).  Cancellation is
    recorded; the result can be configured to surface ``cancelled``.
    """

    def __init__(
        self,
        *,
        result: BackendResult | None = None,
        error: Exception | None = None,
        reconcile_result: BackendReconcileResult | None = None,
        reconcile_error: Exception | None = None,
    ) -> None:
        self._result = result or BackendResult(
            backend="fake",
            stop_reason="end_turn",
            answer="fake result",
            result={"backend": "fake", "stopReason": "end_turn"},
        )
        self._error = error
        self._reconcile_result = reconcile_result or BackendReconcileResult()
        self._reconcile_error = reconcile_error
        self.starts: list[BackendStartRequest] = []
        self.resumes: list[BackendResumeRequest] = []
        self.cancels: list[BackendCancelRequest] = []
        self.dispatch_accepted: list[str] = []
        self.activities: list[BackendActivity] = []
        self.reconcile_calls: list[BackendReconcileRequest] = []

    def start(
        self,
        request: BackendStartRequest,
        callbacks: BackendCallbacks,
    ) -> BackendResult:
        """Synchronous fake execution of a new task."""
        self.starts.append(request)
        callbacks.on_dispatch_accepted("fake-session")
        self.dispatch_accepted.append("fake-session")
        callbacks.on_activity(
            BackendActivity(kind="prompt_accepted", timestamp=0.0)
        )
        if self._error:
            raise self._error
        # Finalization contract: every successful backend calls on_result
        # exactly once before returning (mirrors Gateway/ACP routes).
        callbacks.on_result(self._result)
        return self._result

    def resume(
        self,
        request: BackendResumeRequest,
        callbacks: BackendCallbacks,
    ) -> BackendResult:
        """Synchronous fake resume of an existing session."""
        self.resumes.append(request)
        callbacks.on_dispatch_accepted("fake-resume-session")
        self.dispatch_accepted.append("fake-resume-session")
        if self._error:
            raise self._error
        callbacks.on_result(self._result)
        return self._result

    def cancel(self, request: BackendCancelRequest) -> BackendCancelResult:
        self.cancels.append(request)
        return BackendCancelResult(
            ok=True,
            scope=request.cancel_scope,
            active_execution_found=True,
            transport_requested=True,
        )

    def reconcile(
        self,
        request: BackendReconcileRequest,
    ) -> BackendReconcileResult:
        """Deterministic fake classification (never dispatches)."""
        self.reconcile_calls.append(request)
        if self._reconcile_error:
            raise self._reconcile_error
        return self._reconcile_result

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            runtime="fake", routes=("fake",), supports_resume=True,
            supports_streaming=True, supports_reasoning_effort=True,
        )

    def probe(self) -> dict[str, Any]:
        return {
            "connected": True,
            "endpoint": "fake://acp",
            "sidecar_pid": 0,
            "host_session_id": "fake-host",
        }
