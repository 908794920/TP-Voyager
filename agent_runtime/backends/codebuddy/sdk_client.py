"""Official CodeBuddy Python SDK transport for controlled execution.

Normal workspace read-only may expose only Read/Glob/Grep behind TP-Voyager's
path gate. Explicit frozen-context read-only keeps native tools disabled.
Patch and verification retain their existing bounded policies.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import shlex
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from agent_runtime.backends.base import BackendActivity
from agent_runtime.domain.dispatch import CommandSpec, relative_path_matches_any
from agent_runtime.backends.codebuddy.process import resolve_codebuddy_internet_environment
from agent_runtime.backends.errors import (
    BackendCancelledError,
    BackendProtocolError,
    BackendTimeoutError,
    BackendUnavailableError,
)

# Official built-in tool names from CodeBuddy settings documentation.  The
# context-only route denies all native tools; later patch work will use a
# separate, explicitly bounded tool gateway rather than widening this route.

_CODEBUDDY_USAGE_FIELDS = (
    "input_tokens", "inputTokens", "prompt_tokens",
    "output_tokens", "outputTokens", "completion_tokens",
    "cache_read_input_tokens", "cacheReadTokens", "prompt_cache_hit_tokens",
    "cache_creation_input_tokens", "cacheCreationInputTokens",
    "cached_input_tokens", "cachedInputTokens",
    "cache_miss_tokens", "cacheMissTokens", "prompt_cache_miss_tokens",
    "cache_write_input_tokens", "cacheWriteTokens", "prompt_cache_write_tokens",
    "cache_read_tokens", "cache_write_tokens",
    "total_tokens", "totalTokens",
    "reasoning_tokens", "reasoningTokens",
    "answer_tokens", "answerTokens",
    "credit", "credits", "credits_used", "creditsUsed", "total_credits", "totalCredits",
)

def _normalize_codebuddy_usage(value: object) -> dict[str, Any]:
    """Serialize only bounded, documented/observed numeric Usage fields."""
    if value is None:
        return {}
    source = value if isinstance(value, dict) else None
    out: dict[str, Any] = {}
    for name in _CODEBUDDY_USAGE_FIELDS:
        raw = source.get(name) if source is not None else getattr(value, name, None)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)) and raw >= 0:
            out[name] = raw
    return out

def _codebuddy_stream_usage_update(event: object) -> dict[str, Any]:
    """Extract a documented ACP ``usage_update`` from an SDK StreamEvent.

    The CodeBuddy Python SDK exposes ``StreamEvent.event`` as an opaque dict.
    When the underlying CLI surfaces the ACP update there, retain only the
    bounded numeric Token/Credit fields understood by TP-Voyager.
    """
    if not isinstance(event, dict):
        return {}
    candidate = event
    params = event.get("params")
    if isinstance(params, dict) and isinstance(params.get("update"), dict):
        candidate = params["update"]
    elif isinstance(event.get("update"), dict):
        candidate = event["update"]
    kind = str(candidate.get("sessionUpdate") or candidate.get("type") or "").strip().lower()
    if kind != "usage_update":
        return {}
    nested = candidate.get("usage") if isinstance(candidate.get("usage"), dict) else {}
    meta = candidate.get("_meta") if isinstance(candidate.get("_meta"), dict) else {}
    meta_usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    # Native ACP v2.99+ publishes Credit in ``update._meta.usage``.  Keep the
    # older top-level/nested shapes as explicit SDK compatibility only.
    return {
        **_normalize_codebuddy_usage(candidate),
        **_normalize_codebuddy_usage(nested),
        **_normalize_codebuddy_usage(meta_usage),
    }

_CODEBUDDY_USAGE_ID_KEYS = (
    # Provider/business correlation IDs only.  In particular, the SDK
    # StreamEvent/transport UUID is intentionally excluded: CodeBuddy can
    # replay one logical ``usage_update`` under a new transport UUID.
    "requestId", "request_id", "turnId", "turn_id",
    "modelRequestId", "model_request_id",
    "messageRequestId", "message_request_id",
    "promptRequestId", "prompt_request_id",
    "messageId", "message_id",
    "codebuddy.ai/requestId", "codebuddy.ai/messageRequestId",
)


def _codebuddy_stream_usage_identity(event: object) -> str:
    """Return only a bounded provider business identity for one usage sample.

    CodeBuddy documents request/message correlation IDs in ACP ``_meta``.
    SDK/transport UUIDs are not request identities and must never turn an
    otherwise anonymous usage snapshot into an additive delta.
    """
    if not isinstance(event, dict):
        return ""
    candidate = event
    params = event.get("params")
    if isinstance(params, dict) and isinstance(params.get("update"), dict):
        candidate = params["update"]
    elif isinstance(event.get("update"), dict):
        candidate = event["update"]
    nested = candidate.get("usage") if isinstance(candidate.get("usage"), dict) else {}
    sources: list[dict[str, Any]] = [candidate, nested]
    for owner in (candidate, nested, params if isinstance(params, dict) else {}, event):
        meta = owner.get("_meta") if isinstance(owner, dict) else None
        if isinstance(meta, dict):
            sources.append(meta)
    for source in sources:
        for key in _CODEBUDDY_USAGE_ID_KEYS:
            value = source.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                text = str(value).strip()
                if text:
                    return text[:160]
    return ""


def _merge_usage_sample(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge a late/enriched observation for the same logical provider sample."""
    previous_usage = existing.get("usage") if isinstance(existing.get("usage"), dict) else {}
    next_usage = incoming.get("usage") if isinstance(incoming.get("usage"), dict) else {}
    return {
        **existing,
        **incoming,
        "usage": {**previous_usage, **next_usage},
    }


def _merge_codebuddy_usage_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compatibility summary without re-adding anonymous snapshots."""
    delta_totals: dict[str, int | float] = {}
    latest_snapshot: dict[str, Any] = {}
    for sample in samples:
        usage = sample.get("usage") if isinstance(sample.get("usage"), dict) else {}
        if sample.get("accounting") == "delta":
            for key, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    delta_totals[key] = delta_totals.get(key, 0) + value
        else:
            latest_snapshot = dict(usage)
    merged = dict(latest_snapshot)
    merged.update(delta_totals)
    return merged


_CODEBUDDY_BUILTIN_TOOLS = (
    "AskUserQuestion",
    "Bash",
    "TaskOutput",
    "Edit",
    "MultiEdit",
    "ExitPlanMode",
    "Glob",
    "Grep",
    "TaskStop",
    "LSP",
    "NotebookEdit",
    "Read",
    "Skill",
    "SlashCommand",
    "Task",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
    "WebFetch",
    "WebSearch",
    "Write",
)


@dataclass(frozen=True)
class CodeBuddySdkRunResult:
    session_id: str
    stop_reason: str
    answer: str
    observability: dict[str, Any]
    usage: dict[str, Any]
    total_cost_usd: float | None = None
    usage_samples: tuple[dict[str, Any], ...] = ()
    terminal_usage: dict[str, Any] | None = None


def load_codebuddy_sdk() -> ModuleType:
    try:
        return importlib.import_module("codebuddy_agent_sdk")
    except ImportError as exc:
        raise BackendUnavailableError("CodeBuddy Agent SDK is not installed") from exc


class CodeBuddySdkClient:
    """Synchronous Runtime wrapper over the official async CodeBuddy SDK."""

    def __init__(
        self,
        *,
        cwd: str,
        on_activity: Callable[[BackendActivity], None],
        cli_path: str | None = None,
        sdk_module: ModuleType | None = None,
        region: str = "cn",
        access_mode: str = "read_only",
        allowed_paths: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        native_read_tools: bool = True,
        command_specs: tuple[CommandSpec, ...] = (),
    ) -> None:
        self.cwd = Path(cwd).resolve()
        self.on_activity = on_activity
        self.cli_path = cli_path
        self.sdk_module = sdk_module
        self.region = str(region or "cn").strip().lower()
        self.access_mode = str(access_mode or "read_only").strip().lower()
        self.allowed_paths = tuple(str(item).replace("\\", "/").strip("/") for item in allowed_paths if str(item).strip())
        self.forbidden_paths = tuple(str(item).replace("\\", "/").strip("/") for item in forbidden_paths if str(item).strip())
        self.native_read_tools = bool(native_read_tools)
        self.command_specs = tuple(command_specs)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any | None = None
        self._cancel_requested = threading.Event()
        self._running = threading.Event()

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def run(
        self,
        *,
        prompt: str,
        resume_session_id: str = "",
        model: str = "",
        reasoning_effort: str = "",
        idle_timeout_seconds: float,
        max_task_duration_seconds: float,
        on_dispatch_accepted: Callable[[str], None],
    ) -> CodeBuddySdkRunResult:
        if not str(prompt or "").strip():
            raise BackendProtocolError("CodeBuddy prompt must not be empty")
        if idle_timeout_seconds <= 0 or max_task_duration_seconds <= 0:
            raise BackendProtocolError("CodeBuddy SDK timeouts must be positive")
        if idle_timeout_seconds >= max_task_duration_seconds:
            raise BackendProtocolError(
                "CodeBuddy SDK idle timeout must be less than max duration"
            )
        try:
            return asyncio.run(
                self._run_async(
                    prompt=prompt,
                    resume_session_id=resume_session_id,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    idle_timeout_seconds=float(idle_timeout_seconds),
                    max_task_duration_seconds=float(max_task_duration_seconds),
                    on_dispatch_accepted=on_dispatch_accepted,
                )
            )
        except BackendProtocolError:
            raise
        except BackendTimeoutError:
            raise
        except BackendCancelledError:
            raise
        except Exception as exc:
            if self._cancel_requested.is_set():
                raise BackendCancelledError("CodeBuddy SDK execution cancelled") from exc
            raise BackendProtocolError(
                f"CodeBuddy SDK failed: {type(exc).__name__}"
            ) from exc

    def cancel(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            loop = self._loop
            client = self._client
        if loop is None or client is None or loop.is_closed():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._cancel_async(client), loop)
            future.result(timeout=5.0)
        except Exception:
            # Cancellation remains best-effort at the transport layer.  The
            # Runtime's durable cancel state remains authoritative.
            return

    def close(self) -> None:
        self.cancel()

    async def _run_async(
        self,
        *,
        prompt: str,
        resume_session_id: str,
        model: str,
        reasoning_effort: str,
        idle_timeout_seconds: float,
        max_task_duration_seconds: float,
        on_dispatch_accepted: Callable[[str], None],
    ) -> CodeBuddySdkRunResult:
        sdk = self.sdk_module or load_codebuddy_sdk()
        # Session identity must be fixed *before* dispatch so the Runtime can
        # durably persist the same resumable id at the dispatch gate.  The
        # official SDK exposes ``CodeBuddyAgentOptions.session_id`` for this;
        # the ``query(session_id=...)`` parameter is ignored for string prompts.
        dispatch_id = resume_session_id.strip() or str(uuid.uuid4())
        options = self._build_options(
            sdk,
            resume_session_id=resume_session_id,
            model=model,
            reasoning_effort=reasoning_effort,
            session_id=dispatch_id,
        )
        client = sdk.CodeBuddySDKClient(options=options)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._loop = loop
            self._client = client
        self._running.set()
        started = time.monotonic()
        text_parts: list[str] = []
        event_count = 0
        result_message: Any | None = None
        usage_samples: list[dict[str, Any]] = []
        usage_sample_indexes: dict[str, int] = {}
        anonymous_usage_signatures: set[tuple[tuple[str, int | float], ...]] = set()
        try:
            await client.connect()
            if self._cancel_requested.is_set():
                await self._cancel_async(client)
                raise BackendCancelledError("CodeBuddy SDK cancelled before dispatch")

            on_dispatch_accepted(dispatch_id)
            await client.query(prompt, session_id=dispatch_id)

            iterator = client.receive_response().__aiter__()
            deadline = started + max_task_duration_seconds
            last_activity = time.monotonic()
            while True:
                if self._cancel_requested.is_set():
                    await self._cancel_async(client)
                    raise BackendCancelledError("CodeBuddy SDK execution cancelled")
                now = time.monotonic()
                remaining_total = deadline - now
                if remaining_total <= 0:
                    await self._cancel_async(client)
                    raise BackendTimeoutError(
                        "CodeBuddy SDK exceeded max duration",
                        timeout_reason="max_task_duration",
                    )
                remaining_idle = idle_timeout_seconds - (now - last_activity)
                if remaining_idle <= 0:
                    await self._cancel_async(client)
                    raise BackendTimeoutError(
                        "CodeBuddy SDK became semantically idle",
                        timeout_reason="idle_timeout",
                    )
                try:
                    message = await asyncio.wait_for(
                        iterator.__anext__(),
                        timeout=min(remaining_total, remaining_idle),
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    now = time.monotonic()
                    reason = (
                        "max_task_duration"
                        if now >= deadline
                        else "idle_timeout"
                    )
                    await self._cancel_async(client)
                    raise BackendTimeoutError(
                        "CodeBuddy SDK response timed out",
                        timeout_reason=reason,
                    ) from exc

                last_activity = time.monotonic()
                event_count += 1
                type_name = type(message).__name__
                observation_events: list[dict[str, Any]] = []
                if type_name == "AssistantMessage":
                    for block in list(getattr(message, "content", None) or []):
                        block_name = type(block).__name__
                        if block_name == "TextBlock":
                            text = getattr(block, "text", None)
                            if isinstance(text, str):
                                text_parts.append(text)
                                observation_events.append({
                                    "observation_kind": "assistant_message",
                                    "text": text,
                                })
                        elif block_name in {"ToolUseBlock", "ToolUse"}:
                            tool_name = str(getattr(block, "name", None) or "tool")[:160]
                            tool_input = getattr(block, "input", None)
                            detail: dict[str, Any] = {
                                "observation_kind": "tool_activity",
                                "tool": tool_name,
                                "status": "requested",
                            }
                            if isinstance(tool_input, dict):
                                raw_path = tool_input.get("file_path", tool_input.get("path"))
                                rel = self._relative_path(raw_path) if raw_path not in {None, ""} else None
                                if rel is not None:
                                    detail["path"] = rel
                                if tool_name in {"Edit", "MultiEdit", "Write"}:
                                    detail["action"] = "modify"
                            observation_events.append(detail)
                if type_name == "StreamEvent":
                    raw_event = getattr(message, "event", None)
                    observed_usage = _codebuddy_stream_usage_update(raw_event)
                    if observed_usage:
                        sample_id = _codebuddy_stream_usage_identity(raw_event)
                        accounting = "delta" if sample_id else "snapshot"
                        sample = {
                            "usage": dict(observed_usage),
                            "sample_id": sample_id,
                            "accounting": accounting,
                        }
                        if sample_id:
                            index = usage_sample_indexes.get(sample_id)
                            if index is None:
                                usage_sample_indexes[sample_id] = len(usage_samples)
                                usage_samples.append(sample)
                            else:
                                # The same request/turn can be replayed or
                                # enriched. Keep one logical delta and let the
                                # newest payload for that identity win.
                                usage_samples[index] = _merge_usage_sample(usage_samples[index], sample)
                        else:
                            signature = tuple(sorted(
                                (str(key), value)
                                for key, value in observed_usage.items()
                                if isinstance(value, (int, float)) and not isinstance(value, bool)
                            ))
                            if signature not in anonymous_usage_signatures:
                                anonymous_usage_signatures.add(signature)
                                usage_samples.append(sample)
                if type_name == "ResultMessage":
                    result_message = message
                route_name = "sdk_patch" if self.access_mode == "patch" else ("sdk_verify" if self.access_mode == "verification" else "sdk_context_read_only")
                if observation_events:
                    for observation in observation_events:
                        self.on_activity(
                            BackendActivity(
                                kind="stream_activity",
                                timestamp=time.time(),
                                detail={
                                    "route": route_name,
                                    "sdk_message": type_name[:80],
                                    **observation,
                                },
                            )
                        )
                else:
                    self.on_activity(
                        BackendActivity(
                            kind="stream_activity",
                            timestamp=time.time(),
                            detail={
                                "route": route_name,
                                "sdk_message": type_name[:80],
                            },
                        )
                    )
                if result_message is not None:
                    break

            if self._cancel_requested.is_set():
                raise BackendCancelledError("CodeBuddy SDK execution cancelled")
            if result_message is None:
                raise BackendProtocolError("CodeBuddy SDK returned no ResultMessage")
            if bool(getattr(result_message, "is_error", False)):
                raise BackendProtocolError("CodeBuddy SDK returned an error result")

            session_id = str(getattr(result_message, "session_id", "") or dispatch_id)
            if session_id != dispatch_id:
                # Session identity is durable resume truth.  A vendor response
                # that unexpectedly switches it cannot be safely reconciled
                # against the already persisted dispatch gate, so fail closed.
                raise BackendProtocolError("CodeBuddy SDK changed session identity")
            result_text = getattr(result_message, "result", None)
            answer = (
                str(result_text).strip()
                if isinstance(result_text, str) and result_text.strip()
                else "".join(text_parts).strip()
            )
            usage = getattr(result_message, "usage", None)
            # SDK Result usage is the terminal Token source; a documented ACP
            # usage_update surfaced through StreamEvent can additionally carry
            # per-turn Credit.  Never estimate Credit from Token counts.
            terminal_usage = _normalize_codebuddy_usage(usage)
            stream_usage = _merge_codebuddy_usage_samples(usage_samples)
            usage_dict = {**stream_usage, **terminal_usage}
            # CodeBuddy ACP v2.99 documents usage_update.usage.credit as the
            # per-turn Credit value.  Preserve the de-duplicated stream value
            # when present; terminal SDK usage remains authoritative for Token
            # snapshots but cannot silently replace that Credit quantity.
            stream_credit = stream_usage.get("credit")
            if isinstance(stream_credit, (int, float)) and not isinstance(stream_credit, bool):
                usage_dict["credit"] = stream_credit
            total_cost = getattr(result_message, "total_cost_usd", None)
            duration_ms = getattr(result_message, "duration_ms", None)
            turns = getattr(result_message, "num_turns", None)
            return CodeBuddySdkRunResult(
                session_id=session_id,
                stop_reason=str(getattr(result_message, "subtype", "") or "end_turn"),
                answer=answer,
                usage=usage_dict,
                usage_samples=tuple({
                    "usage": dict(sample.get("usage") or {}),
                    "sample_id": str(sample.get("sample_id") or ""),
                    "accounting": str(sample.get("accounting") or "snapshot"),
                } for sample in usage_samples),
                terminal_usage=dict(terminal_usage),
                total_cost_usd=(
                    float(total_cost)
                    if isinstance(total_cost, (int, float))
                    else None
                ),
                observability={
                    "route": "sdk_patch" if self.access_mode == "patch" else ("sdk_verify" if self.access_mode == "verification" else "sdk_context_read_only"),
                    "access_mode": self.access_mode,
                    "native_tools_enabled": (
                        self.access_mode in {"patch", "verification"}
                        or (self.access_mode == "read_only" and self.native_read_tools)
                    ),
                    "command_whitelist_size": len(self.command_specs),
                    "event_count": event_count,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "sdk_duration_ms": duration_ms if isinstance(duration_ms, int) else None,
                    "num_turns": turns if isinstance(turns, int) else None,
                },
            )
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
            self._running.clear()
            with self._lock:
                self._client = None
                self._loop = None

    def _relative_path(self, raw: object) -> str | None:
        value = str(raw or "").strip()
        if not value:
            return None
        path = Path(value)
        candidate = path.resolve() if path.is_absolute() else (self.cwd / path).resolve()
        try:
            rel = candidate.relative_to(self.cwd)
        except ValueError:
            return None
        return rel.as_posix()

    @staticmethod
    def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
        return relative_path_matches_any(path, prefixes)

    def _path_allowed(self, raw: object, *, write: bool) -> bool:
        rel = self._relative_path(raw)
        if rel is None:
            return False
        if self._matches(rel, self.forbidden_paths):
            return False
        if self.allowed_paths and not self._matches(rel, self.allowed_paths):
            return False
        return not write or self.access_mode == "patch"

    def _allowed_command_texts(self) -> set[str]:
        values: set[str] = set()
        for spec in self.command_specs:
            values.add(shlex.join(list(spec.argv)).strip())
        return values

    def _build_options(
        self,
        sdk: ModuleType,
        *,
        resume_session_id: str,
        model: str,
        reasoning_effort: str = "",
        session_id: str = "",
    ) -> Any:
        env: dict[str, str] = {}
        # The target product uses the China CodeBuddy account environment.
        # Preserve an explicit caller override for enterprise/iOA testing, but
        # never silently switch a configured CN environment to public.
        current_env = resolve_codebuddy_internet_environment().strip()
        if current_env:
            env["CODEBUDDY_INTERNET_ENVIRONMENT"] = current_env

        async def authorize_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            options: Any,
        ) -> Any:
            del options
            name = str(tool_name or "")
            data = dict(tool_input or {})
            if self.access_mode == "read_only":
                if not self.native_read_tools:
                    return sdk.PermissionResultDeny(
                        message=(
                            "TP-Voyager frozen-context route denies native tools; "
                            f"requested={name[:80]}"
                        ),
                        interrupt=False,
                    )
                if name == "Read":
                    raw = data.get("file_path", data.get("path"))
                    allowed = self._path_allowed(raw, write=False)
                elif name in {"Glob", "Grep"}:
                    raw = data.get("path")
                    if raw in {None, "", "."}:
                        data["path"] = "."
                        raw = "."
                    allowed = self._path_allowed(raw, write=False)
                else:
                    allowed = False

                if not allowed:
                    return sdk.PermissionResultDeny(
                        message=f"TP-Voyager read-only policy denied tool: {name[:80]}",
                        interrupt=False,
                    )
                return sdk.PermissionResultAllow(updated_input=data)

            # T4 patch route: only bounded filesystem tools and exact
            # Captain-authorized commands are eligible.  Unknown tools fail
            # closed even if a future CodeBuddy version adds them.
            if name in {"Read"}:
                raw = data.get("file_path", data.get("path"))
                allowed = self._path_allowed(raw, write=False)
            elif name in {"Glob", "Grep"}:
                # Require an explicit bounded search root.  A missing path
                # would allow a whole-worktree scan and defeat the Captain's
                # declared context boundary.
                raw = data.get("path")
                allowed = self._path_allowed(raw, write=False)
            elif name in {"Edit", "MultiEdit", "Write"}:
                # Independent verification never grants source-write tools,
                # even though exact test commands may create disposable build
                # outputs through Bash inside the isolated worktree.
                raw = data.get("file_path", data.get("path"))
                allowed = self.access_mode == "patch" and self._path_allowed(raw, write=True)
            elif name == "Bash":
                command = str(data.get("command") or "").strip()
                raw_cwd = data.get("cwd", data.get("working_directory"))
                requested_cwd = "."
                if raw_cwd not in {None, "", "."}:
                    requested_cwd = self._relative_path(raw_cwd) or "__outside__"
                has_env_override = bool(data.get("env") or data.get("environment"))
                background = bool(data.get("run_in_background") or data.get("background"))
                allowed = (
                    not has_env_override
                    and not background
                    and any(
                        shlex.join(list(spec.argv)).strip() == command
                        and spec.cwd == requested_cwd
                        for spec in self.command_specs
                    )
                )
            else:
                allowed = False

            if not allowed:
                return sdk.PermissionResultDeny(
                    message=f"TP-Voyager controlled policy denied tool: {name[:80]}",
                    interrupt=False,
                )
            return sdk.PermissionResultAllow(updated_input=data)

        if self.access_mode == "patch":
            allowed_native = {"Read", "Glob", "Grep", "Edit", "MultiEdit", "Write"}
            if self.command_specs:
                allowed_native.add("Bash")
            permission_mode = "default"
            disallowed = [tool for tool in _CODEBUDDY_BUILTIN_TOOLS if tool not in allowed_native]
        elif self.access_mode == "verification":
            allowed_native = {"Read", "Glob", "Grep"}
            if self.command_specs:
                allowed_native.add("Bash")
            permission_mode = "default"
            disallowed = [tool for tool in _CODEBUDDY_BUILTIN_TOOLS if tool not in allowed_native]
        else:
            allowed_native = {"Read", "Glob", "Grep"} if self.native_read_tools else set()
            permission_mode = "plan"
            disallowed = [tool for tool in _CODEBUDDY_BUILTIN_TOOLS if tool not in allowed_native]

        kwargs: dict[str, Any] = {
            "cwd": str(self.cwd),
            "model": model.strip() or None,
            "effort": reasoning_effort.strip() or None,
            "resume": resume_session_id.strip() or None,
            "session_id": session_id.strip() or None,
            "max_turns": 30,
            "permission_mode": permission_mode,
            "allowed_tools": (sorted(allowed_native) if self.access_mode == "read_only" else []),
            "disallowed_tools": disallowed,
            "mcp_servers": {},
            "setting_sources": [],
            "can_use_tool": authorize_tool,
            "include_partial_messages": True,
            "env": env,
        }
        if self.cli_path:
            kwargs["codebuddy_code_path"] = self.cli_path
        if reasoning_effort.strip():
            # The SDK documents effort alongside a thinking configuration.
            # Use its adaptive mode so the requested effort is carried without
            # inventing a fixed token budget that the Captain did not select.
            adaptive = getattr(sdk, "ThinkingConfigAdaptive", None)
            if callable(adaptive):
                kwargs["thinking"] = adaptive(type="adaptive")
        return sdk.CodeBuddyAgentOptions(**kwargs)

    async def _cancel_async(self, client: Any) -> None:
        interrupt = getattr(client, "interrupt", None)
        if callable(interrupt):
            try:
                await interrupt()
                return
            except Exception:
                pass
        disconnect = getattr(client, "disconnect", None)
        if callable(disconnect):
            try:
                await disconnect()
            except Exception:
                pass
