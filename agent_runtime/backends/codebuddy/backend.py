"""CodeBuddy official Python SDK backend behind the shared Runtime contract."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

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
from agent_runtime.backends.codebuddy.process import resolve_codebuddy_cli
from agent_runtime.backends.codebuddy.sdk_client import CodeBuddySdkClient, load_codebuddy_sdk
from agent_runtime.backends.errors import BackendCancelledError, BackendProtocolError
from agent_runtime.domain.dispatch import CommandSpec


@dataclass
class _LiveExecution:
    route: str
    session_id: str = ""
    client: CodeBuddySdkClient | None = None
    cancel_pending: bool = False


class CodeBuddyBackend:
    def __init__(
        self,
        *,
        sdk_client_factory: Callable[..., CodeBuddySdkClient] = CodeBuddySdkClient,
    ) -> None:
        self._sdk_client_factory = sdk_client_factory
        self._lock = threading.Lock()
        self._live: dict[str, _LiveExecution] = {}
        self._pending_cancel: set[str] = set()

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            runtime="codebuddy",
            routes=("sdk_context_read_only", "sdk_patch", "sdk_verify"),
            supports_resume=True,
            supports_streaming=True,
            supports_cancel=True,
            supports_reasoning_effort=False,
            observability="standard",
        )

    def start(self, request: BackendStartRequest, callbacks: BackendCallbacks) -> BackendResult:
        route = str(request.metadata.get("route") or "").strip().lower()
        if route not in {"sdk_context_read_only", "sdk_patch", "sdk_verify"}:
            raise BackendProtocolError(f"Unsupported CodeBuddy route: {route}")
        return self._run_sdk(request, callbacks, resume_session_id="")

    def resume(self, request: BackendResumeRequest, callbacks: BackendCallbacks) -> BackendResult:
        route = str(request.metadata.get("route") or "").strip().lower()
        if route not in {"sdk_context_read_only", "sdk_patch", "sdk_verify"}:
            raise BackendProtocolError("CodeBuddy resume is only supported on controlled SDK routes")
        if not request.resume_session_id:
            raise BackendProtocolError("CodeBuddy SDK resume requires a durable session id")
        return self._run_sdk(
            request,
            callbacks,
            resume_session_id=request.resume_session_id,
        )

    def cancel(self, request: BackendCancelRequest) -> BackendCancelResult:
        with self._lock:
            live = self._live.get(request.task_id)
            if live is None:
                self._pending_cancel.add(request.task_id)
                return BackendCancelResult(
                    ok=True,
                    scope=request.cancel_scope or "codebuddy_sdk",
                    active_execution_found=False,
                    transport_requested=False,
                )
            live.cancel_pending = True
        try:
            if live.client is not None:
                live.client.cancel()
                return BackendCancelResult(
                    ok=True,
                    scope=request.cancel_scope or "codebuddy_sdk",
                    active_execution_found=True,
                    transport_requested=True,
                )
            return BackendCancelResult(
                ok=True,
                scope=request.cancel_scope or "codebuddy_sdk",
                active_execution_found=True,
                transport_requested=False,
            )
        except Exception as exc:
            return BackendCancelResult(
                ok=False,
                scope=request.cancel_scope or "codebuddy_sdk",
                error=type(exc).__name__,
                active_execution_found=True,
                transport_requested=False,
            )

    def reconcile(self, request: BackendReconcileRequest) -> BackendReconcileResult:
        with self._lock:
            live = self._live.get(request.task_id)
        if live and live.client is not None and live.client.running:
            return BackendReconcileResult(
                outcome="orphaned",
                detail={"route": live.route},
            )
        return BackendReconcileResult(
            outcome="unknown",
            detail={"route": request.route},
        )

    def probe(self) -> dict[str, Any]:
        resolve_codebuddy_cli()
        load_codebuddy_sdk()
        # Authentication is intentionally verified by an explicit live task,
        # not by reading or exposing cached credentials in a health probe.
        return {
            "ok": True,
            "runtime": "codebuddy",
            "sdk_installed": True,
            "capabilities": self.capabilities().to_dict(),
        }


    @staticmethod
    def _usage_fact(
        request: BackendStartRequest | BackendResumeRequest,
        raw: dict[str, Any],
        total_cost_usd: float | None,
    ) -> BackendUsage | None:
        if not raw and total_cost_usd is None:
            return None

        def number(*names: str) -> float | int | None:
            for name in names:
                value = raw.get(name)
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)) and value >= 0:
                    return value
            return None

        input_tokens = number("input_tokens", "inputTokens", "prompt_tokens")
        output_tokens = number("output_tokens", "outputTokens", "completion_tokens")
        credits = number("credits_used", "creditsUsed", "credit_used")
        return BackendUsage(
            provider="codebuddy",
            model=request.model,
            source="codebuddy_sdk_result",
            input_tokens=int(input_tokens) if input_tokens is not None else None,
            output_tokens=int(output_tokens) if output_tokens is not None else None,
            credits_used=float(credits) if credits is not None else None,
            reported_cost=(
                float(total_cost_usd)
                if isinstance(total_cost_usd, (int, float)) and not isinstance(total_cost_usd, bool) and total_cost_usd >= 0
                else None
            ),
            currency="USD" if total_cost_usd is not None else None,
            provider_usage=raw,
        )

    def _register(self, task_id: str, live: _LiveExecution) -> None:
        with self._lock:
            if task_id in self._live:
                raise BackendProtocolError("CodeBuddy execution already active for task")
            live.cancel_pending = task_id in self._pending_cancel
            self._pending_cancel.discard(task_id)
            self._live[task_id] = live

    def _unregister(self, task_id: str) -> None:
        with self._lock:
            self._live.pop(task_id, None)
            self._pending_cancel.discard(task_id)

    def _run_sdk(
        self,
        request: BackendStartRequest | BackendResumeRequest,
        callbacks: BackendCallbacks,
        *,
        resume_session_id: str,
    ) -> BackendResult:
        route = str(request.metadata.get("route") or "sdk_context_read_only").strip().lower()
        plan = request.metadata.get("patch_policy")
        plan = plan if isinstance(plan, dict) else {}
        raw_specs = plan.get("commands", plan.get("command_specs"))
        command_specs: list[CommandSpec] = []
        if isinstance(raw_specs, list):
            for item in raw_specs:
                try:
                    command_specs.append(CommandSpec.from_dict(item))
                except ValueError:
                    raise BackendProtocolError("CodeBuddy patch policy contains an invalid command spec")
        if route in {"sdk_patch", "sdk_verify"}:
            client = self._sdk_client_factory(
                cwd=request.cwd,
                on_activity=callbacks.on_activity,
                access_mode=("patch" if route == "sdk_patch" else "verification"),
                allowed_paths=tuple(str(item) for item in plan.get("allowed_paths", []) if isinstance(item, str)),
                forbidden_paths=tuple(str(item) for item in plan.get("forbidden_paths", []) if isinstance(item, str)),
                command_specs=tuple(command_specs),
            )
        else:
            # Preserve the long-standing custom transport/factory contract for
            # the accepted T3 read-only route.  Patch-only policy parameters
            # are passed only when the new patch route actually needs them.
            client = self._sdk_client_factory(
                cwd=request.cwd,
                on_activity=callbacks.on_activity,
            )
        live = _LiveExecution(route=route, client=client)
        self._register(request.task_id, live)
        try:
            if live.cancel_pending:
                client.cancel()
                raise BackendCancelledError("CodeBuddy execution cancelled before dispatch")

            def accepted(session_id: str) -> None:
                live.session_id = session_id
                callbacks.on_dispatch_accepted(session_id)
                if live.cancel_pending:
                    client.cancel()

            result = client.run(
                prompt=request.prompt,
                resume_session_id=resume_session_id,
                model=request.model,
                idle_timeout_seconds=request.idle_timeout_seconds,
                max_task_duration_seconds=request.max_task_duration_seconds,
                on_dispatch_accepted=accepted,
            )
            usage_fact = self._usage_fact(request, dict(result.usage or {}), result.total_cost_usd)
            if usage_fact is not None:
                usage_sink = getattr(callbacks, "on_usage", None)
                if callable(usage_sink):
                    usage_sink(usage_fact)
            backend_result = BackendResult(
                backend="codebuddy",
                stop_reason=result.stop_reason,
                answer=result.answer,
                result={
                    "answer": result.answer,
                    "backend": "codebuddy",
                    "stopReason": result.stop_reason,
                    "model_applied": bool(request.model) if request.model else None,
                    "usage": usage_fact.to_dict() if usage_fact is not None else {},
                    "total_cost_usd": result.total_cost_usd,
                },
                observability={**result.observability},
                backend_session_id=result.session_id,
            )
            callbacks.on_result(backend_result)
            return backend_result
        finally:
            client.close()
            self._unregister(request.task_id)
