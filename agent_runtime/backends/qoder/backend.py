"""Controlled Qoder ACP routes behind the common Crew backend contract."""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from agent_runtime.backends.base import (
    BackendCallbacks,
    BackendCancelRequest,
    BackendCancelResult,
    BackendCapabilities,
    BackendReconcileRequest,
    BackendReconcileResult,
    BackendResumeRequest,
    BackendResult,
    BackendStartRequest,
    BackendUsage,
)
from agent_runtime.domain.dispatch import CommandSpec
from agent_runtime.backends.errors import (
    BackendCancelledError,
    BackendProtocolError,
)
from agent_runtime.backends.qoder.acp_client import QoderAcpClient
from agent_runtime.backends.qoder.process import resolve_qoder_cli

@dataclass
class _LiveExecution:
    route: str
    session_id: str = ""
    client: QoderAcpClient | None = None
    cancel_pending: bool = False


def _cleanup_read_scope_snapshot(snapshot: tempfile.TemporaryDirectory[str]) -> bool:
    """Best-effort cleanup that never converts a completed Crew task to failed.

    On Windows, ``taskkill /T`` can return just before a Qoder descendant has
    released the snapshot directory.  Retrying the narrow cleanup briefly
    handles that normal race.  A remaining lock leaves only an OS-temp
    directory for later cleanup; it must not erase an already obtained result.
    """
    for attempt in range(20):
        try:
            snapshot.cleanup()
            return True
        except PermissionError:
            if attempt < 19:
                time.sleep(0.1)
    return False



def _materialize_read_scope_snapshot(source_cwd: str, resolved_files: tuple[str, ...]) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Create a disposable Qoder cwd containing only approved read-scope files.

    This is workspace-exposure isolation, not an OS sandbox.  The local Qoder
    process still runs with the host user's privileges, but the Passenger repo
    itself is no longer the process cwd for bounded read-only routes.
    """
    source_root = Path(source_cwd).resolve(strict=True)
    temp = tempfile.TemporaryDirectory(prefix="tp-voyager-qoder-readonly-")
    snapshot_root = Path(temp.name)
    try:
        if not resolved_files:
            raise BackendProtocolError("Qoder read-only route requires a non-empty resolved read_scope")
        for raw in resolved_files:
            normalized = str(raw or "").strip().replace("\\", "/")
            pure = PurePosixPath(normalized)
            if (
                not normalized
                or normalized.startswith("/")
                or (len(normalized) >= 2 and normalized[1] == ":")
                or any(part in {"", ".", ".."} for part in pure.parts)
            ):
                raise BackendProtocolError("Qoder read_scope contains an unsafe relative path")
            source = (source_root / Path(*pure.parts)).resolve(strict=True)
            try:
                source.relative_to(source_root)
            except ValueError as exc:
                raise BackendProtocolError("Qoder read_scope resolves outside the source workspace") from exc
            if not source.is_file():
                raise BackendProtocolError("Qoder read_scope contains a non-file entry")
            destination = snapshot_root / Path(*pure.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
        return temp, snapshot_root
    except Exception:
        temp.cleanup()
        raise


class QoderBackend:
    """One backend, two explicit routes; ACP never silently falls back."""

    def __init__(
        self,
        *,
        read_only_acp_client_factory=None,
        patch_acp_client_factory=None,
        verification_acp_client_factory=None,
    ) -> None:
        self._read_only_acp_client_factory = (
            read_only_acp_client_factory
            or (lambda **kwargs: QoderAcpClient(read_only=True, allow_permissions=False, **kwargs))
        )
        self._patch_acp_client_factory = patch_acp_client_factory or (
            lambda **kwargs: QoderAcpClient(
                read_only=False, allow_permissions=True, **kwargs
            )
        )
        self._verification_acp_client_factory = verification_acp_client_factory or (
            lambda **kwargs: QoderAcpClient(
                read_only=False, allow_permissions=True,
                allow_file_writes=False, allow_terminal=True, **kwargs
            )
        )
        self._lock = threading.RLock()
        self._live: dict[str, _LiveExecution] = {}
        self._pending_cancel: set[str] = set()

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            runtime="qoder",
            routes=("acp_read_only", "acp_patch", "acp_verify"),
            supports_resume=True,
            supports_streaming=True,
            supports_reasoning_effort=True,
            observability="standard",
        )

    def start(self, request: BackendStartRequest, callbacks: BackendCallbacks) -> BackendResult:
        route = str(request.metadata.get("route") or "acp_read_only").lower()
        if route == "acp_read_only":
            return self._run_acp(request, callbacks, resume_session_id="", mode="read_only")
        if route == "acp_patch":
            return self._run_acp(request, callbacks, resume_session_id="", mode="patch")
        if route == "acp_verify":
            return self._run_acp(request, callbacks, resume_session_id="", mode="verification")
        raise BackendProtocolError(f"Unsupported Qoder controlled route: {route}")

    def resume(self, request: BackendResumeRequest, callbacks: BackendCallbacks) -> BackendResult:
        route = str(request.metadata.get("route") or "acp_read_only").lower()
        if route not in {"acp_read_only", "acp_patch", "acp_verify"}:
            raise BackendProtocolError("Qoder resume is only supported on controlled ACP routes")
        if not request.resume_session_id:
            raise BackendProtocolError("Qoder ACP resume requires a durable session id")
        return self._run_acp(
            request,
            callbacks,
            resume_session_id=request.resume_session_id,
            mode=("read_only" if route == "acp_read_only" else ("verification" if route == "acp_verify" else "patch")),
        )

    def cancel(self, request: BackendCancelRequest) -> BackendCancelResult:
        with self._lock:
            live = self._live.get(request.task_id)
            if live is None:
                self._pending_cancel.add(request.task_id)
                return BackendCancelResult(
                    ok=True,
                    scope=request.cancel_scope or "process",
                    active_execution_found=False,
                    transport_requested=False,
                )
        try:
            live.cancel_pending = True
            if live.client is not None:
                live.client.cancel(live.session_id or request.backend_session_id)
            else:
                live.cancel_pending = True
                return BackendCancelResult(
                    ok=True,
                    scope=request.cancel_scope or live.route,
                    active_execution_found=True,
                    transport_requested=False,
                )
            return BackendCancelResult(
                ok=True,
                scope=request.cancel_scope or live.route,
                active_execution_found=True,
                transport_requested=True,
            )
        except Exception as exc:
            return BackendCancelResult(
                ok=False,
                scope=request.cancel_scope or live.route,
                error=str(exc),
                active_execution_found=True,
                transport_requested=False,
            )

    def reconcile(self, request: BackendReconcileRequest) -> BackendReconcileResult:
        # A Runtime restart cannot safely rebind the old local CLI process.
        # Never re-dispatch; distinguish a known local live handle only.
        with self._lock:
            live = self._live.get(request.task_id)
        if live and (
            live.client is not None and live.client.process.poll() is None
        ):
            return BackendReconcileResult(outcome="orphaned", detail={"route": live.route})
        return BackendReconcileResult(outcome="unknown", detail={"route": request.route})

    def probe(self) -> dict[str, Any]:
        resolve_qoder_cli()
        return {"ok": True, "runtime": "qoder", "capabilities": self.capabilities().to_dict()}


    @staticmethod
    def _usage_fact(
        request: BackendStartRequest | BackendResumeRequest,
        raw: dict[str, Any],
    ) -> BackendUsage | None:
        if not raw:
            return None

        def number(*names: str) -> float | int | None:
            for name in names:
                value = raw.get(name)
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)) and value >= 0:
                    return value
            return None

        input_tokens = number("inputTokens", "input_tokens", "prompt_tokens")
        output_tokens = number("outputTokens", "output_tokens", "completion_tokens")
        credits = number("creditsUsed", "credits_used", "credit_used")
        reported_cost = number("cost", "reported_cost", "total_cost")
        return BackendUsage(
            provider="qoder",
            model=request.model,
            source="qoder_acp_usage_update",
            input_tokens=int(input_tokens) if input_tokens is not None else None,
            output_tokens=int(output_tokens) if output_tokens is not None else None,
            credits_used=float(credits) if credits is not None else None,
            reported_cost=float(reported_cost) if reported_cost is not None else None,
            provider_usage=raw,
        )

    def _register(self, task_id: str, live: _LiveExecution) -> None:
        with self._lock:
            if task_id in self._live:
                raise BackendProtocolError("Qoder execution already active for task")
            live.cancel_pending = task_id in self._pending_cancel
            self._pending_cancel.discard(task_id)
            self._live[task_id] = live

    def _unregister(self, task_id: str) -> None:
        with self._lock:
            self._live.pop(task_id, None)
            self._pending_cancel.discard(task_id)

    def _run_acp(
        self,
        request: BackendStartRequest | BackendResumeRequest,
        callbacks: BackendCallbacks,
        *,
        resume_session_id: str,
        mode: str,
    ) -> BackendResult:
        plan = request.metadata.get("patch_policy")
        plan = plan if isinstance(plan, dict) else {}
        raw_specs = plan.get("commands", plan.get("command_specs"))
        command_specs: list[CommandSpec] = []
        if isinstance(raw_specs, list):
            for item in raw_specs:
                try:
                    command_specs.append(CommandSpec.from_dict(item))
                except ValueError as exc:
                    raise BackendProtocolError("Qoder patch policy contains an invalid command spec") from exc
        read_scope_snapshot: tempfile.TemporaryDirectory[str] | None = None
        if mode == "read_only":
            routing = request.metadata.get("routing_metadata")
            routing = routing if isinstance(routing, dict) else {}
            read_scope = routing.get("read_scope")
            read_scope = read_scope if isinstance(read_scope, dict) else None
            factory = self._read_only_acp_client_factory
            if read_scope is None:
                client = factory(cwd=request.cwd, on_activity=callbacks.on_activity)
            else:
                resolved_files = tuple(
                    str(item) for item in read_scope.get("resolved_files", [])
                    if isinstance(item, str) and item
                )
                read_scope_snapshot, snapshot_root = _materialize_read_scope_snapshot(
                    request.cwd, resolved_files
                )
                try:
                    client = factory(
                        cwd=str(snapshot_root),
                        on_activity=callbacks.on_activity,
                        context_window_tokens=request.context_window_tokens,
                        allowed_paths=resolved_files,
                        forbidden_paths=(".git", ".codebuddy", ".qoder"),
                    )
                except Exception:
                    read_scope_snapshot.cleanup()
                    read_scope_snapshot = None
                    raise
            route = "acp_read_only"
        elif mode in {"patch", "verification"}:
            factory = self._patch_acp_client_factory if mode == "patch" else self._verification_acp_client_factory
            client = factory(
                cwd=request.cwd,
                on_activity=callbacks.on_activity,
                context_window_tokens=request.context_window_tokens,
                allowed_paths=tuple(str(item) for item in plan.get("allowed_paths", []) if isinstance(item, str)),
                forbidden_paths=tuple(str(item) for item in plan.get("forbidden_paths", []) if isinstance(item, str)),
                command_specs=tuple(command_specs),
            )
            route = "acp_patch" if mode == "patch" else "acp_verify"
        live = _LiveExecution(route=route, client=client)
        self._register(request.task_id, live)
        try:
            if live.cancel_pending:
                client.cancel()
                raise BackendCancelledError("Qoder execution cancelled before dispatch")

            def accepted(session_id: str) -> None:
                live.session_id = session_id
                callbacks.on_dispatch_accepted(session_id)
                if live.cancel_pending:
                    client.cancel(session_id)

            result = client.run(
                prompt=request.prompt,
                resume_session_id=resume_session_id,
                model=request.model,
                reasoning_effort=request.reasoning_effort,
                context_window_tokens=request.context_window_tokens,
                idle_timeout_seconds=request.idle_timeout_seconds,
                max_task_duration_seconds=request.max_task_duration_seconds,
                on_dispatch_accepted=accepted,
            )
            usage_fact = self._usage_fact(request, dict(result.usage or {}))
            if usage_fact is not None:
                usage_sink = getattr(callbacks, "on_usage", None)
                if callable(usage_sink):
                    usage_sink(usage_fact)
            backend_result = BackendResult(
                backend="qoder",
                stop_reason=result.stop_reason,
                answer=result.answer,
                result={
                    "answer": result.answer,
                    "backend": "qoder",
                    "stopReason": result.stop_reason,
                    "reasoning_effort_applied": result.reasoning_effort_applied,
                    "context_window_tokens_applied": result.context_window_tokens_applied,
                    "model_applied": result.model_applied,
                    "usage": usage_fact.to_dict() if usage_fact is not None else {},
                },
                observability={
                    **result.observability,
                    "access_mode": mode,
                    "command_whitelist_size": len(command_specs) if mode in {"patch", "verification"} else 0,
                },
                backend_session_id=result.session_id,
            )
            callbacks.on_result(backend_result)
            return backend_result
        finally:
            # ACP may report usage before a timeout/cancel/protocol failure.
            # Capture that observed fact even when no terminal result exists.
            try:
                raw_usage = client.usage_snapshot()
                usage_fact = self._usage_fact(request, raw_usage)
                usage_sink = getattr(callbacks, "on_usage", None)
                if usage_fact is not None and callable(usage_sink):
                    usage_sink(usage_fact)
            except Exception:
                # Usage evidence is auxiliary and must never mask the backend
                # execution outcome.  The Runtime records only facts it got.
                pass
            client.close()
            self._unregister(request.task_id)
            if read_scope_snapshot is not None:
                _cleanup_read_scope_snapshot(read_scope_snapshot)
