"""CodeBuddy native ACP backend with explicit SDK compatibility routes."""

from __future__ import annotations

import tempfile
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
from agent_runtime.backends.codebuddy.acp_client import CodeBuddyAcpClient
from agent_runtime.backends.codebuddy.process import resolve_codebuddy_cli
from agent_runtime.backends.codebuddy.sdk_client import CodeBuddySdkClient
from agent_runtime.backends.errors import BackendCancelledError, BackendProtocolError
from agent_runtime.backends.workspace_snapshot import materialize_workspace_snapshot
from agent_runtime.domain.dispatch import CommandSpec, _MANDATORY_FORBIDDEN


@dataclass
class _LiveExecution:
    route: str
    session_id: str = ""
    client: Any | None = None
    cancel_pending: bool = False


class CodeBuddyBackend:
    def __init__(
        self,
        *,
        acp_client_factory: Callable[..., Any] = CodeBuddyAcpClient,
        sdk_client_factory: Callable[..., CodeBuddySdkClient] = CodeBuddySdkClient,
    ) -> None:
        self._acp_client_factory = acp_client_factory
        self._sdk_client_factory = sdk_client_factory
        self._lock = threading.Lock()
        self._live: dict[str, _LiveExecution] = {}
        self._pending_cancel: set[str] = set()

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            runtime="codebuddy",
            routes=("acp_read_only", "acp_patch", "acp_verify", "sdk_context_read_only", "sdk_patch", "sdk_verify"),
            supports_resume=True,
            supports_streaming=True,
            supports_cancel=True,
            supports_reasoning_effort=True,
            observability="standard",
        )

    def start(self, request: BackendStartRequest, callbacks: BackendCallbacks) -> BackendResult:
        route = str(request.metadata.get("route") or "acp_read_only").strip().lower()
        if route in {"acp_read_only", "acp_patch", "acp_verify"}:
            return self._run_acp(request, callbacks, resume_session_id="")
        if route in {"sdk_context_read_only", "sdk_patch", "sdk_verify"}:
            return self._run_sdk(request, callbacks, resume_session_id="")
        raise BackendProtocolError(f"Unsupported CodeBuddy route: {route}")

    def resume(self, request: BackendResumeRequest, callbacks: BackendCallbacks) -> BackendResult:
        route = str(request.metadata.get("route") or "acp_read_only").strip().lower()
        if route not in self.capabilities().routes:
            raise BackendProtocolError("CodeBuddy resume route is unsupported")
        if not request.resume_session_id:
            raise BackendProtocolError("CodeBuddy resume requires a durable session id")
        if route.startswith("acp_"):
            return self._run_acp(request, callbacks, resume_session_id=request.resume_session_id)
        return self._run_sdk(request, callbacks, resume_session_id=request.resume_session_id)

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
                if live.route.startswith("acp_"):
                    live.client.cancel(live.session_id or request.backend_session_id)
                else:
                    live.client.cancel()
                return BackendCancelResult(
                    ok=True,
                    scope=request.cancel_scope or ("codebuddy_acp" if live.route.startswith("acp_") else "codebuddy_sdk"),
                    active_execution_found=True,
                    transport_requested=True,
                )
            return BackendCancelResult(
                ok=True,
                scope=request.cancel_scope or ("codebuddy_acp" if live.route.startswith("acp_") else "codebuddy_sdk"),
                active_execution_found=True,
                transport_requested=False,
            )
        except Exception as exc:
            return BackendCancelResult(
                ok=False,
                scope=request.cancel_scope or ("codebuddy_acp" if live.route.startswith("acp_") else "codebuddy_sdk"),
                error=type(exc).__name__,
                active_execution_found=True,
                transport_requested=False,
            )

    def reconcile(self, request: BackendReconcileRequest) -> BackendReconcileResult:
        with self._lock:
            live = self._live.get(request.task_id)
        if live and live.client is not None and (
            (live.route.startswith("acp_") and getattr(getattr(live.client, "process", None), "poll", lambda: 0)() is None)
            or (not live.route.startswith("acp_") and bool(getattr(live.client, "running", False)))
        ):
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
        # Authentication is intentionally verified by an explicit live task,
        # not by reading or exposing cached credentials in a health probe.
        return {
            "ok": True,
            "runtime": "codebuddy",
            "capabilities": self.capabilities().to_dict(),
        }


    @staticmethod
    def _usage_fact(
        request: BackendStartRequest | BackendResumeRequest,
        raw: dict[str, Any],
        total_cost_usd: float | None,
        *,
        source: str = "codebuddy_sdk_result",
        accounting: str = "snapshot",
        sample_id: str = "",
    ) -> BackendUsage | None:
        # CodeBuddy SDK result usage is a per-query/turn fact.  Some ACP
        # versions wrap the same fields in ``usage``; accept both without
        # retaining the nested/raw payload.
        nested = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
        meta_usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
        values = {**raw, **nested, **meta_usage}
        if not values and total_cost_usd is None:
            return None

        def number(*names: str) -> float | int | None:
            for name in names:
                value = values.get(name)
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)) and value >= 0:
                    return value
            return None

        raw_input = number("input_tokens", "inputTokens", "prompt_tokens")
        cache_read = number(
            "cache_read_tokens", "cacheReadTokens", "cache_read_input_tokens",
            "cached_input_tokens", "cachedInputTokens", "prompt_cache_hit_tokens",
        )
        cache_write = number(
            "cache_write_tokens", "cacheWriteTokens", "cache_write_input_tokens",
            "cache_creation_input_tokens", "cacheCreationInputTokens", "prompt_cache_write_tokens",
        )
        explicit_cache_miss = number("cache_miss_tokens", "cacheMissTokens", "prompt_cache_miss_tokens")
        output_tokens = number("output_tokens", "outputTokens", "completion_tokens")
        reasoning_tokens = number("reasoning_tokens", "reasoningTokens", "thinking_tokens", "thinkingTokens")
        answer_tokens = number("answer_tokens", "answerTokens", "response_tokens", "responseTokens")
        credits = number("credit", "credits", "credits_used", "creditsUsed", "credit_used")
        explicit_total = number("total_tokens", "totalTokens")

        derived: list[str] = []
        # CodeBuddy cost semantics expose ``input/cacheRead/cacheWrite`` as
        # mutually exclusive input categories.  Only derive a total input when
        # both cache categories were actually reported; missing never means 0.
        input_tokens = raw_input
        cache_miss = explicit_cache_miss
        if source != "codebuddy_acp_usage_update":
            if cache_miss is None and raw_input is not None and (cache_read is not None or cache_write is not None):
                cache_miss = raw_input
                derived.append("cache_miss_tokens")
            if raw_input is not None and cache_read is not None and cache_write is not None:
                input_tokens = raw_input + cache_read + cache_write
                derived.append("input_tokens")
        total_tokens = explicit_total
        if (
            source != "codebuddy_acp_usage_update"
            and total_tokens is None
            and input_tokens is not None
            and output_tokens is not None
        ):
            total_tokens = input_tokens + output_tokens
            derived.append("total_tokens")

        return BackendUsage(
            provider="codebuddy",
            scope="turn",
            model=request.model,
            source=source,
            accounting=accounting,
            sample_id=sample_id,
            total_tokens=int(total_tokens) if total_tokens is not None else None,
            input_tokens=int(input_tokens) if input_tokens is not None else None,
            cache_read_tokens=int(cache_read) if cache_read is not None else None,
            cache_miss_tokens=int(cache_miss) if cache_miss is not None else None,
            cache_write_tokens=int(cache_write) if cache_write is not None else None,
            output_tokens=int(output_tokens) if output_tokens is not None else None,
            reasoning_tokens=int(reasoning_tokens) if reasoning_tokens is not None else None,
            answer_tokens=int(answer_tokens) if answer_tokens is not None else None,
            credits=float(credits) if credits is not None else None,
            derived_fields=tuple(derived),
            # Kept only for legacy non-panel callers.  The panel projection
            # intentionally ignores monetary billing values.
            reported_cost=(
                float(total_cost_usd)
                if isinstance(total_cost_usd, (int, float)) and not isinstance(total_cost_usd, bool) and total_cost_usd >= 0
                else None
            ),
            currency="USD" if total_cost_usd is not None else None,
            provider_usage=values,
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

    def _run_acp(
        self,
        request: BackendStartRequest | BackendResumeRequest,
        callbacks: BackendCallbacks,
        *,
        resume_session_id: str,
    ) -> BackendResult:
        route = str(request.metadata.get("route") or "acp_read_only").strip().lower()
        mode = "patch" if route == "acp_patch" else ("verification" if route == "acp_verify" else "read_only")
        plan = request.metadata.get("patch_policy")
        plan = plan if isinstance(plan, dict) else {}
        raw_specs = plan.get("commands", plan.get("command_specs"))
        command_specs: list[CommandSpec] = []
        if isinstance(raw_specs, list):
            for item in raw_specs:
                try:
                    command_specs.append(CommandSpec.from_dict(item))
                except ValueError as exc:
                    raise BackendProtocolError("CodeBuddy patch policy contains an invalid command spec") from exc
        workspace_snapshot: tempfile.TemporaryDirectory[str] | None = None
        client_cwd = request.cwd
        allowed_paths = tuple(str(item) for item in plan.get("allowed_paths", []) if isinstance(item, str))
        forbidden_paths = tuple(str(item) for item in plan.get("forbidden_paths", []) if isinstance(item, str))
        native_read_tools = False
        if mode == "read_only":
            routing = request.metadata.get("routing_metadata")
            routing = routing if isinstance(routing, dict) else {}
            native_read_tools = routing.get("context_delivery") in {"vendor_workspace", "vendor_workspace_scoped"}
            if native_read_tools:
                read_plan = request.metadata.get("verification_plan")
                read_plan = read_plan if isinstance(read_plan, dict) else {}
                allowed_paths = tuple(str(item) for item in read_plan.get("allowed_paths", []) if isinstance(item, str))
                workspace_snapshot, snapshot_root = materialize_workspace_snapshot(
                    request.cwd,
                    allowed_paths=(allowed_paths or None) if routing.get("context_delivery") == "vendor_workspace_scoped" else None,
                )
                client_cwd = str(snapshot_root)
            else:
                # Frozen-context ACP must not regain workspace reads merely
                # because the ACP client advertises filesystem callbacks.
                # Run it in an empty disposable cwd; the prompt already holds
                # the exact Runtime-rendered context.
                workspace_snapshot = tempfile.TemporaryDirectory(prefix="tp-voyager-codebuddy-frozen-")
                client_cwd = workspace_snapshot.name
            forbidden_paths = tuple(_MANDATORY_FORBIDDEN)
        try:
            client = self._acp_client_factory(
                cwd=client_cwd,
                on_activity=callbacks.on_activity,
                access_mode=mode,
                native_read_tools=native_read_tools,
                allowed_paths=allowed_paths,
                forbidden_paths=forbidden_paths,
                command_specs=tuple(command_specs),
            )
        except Exception:
            if workspace_snapshot is not None:
                workspace_snapshot.cleanup()
            raise
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
                    client.cancel(session_id)

            result = client.run(
                prompt=request.prompt,
                resume_session_id=resume_session_id,
                model=request.model,
                reasoning_effort=request.reasoning_effort,
                idle_timeout_seconds=request.idle_timeout_seconds,
                max_task_duration_seconds=request.max_task_duration_seconds,
                on_dispatch_accepted=accepted,
            )
            usage_sink = getattr(callbacks, "on_usage", None)
            samples = tuple(getattr(result, "usage_samples", ()) or ())
            if callable(usage_sink):
                if samples:
                    for sample in samples:
                        if not isinstance(sample, dict):
                            continue
                        raw_sample = sample.get("usage") if isinstance(sample.get("usage"), dict) else {}
                        fact = self._usage_fact(
                            request, raw_sample, None,
                            source="codebuddy_acp_usage_update",
                            accounting=str(sample.get("accounting") or "snapshot"),
                            sample_id=str(sample.get("sample_id") or ""),
                        )
                        if fact is not None:
                            usage_sink(fact)
                else:
                    fallback = self._usage_fact(
                        request, dict(getattr(result, "usage", {}) or {}), None,
                        source="codebuddy_acp_usage_update", accounting="snapshot",
                    )
                    if fallback is not None:
                        usage_sink(fallback)
            usage_fact = self._usage_fact(
                request, dict(getattr(result, "usage", {}) or {}), None,
                source="codebuddy_acp_usage_update", accounting="snapshot",
            )
            backend_result = BackendResult(
                backend="codebuddy",
                stop_reason=result.stop_reason,
                answer=result.answer,
                result={
                    "answer": result.answer,
                    "backend": "codebuddy",
                    "stopReason": result.stop_reason,
                    "model_applied": getattr(result, "model_applied", bool(request.model) if request.model else None),
                    "reasoning_effort_applied": getattr(result, "reasoning_effort_applied", bool(request.reasoning_effort) if request.reasoning_effort else None),
                    "usage": usage_fact.to_dict() if usage_fact is not None else {},
                },
                observability={**dict(getattr(result, "observability", {}) or {})},
                backend_session_id=result.session_id,
            )
            callbacks.on_result(backend_result)
            return backend_result
        finally:
            client.close()
            self._unregister(request.task_id)
            if workspace_snapshot is not None:
                workspace_snapshot.cleanup()

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
        workspace_snapshot: tempfile.TemporaryDirectory[str] | None = None
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
            routing = request.metadata.get("routing_metadata")
            routing = routing if isinstance(routing, dict) else {}
            native_read_tools = routing.get("context_delivery") in {
                "vendor_workspace",
                "vendor_workspace_scoped",
            }
            read_plan = request.metadata.get("verification_plan")
            read_plan = read_plan if isinstance(read_plan, dict) else {}
            allowed_paths = tuple(
                str(item)
                for item in read_plan.get("allowed_paths", [])
                if isinstance(item, str)
            )
            client_cwd = request.cwd
            if native_read_tools:
                # Broad read-only research with native tools runs against a
                # sensitive-path-free snapshot.  Glob/Grep are only authorized
                # by their search root, so the only reliable way to keep
                # .env/*.pem/.git out of vendor output is physical exclusion.
                workspace_snapshot, snapshot_root = materialize_workspace_snapshot(
                    request.cwd,
                    allowed_paths=(allowed_paths or None)
                    if routing.get("context_delivery") == "vendor_workspace_scoped"
                    else None,
                )
                client_cwd = str(snapshot_root)
            try:
                client = self._sdk_client_factory(
                    cwd=client_cwd,
                    on_activity=callbacks.on_activity,
                    access_mode="read_only",
                    allowed_paths=allowed_paths,
                    forbidden_paths=_MANDATORY_FORBIDDEN,
                    native_read_tools=native_read_tools,
                )
            except Exception:
                if workspace_snapshot is not None:
                    workspace_snapshot.cleanup()
                    workspace_snapshot = None
                raise
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
                reasoning_effort=request.reasoning_effort,
                idle_timeout_seconds=request.idle_timeout_seconds,
                max_task_duration_seconds=request.max_task_duration_seconds,
                on_dispatch_accepted=accepted,
            )
            usage_sink = getattr(callbacks, "on_usage", None)
            if callable(usage_sink):
                for sample in tuple(getattr(result, "usage_samples", ()) or ()):
                    if not isinstance(sample, dict):
                        continue
                    sample_usage = sample.get("usage") if isinstance(sample.get("usage"), dict) else {}
                    sample_fact = self._usage_fact(
                        request,
                        sample_usage,
                        None,
                        source="codebuddy_acp_usage_update",
                        accounting=str(sample.get("accounting") or "snapshot"),
                        sample_id=str(sample.get("sample_id") or ""),
                    )
                    if sample_fact is not None:
                        usage_sink(sample_fact)
                terminal_raw = getattr(result, "terminal_usage", None)
                terminal_raw = terminal_raw if isinstance(terminal_raw, dict) else {}
                terminal_fact = self._usage_fact(
                    request,
                    terminal_raw,
                    result.total_cost_usd,
                    source="codebuddy_sdk_result",
                    accounting="snapshot",
                )
                if terminal_fact is not None:
                    usage_sink(terminal_fact)
            usage_fact = self._usage_fact(
                request,
                dict(result.usage or {}),
                result.total_cost_usd,
                source="codebuddy_sdk_result",
                accounting="snapshot",
            )
            backend_result = BackendResult(
                backend="codebuddy",
                stop_reason=result.stop_reason,
                answer=result.answer,
                result={
                    "answer": result.answer,
                    "backend": "codebuddy",
                    "stopReason": result.stop_reason,
                    "model_applied": bool(request.model) if request.model else None,
                    "reasoning_effort_applied": bool(request.reasoning_effort) if request.reasoning_effort else None,
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
            if workspace_snapshot is not None:
                workspace_snapshot.cleanup()
