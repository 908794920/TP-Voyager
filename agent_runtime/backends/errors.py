"""Backend-specific error hierarchy.

Backend errors signal transport/protocol failures to the runtime.
The runtime decides how (and whether) to update task state.
"""

from __future__ import annotations


class BackendError(RuntimeError):
    """Base class for all backend transport errors."""


class BackendDispatchError(BackendError):
    """Failed to dispatch a task to the backend."""


class BackendTimeoutError(BackendError):
    """Backend execution timed out."""
    def __init__(self, message: str = "", *, timeout_reason: str = "") -> None:
        super().__init__(message)
        self.timeout_reason = timeout_reason


class BackendCancelledError(BackendError):
    """Backend acknowledged cancellation."""


class BackendProtocolError(BackendError):
    """Backend returned an unexpected or malformed response."""


class BackendUnavailableError(BackendError):
    """Backend is not available (not running, not reachable)."""