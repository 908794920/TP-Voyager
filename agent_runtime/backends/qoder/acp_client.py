"""Minimal ACP v1 stdio client used by the Qoder backend.

The client implements the protocol surface required by coding agents:
initialize, session new/load, prompt streaming, filesystem callbacks,
terminal callbacks, permission decisions, cancellation and bounded cleanup.
It owns transport only; durable task state remains in the runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_runtime.backends.base import BackendActivity
from agent_runtime.domain.dispatch import CommandSpec, relative_path_matches_any
from agent_runtime.backends.errors import (
    BackendCancelledError,
    BackendProtocolError,
    BackendTimeoutError,
)
from agent_runtime.backends.qoder.process import (
    popen_command,
    resolve_qoder_cli,
    terminate_process_tree,
)

_MAX_LINE_BYTES = 8 * 1024 * 1024
_MAX_TERMINAL_OUTPUT_BYTES = 1024 * 1024

_ACP_KIND_ACTIONS = {
    "read": "read",
    "edit": "modify",
    "delete": "delete",
    "move": "modify",
    "search": "search",
    "execute": "execute",
    "fetch": "fetch",
}
_ACP_KIND_TOOLS = {
    "read": "Read",
    "edit": "Edit",
    "delete": "Delete",
    "move": "Move",
    "search": "Search",
    "execute": "Shell",
    "fetch": "Fetch",
}
_READ_TOOL_NAMES = {"read", "openfile", "open_file", "view"}
_SEARCH_TOOL_NAMES = {"grep", "search", "glob", "find", "codesearch"}
_WRITE_TOOL_NAMES = {"write", "edit", "applypatch", "apply_patch", "createfile"}
_SHELL_TOOL_NAMES = {"bash", "shell", "terminal", "exec", "runcommand"}
_QODER_USAGE_ID_KEYS = (
    # Qoder SDK exposes request-level usage on Assistant messages and documents
    # ``request_id`` (including the existing ``message.usage.request_id``).
    # Keep only provider/business IDs here; JSON-RPC/transport IDs are excluded.
    "requestId", "request_id", "turnId", "turn_id",
    "modelRequestId", "model_request_id", "messageId", "message_id",
)
_QODER_USAGE_SCALAR_KEYS = frozenset({
    "inputTokens", "input_tokens", "prompt_tokens",
    "outputTokens", "output_tokens", "completion_tokens",
    "total_tokens", "totalTokens",
    "cached_tokens", "cachedTokens", "cache_read_tokens", "cacheReadTokens",
    "cached_input_tokens", "cachedInputTokens",
    "cache_miss_tokens", "cacheMissTokens",
    "cache_write_tokens", "cacheWriteTokens",
    "reasoning_tokens", "reasoningTokens", "thinking_tokens", "thinkingTokens",
    "answer_tokens", "answerTokens", "response_tokens", "responseTokens",
    "credits", "credit", "creditsUsed", "credits_used", "credit_used",
    "total_credits", "totalCredits", "original_credits", "originalCredits",
    "billable",
})


def _qoder_usage_identity(update: dict[str, Any], nested_usage: dict[str, Any]) -> str:
    """Return a bounded provider request/turn identity, never a transport ID."""
    sources: list[dict[str, Any]] = [nested_usage, update]
    for owner in (nested_usage, update):
        meta = owner.get("_meta") if isinstance(owner, dict) else None
        if isinstance(meta, dict):
            sources.append(meta)
    for source in sources:
        for key in _QODER_USAGE_ID_KEYS:
            value = source.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                text = str(value).strip()
                if text:
                    return text[:160]
    return ""


def _merge_qoder_usage_sample(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge late fields for one request without creating a second delta."""
    previous_usage = existing.get("usage") if isinstance(existing.get("usage"), dict) else {}
    next_usage = incoming.get("usage") if isinstance(incoming.get("usage"), dict) else {}
    return {
        **existing,
        **incoming,
        "usage": {**previous_usage, **next_usage},
    }


@dataclass
class AcpRunResult:
    session_id: str
    stop_reason: str
    answer: str
    observability: dict[str, Any]
    reasoning_effort_applied: bool | None = None
    context_window_tokens_applied: bool | None = None
    model_applied: bool | None = None
    usage: dict[str, Any] | None = None
    usage_samples: tuple[dict[str, Any], ...] = ()


class _Terminal:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self.lock = threading.Lock()
        self.output = bytearray()
        self.truncated = False
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()

    def _drain(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            with self.lock:
                remaining = _MAX_TERMINAL_OUTPUT_BYTES - len(self.output)
                if remaining > 0:
                    self.output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True

    def snapshot(self) -> tuple[str, bool]:
        with self.lock:
            return self.output.decode("utf-8", "replace"), self.truncated


class QoderAcpClient:
    def __init__(
        self,
        *,
        cwd: str,
        on_activity: Callable[[BackendActivity], None],
        cli_path: str | None = None,
        allow_permissions: bool = True,
        read_only: bool = False,
        allow_file_writes: bool | None = None,
        allow_terminal: bool | None = None,
        context_window_tokens: int | None = None,
        allowed_paths: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        visible_tools: tuple[str, ...] = (),
        allowed_tools: tuple[str, ...] = (),
        command_specs: tuple[CommandSpec, ...] = (),
    ) -> None:
        self.cwd = Path(cwd).resolve()
        self.on_activity = on_activity
        self.read_only = bool(read_only)
        self.allow_file_writes = (not self.read_only) if allow_file_writes is None else bool(allow_file_writes)
        self.allow_terminal = (not self.read_only) if allow_terminal is None else bool(allow_terminal)
        self.allow_permissions = bool(allow_permissions) and (self.allow_terminal or self.allow_file_writes)
        self.context_window_tokens = context_window_tokens
        self.allowed_paths = tuple(str(item).replace("\\", "/").strip("/") for item in allowed_paths if str(item).strip())
        self.forbidden_paths = tuple(str(item).replace("\\", "/").strip("/") for item in forbidden_paths if str(item).strip())
        self.visible_tools = tuple(str(item).strip() for item in visible_tools if str(item).strip())
        self.allowed_tools = tuple(str(item).strip() for item in allowed_tools if str(item).strip())
        self.command_specs = tuple(command_specs)
        cli = cli_path or resolve_qoder_cli()
        # Preserve the exact TP-Voyager-config-resolved CLI path so auxiliary
        # Qoder adapters use the same runtime identity without hardcoding it.
        self.cli_path = cli
        command = [cli, "--acp"]
        if self.context_window_tokens is not None:
            command.extend(["--context-window", str(self.context_window_tokens)])
        if self.visible_tools:
            command.extend(["--tools", *self.visible_tools])
        if self.allowed_tools:
            command.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        self.process = popen_command(command, cwd=str(self.cwd))
        self._write_lock = threading.Lock()
        self._responses: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._responses_lock = threading.Lock()
        self._next_id = 1
        self._closed = threading.Event()
        self._cancelled = threading.Event()
        self._last_activity = time.monotonic()
        self._answer: list[str] = []
        self._event_count = 0
        self._stderr_bytes = 0
        self._usage: dict[str, Any] = {}
        self._usage_events: list[dict[str, Any]] = []
        self._usage_samples: list[dict[str, Any]] = []
        self._usage_sample_indexes: dict[str, int] = {}
        self._anonymous_usage_signatures: set[str] = set()
        self._file_access_events: list[dict[str, Any]] = []
        self._agent_request_errors: list[dict[str, str]] = []
        self._read_scope_evidence_captured = False
        self._terminals: dict[str, _Terminal] = {}
        self._reader_error: BaseException | None = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._stderr = threading.Thread(target=self._drain_stderr, daemon=True)
        self._reader.start()
        self._stderr.start()

    # --------------------------------------------------------------- lifecycle

    def run(
        self,
        *,
        prompt: str,
        resume_session_id: str = "",
        model: str = "",
        reasoning_effort: str = "",
        context_window_tokens: int | None = None,
        idle_timeout_seconds: float,
        max_task_duration_seconds: float,
        on_dispatch_accepted: Callable[[str], None],
    ) -> AcpRunResult:
        started = time.monotonic()
        self.capture_read_scope_evidence()
        initialize = self._request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {
                        "readTextFile": True,
                        "writeTextFile": self.allow_file_writes,
                    },
                    "terminal": self.allow_terminal,
                },
                "clientInfo": {
                    "name": "tp-voyager",
                    "title": "TP-Voyager",
                    "version": "1.0",
                },
            },
            timeout=30.0,
        )
        capabilities = initialize.get("agentCapabilities") or {}
        if resume_session_id:
            session_id = resume_session_id
            if bool(capabilities.get("loadSession")):
                session_response = self._request(
                    "session/load",
                    {"sessionId": session_id, "cwd": str(self.cwd), "mcpServers": []},
                    timeout=45.0,
                )
            else:
                raise BackendProtocolError("Qoder ACP does not advertise session/load")
        else:
            session_response = self._request(
                "session/new",
                {"cwd": str(self.cwd), "mcpServers": []},
                timeout=45.0,
            )
            session_id = str(session_response.get("sessionId") or "")
            if not session_id:
                raise BackendProtocolError("Qoder ACP session/new returned no sessionId")

        config_options = session_response.get("configOptions")
        if not isinstance(config_options, list):
            config_options = []
        model_applied, config_options = self._apply_config_option(
            session_id=session_id,
            config_options=config_options,
            category="model",
            requested_value=model,
        )
        if context_window_tokens != self.context_window_tokens:
            raise BackendProtocolError("Qoder ACP context window launch setting mismatch")
        # Qoder exposes context window as a CLI session-start option
        # (``qodercli --context-window <tokens>``), not as an ACP config
        # option.  The value is therefore fixed before the ACP transport
        # begins and is only reported applied after the session was created.
        context_window_applied = (True if context_window_tokens is not None else None)
        reasoning_applied, config_options = self._apply_config_option(
            session_id=session_id,
            config_options=config_options,
            category="thought_level",
            requested_value=reasoning_effort,
        )
        if reasoning_effort and reasoning_applied is not True:
            raise BackendProtocolError("Qoder ACP did not accept the requested thinking effort")

        # Hard dispatch gate: durable session id must be committed before the
        # real prompt request is written to the CLI.
        on_dispatch_accepted(session_id)
        response = self._request_with_activity_timeouts(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": prompt}],
            },
            idle_timeout_seconds=idle_timeout_seconds,
            max_task_duration_seconds=max_task_duration_seconds,
            started=started,
        )
        stop_reason = str(response.get("stopReason") or "end_turn")
        return AcpRunResult(
            session_id=session_id,
            stop_reason=stop_reason,
            answer="".join(self._answer).strip(),
            observability={
                "route": "acp",
                "event_count": self._event_count,
                "stderr_bytes": self._stderr_bytes,
                "duration_seconds": round(time.monotonic() - started, 3),
                "reasoning_effort_applied": reasoning_applied,
                "context_window_tokens_applied": context_window_applied,
                "model_applied": model_applied,
                "usage_provenance": self.usage_provenance(),
                "file_access_events": self.file_access_snapshot(),
            },
            reasoning_effort_applied=reasoning_applied,
            context_window_tokens_applied=context_window_applied,
            model_applied=model_applied,
            usage=dict(self._usage),
            usage_samples=tuple(self.usage_samples()),
        )

    def usage_snapshot(self) -> dict[str, Any]:
        """Return only bounded usage fields actually observed from ACP."""
        return json.loads(json.dumps(self._usage, ensure_ascii=False))

    def usage_samples(self) -> list[dict[str, Any]]:
        """Return distinct bounded request/snapshot usage observations."""
        return json.loads(json.dumps(self._usage_samples, ensure_ascii=False))

    def usage_provenance(self) -> dict[str, Any]:
        known_keys = _QODER_USAGE_SCALAR_KEYS
        has_known_numeric = any(
            key in known_keys
            and isinstance(value, (int, float)) and not isinstance(value, bool)
            and value >= 0
            for key, value in self._usage.items()
        )
        status = "observed" if has_known_numeric else ("protocol_unrecognized" if self._usage_events else "provider_omitted")
        identified = [item for item in self._usage_samples if str(item.get("sample_id") or "").strip()]
        anonymous = [item for item in self._usage_samples if not str(item.get("sample_id") or "").strip()]
        request_identity = (
            "mixed" if identified and anonymous
            else "provider" if identified
            else "unavailable" if anonymous
            else "none"
        )
        return {
            "status": status,
            "request_identity": request_identity,
            "event_count": len(self._usage_events),
            "events": [dict(item) for item in self._usage_events[-16:]],
        }

    def file_access_snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._file_access_events[-256:]]

    def agent_request_diagnostics(self) -> list[dict[str, str]]:
        """Return bounded callback failure categories without paths or content."""
        return [dict(item) for item in self._agent_request_errors[-32:]]

    def capture_read_scope_evidence(self) -> None:
        """Record the bounded files exposed to a read-only ACP session.

        A local ACP agent may read its cwd without invoking the optional client
        filesystem callback.  This snapshot records only the Captain-approved
        access boundary and file digest; it does not claim that the agent opened
        the file and never stores file content.
        """
        if self._read_scope_evidence_captured or not self.read_only:
            return
        self._read_scope_evidence_captured = True
        for raw_path in self.allowed_paths:
            try:
                path = self._safe_path(raw_path, write=False)
                digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
                self._record_file_access(
                    raw_path,
                    operation="read_scope_grant",
                    allowed=True,
                    reason="captain_read_scope",
                    sha256=digest,
                )
            except Exception as exc:
                self._record_file_access(
                    raw_path,
                    operation="read_scope_grant",
                    allowed=False,
                    reason=type(exc).__name__,
                )

    def _record_file_access(self, raw: str, *, operation: str, allowed: bool, reason: str = "", sha256: str = "") -> None:
        try:
            candidate = Path(raw)
            resolved = candidate.resolve() if candidate.is_absolute() else (self.cwd / candidate).resolve()
            try:
                rel = resolved.relative_to(self.cwd).as_posix()
            except ValueError:
                rel = "<outside-workspace>"
        except Exception:
            rel = "<invalid>"
        self._file_access_events.append({
            "path": rel[:512], "operation": operation[:32], "allowed": bool(allowed),
            "reason": str(reason or "")[:160], "timestamp": round(time.time(), 6),
            "sha256": str(sha256 or "")[:64],
        })
        if len(self._file_access_events) > 512:
            del self._file_access_events[:-512]

    def cancel(self, session_id: str = "") -> None:
        self._cancelled.set()
        if session_id and self.process.poll() is None:
            try:
                self._notify("session/cancel", {"sessionId": session_id})
            except OSError:
                pass
        terminate_process_tree(self.process)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        for terminal in list(self._terminals.values()):
            terminate_process_tree(terminal.process)
        terminate_process_tree(self.process)

    def _apply_config_option(
        self,
        *,
        session_id: str,
        config_options: list[Any],
        category: str,
        requested_value: str,
        require_declared: bool = False,
    ) -> tuple[bool | None, list[Any]]:
        requested = requested_value.strip()
        if not requested:
            return None, config_options
        option = self._find_config_option(config_options, category)
        if option is None:
            return False, config_options
        config_id = str(option.get("id") or "")
        if not config_id:
            return False, config_options
        value = self._resolve_declared_config_value(option, requested)
        if value is None:
            if require_declared:
                return False, config_options
            value = requested
        try:
            response = self._request(
                "session/set_config_option",
                {
                    "sessionId": session_id,
                    "configId": config_id,
                    "value": value,
                },
                timeout=10.0,
            )
        except BackendProtocolError:
            return False, config_options
        updated = response.get("configOptions")
        # Some ACP agents omit the refreshed list or return an empty list.
        # Preserve the session/new options so subsequent independent settings
        # (for example thought level after model) can still be applied.
        return True, updated if isinstance(updated, list) and updated else config_options

    @staticmethod
    def _find_config_option(config_options: list[Any], category: str) -> dict[str, Any] | None:
        aliases = {
            "model": ("model", "models"),
            "thought_level": ("thought", "reason", "effort", "thinking"),
            "context_window": ("context", "window"),
        }[category]
        candidates = [item for item in config_options if isinstance(item, dict)]
        for item in candidates:
            if str(item.get("category") or "").strip().lower() == category:
                return item
        for item in candidates:
            haystack = " ".join(
                str(item.get(key) or "").lower() for key in ("id", "name", "description")
            )
            if any(alias in haystack for alias in aliases):
                return item
        return None

    @staticmethod
    def _resolve_config_value(option: dict[str, Any], requested: str) -> str:
        return QoderAcpClient._resolve_declared_config_value(option, requested) or requested

    @staticmethod
    def _resolve_declared_config_value(option: dict[str, Any], requested: str) -> str | None:
        requested_folded = requested.casefold()
        raw_options = option.get("options")
        flattened: list[dict[str, Any]] = []
        if isinstance(raw_options, list):
            for item in raw_options:
                if not isinstance(item, dict):
                    continue
                nested = item.get("options")
                if isinstance(nested, list):
                    flattened.extend(value for value in nested if isinstance(value, dict))
                else:
                    flattened.append(item)
        for item in flattened:
            value = str(item.get("value") or "")
            name = str(item.get("name") or "")
            if requested_folded in {value.casefold(), name.casefold()}:
                return value or requested
        return None

    # --------------------------------------------------------------- JSON-RPC

    def _request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        request_id = self._allocate_id()
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._responses_lock:
            self._responses[request_id] = response_queue
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self._cancelled.is_set():
                    raise BackendCancelledError("Qoder execution cancelled")
                if self._reader_error is not None:
                    raise BackendProtocolError(
                        f"Qoder ACP reader stopped: {type(self._reader_error).__name__}"
                    )
                if self.process.poll() is not None and response_queue.empty():
                    raise BackendProtocolError(
                        f"Qoder ACP exited during request {method} ({self.process.returncode})"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BackendTimeoutError(
                        f"Qoder ACP request timed out: {method}",
                        timeout_reason="transport_timeout",
                    )
                try:
                    response = response_queue.get(timeout=min(0.25, remaining))
                    return self._unwrap_response(response)
                except queue.Empty:
                    continue
        finally:
            with self._responses_lock:
                self._responses.pop(request_id, None)

    def _request_with_activity_timeouts(
        self,
        method: str,
        params: dict[str, Any],
        *,
        idle_timeout_seconds: float,
        max_task_duration_seconds: float,
        started: float,
    ) -> dict[str, Any]:
        request_id = self._allocate_id()
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._responses_lock:
            self._responses[request_id] = response_queue
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            while True:
                if self._cancelled.is_set():
                    raise BackendCancelledError("Qoder execution cancelled")
                if self._reader_error is not None:
                    raise BackendProtocolError(
                        f"Qoder ACP reader stopped: {type(self._reader_error).__name__}"
                    )
                if self.process.poll() is not None and response_queue.empty():
                    raise BackendProtocolError(
                        f"Qoder ACP exited before final response ({self.process.returncode})"
                    )
                now = time.monotonic()
                if now - started >= max_task_duration_seconds:
                    self.cancel(str(params.get("sessionId") or ""))
                    raise BackendTimeoutError(
                        "Qoder execution exceeded max duration",
                        timeout_reason="max_task_duration",
                    )
                if now - self._last_activity >= idle_timeout_seconds:
                    self.cancel(str(params.get("sessionId") or ""))
                    raise BackendTimeoutError(
                        "Qoder execution became semantically idle",
                        timeout_reason="idle_timeout",
                    )
                try:
                    response = response_queue.get(timeout=0.25)
                    return self._unwrap_response(response)
                except queue.Empty:
                    continue
        finally:
            with self._responses_lock:
                self._responses.pop(request_id, None)

    def _unwrap_response(self, response: dict[str, Any]) -> dict[str, Any]:
        error = response.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "ACP request failed")
            diagnostics = self.agent_request_diagnostics()
            if diagnostics:
                last = diagnostics[-1]
                message = (
                    f"{message}; agent_callback={last['method']}:{last['error']}"
                )
            raise BackendProtocolError(message)
        result = response.get("result")
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise BackendProtocolError("ACP result must be an object")
        return result

    def _allocate_id(self) -> int:
        with self._responses_lock:
            value = self._next_id
            self._next_id += 1
            return value

    def _send(self, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if self.process.stdin is None:
            raise BackendProtocolError("Qoder ACP stdin is unavailable")
        with self._write_lock:
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except OSError as exc:
                raise BackendProtocolError("Failed to write Qoder ACP request") from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _read_loop(self) -> None:
        stream = self.process.stdout
        if stream is None:
            self._reader_error = BackendProtocolError("Qoder ACP stdout is unavailable")
            return
        try:
            while not self._closed.is_set():
                line = stream.readline(_MAX_LINE_BYTES + 1)
                if not line:
                    return
                if len(line) > _MAX_LINE_BYTES:
                    raise BackendProtocolError("Qoder ACP message exceeded size limit")
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BackendProtocolError("Qoder ACP emitted invalid JSON") from exc
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    threading.Thread(
                        target=self._handle_agent_request,
                        args=(message,),
                        daemon=True,
                    ).start()
                elif "method" in message:
                    self._handle_notification(message)
                elif "id" in message:
                    try:
                        response_id = int(message["id"])
                    except (TypeError, ValueError):
                        continue
                    with self._responses_lock:
                        target = self._responses.get(response_id)
                    if target is not None:
                        try:
                            target.put_nowait(message)
                        except queue.Full:
                            pass
        except BaseException as exc:
            self._reader_error = exc

    def _handle_notification(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params")
        if method != "session/update" or not isinstance(params, dict):
            return
        update = params.get("update")
        if not isinstance(update, dict):
            return
        kind = str(update.get("sessionUpdate") or update.get("type") or "activity").strip().lower()
        nested_tool = update.get("toolCall")
        tool_like_update = (
            kind in {"tool_call", "tool_call_update"}
            or isinstance(nested_tool, dict)
            or (
                bool(update.get("toolCallId"))
                and any(key in update for key in ("title", "kind", "status", "rawInput"))
            )
        )
        content = update.get("content")
        observation: dict[str, Any] = {}
        if kind == "agent_message_chunk":
            if isinstance(content, dict):
                text = content.get("text")
            else:
                text = content
            if isinstance(text, str):
                self._answer.append(text)
                observation = {
                    "observation_kind": "assistant_message",
                    "text": text,
                }
        elif kind == "usage_update":
            keys = sorted(str(key)[:80] for key in update if key != "sessionUpdate")[:64]
            self._usage_events.append({"type": "usage_update", "keys": keys, "timestamp": round(time.time(), 6), "size_bytes": len(json.dumps(update, ensure_ascii=False).encode("utf-8"))})
            if len(self._usage_events) > 32:
                del self._usage_events[:-32]

            # ACP providers use both top-level scalar usage fields and a
            # nested ``usage`` object. Keep only bounded Token/Credit facts.
            nested_usage = update.get("usage") if isinstance(update.get("usage"), dict) else {}
            sample_values: dict[str, Any] = {}
            for source in (update, nested_usage):
                for key, value in source.items():
                    safe_key = str(key)[:80]
                    if safe_key not in _QODER_USAGE_SCALAR_KEYS:
                        continue
                    if isinstance(value, bool):
                        if safe_key == "billable":
                            sample_values[safe_key] = value
                    elif isinstance(value, (int, float)) and value >= 0:
                        sample_values[safe_key] = value
            for source in (update, nested_usage):
                for key in ("model_usage", "modelUsage"):
                    raw_models = source.get(key)
                    if not isinstance(raw_models, dict):
                        continue
                    safe_models: dict[str, dict[str, int | float | bool]] = {}
                    for model, raw_values in list(raw_models.items())[:32]:
                        if not isinstance(raw_values, dict):
                            continue
                        safe_values: dict[str, int | float | bool] = {}
                        for field, value in list(raw_values.items())[:32]:
                            if isinstance(value, bool) or (
                                isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
                            ):
                                safe_values[str(field)[:80]] = value
                        if safe_values:
                            safe_models[str(model)[:160]] = safe_values
                    if safe_models:
                        sample_values["model_usage"] = safe_models

            if sample_values:
                self._usage.update(sample_values)
                sample_id = _qoder_usage_identity(update, nested_usage)
                accounting = "delta" if sample_id else "snapshot"
                sample = {
                    "usage": json.loads(json.dumps(sample_values, ensure_ascii=False)),
                    "sample_id": sample_id,
                    "accounting": accounting,
                }
                if sample_id:
                    index = self._usage_sample_indexes.get(sample_id)
                    if index is None:
                        self._usage_sample_indexes[sample_id] = len(self._usage_samples)
                        self._usage_samples.append(sample)
                    else:
                        # Replayed/enriched update for the same request replaces
                        # that logical sample instead of creating a new delta.
                        self._usage_samples[index] = _merge_qoder_usage_sample(self._usage_samples[index], sample)
                else:
                    signature = json.dumps(sample_values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    if signature not in self._anonymous_usage_signatures:
                        self._anonymous_usage_signatures.add(signature)
                        self._usage_samples.append(sample)
        elif tool_like_update:
            raw_tool = nested_tool
            if not isinstance(raw_tool, dict):
                raw_tool = update
            tool_kind = str(raw_tool.get("kind") or update.get("kind") or "").strip().lower()
            explicit_tool_name = str(
                raw_tool.get("name")
                or raw_tool.get("toolName")
                or ""
            ).strip()
            if any(marker in explicit_tool_name for marker in ("/", "\\", "\r", "\n")):
                explicit_tool_name = ""
            title = str(raw_tool.get("title") or update.get("title") or "").strip()
            tool_name = explicit_tool_name or _ACP_KIND_TOOLS.get(tool_kind, "")
            if not tool_name and title:
                # ACP titles are human-readable and may contain paths or command
                # arguments. Only retain a leading token when it is a known
                # tool label; otherwise use the generic public label.
                candidate = title.split(maxsplit=1)[0]
                if self._tool_action("", candidate):
                    tool_name = candidate
            tool_name = (tool_name or "tool")[:160]
            status = str(raw_tool.get("status") or update.get("status") or "")[:80]
            raw_input = raw_tool.get("rawInput")
            if not isinstance(raw_input, dict):
                raw_input = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
            action = self._tool_action(tool_kind, tool_name)
            observation = {
                "observation_kind": "tool_activity",
                "tool": tool_name,
            }
            if action:
                observation["action"] = action
            path = self._safe_observation_path(
                raw_input.get("file_path") or raw_input.get("path") or raw_input.get("file")
            )
            if path:
                observation["path"] = path
            if status:
                observation["status"] = status
        detail = {"route": "acp", "acp_update": kind[:80], **observation}
        self._last_activity = time.monotonic()
        self._event_count += 1
        self.on_activity(
            BackendActivity(
                kind="stream_activity",
                timestamp=time.time(),
                detail=detail,
            )
        )

    @staticmethod
    def _tool_action(tool_kind: str, tool_name: str) -> str:
        kind = str(tool_kind or "").strip().lower()
        if kind in _ACP_KIND_ACTIONS:
            return _ACP_KIND_ACTIONS[kind]
        normalized = str(tool_name or "").replace("-", "").replace("_", "").casefold()
        if normalized in {item.replace("_", "") for item in _READ_TOOL_NAMES}:
            return "read"
        if normalized in {item.replace("_", "") for item in _SEARCH_TOOL_NAMES}:
            return "search"
        if normalized in {item.replace("_", "") for item in _WRITE_TOOL_NAMES}:
            return "modify"
        if normalized in {item.replace("_", "") for item in _SHELL_TOOL_NAMES}:
            return "execute"
        return ""

    def _safe_observation_path(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            candidate = Path(raw)
            resolved = candidate.resolve() if candidate.is_absolute() else (self.cwd / candidate).resolve()
            return resolved.relative_to(self.cwd).as_posix()[:512]
        except (OSError, RuntimeError, ValueError):
            return ""

    def _emit_client_tool_activity(
        self,
        *,
        tool: str,
        action: str,
        status: str,
        path: str = "",
    ) -> None:
        detail: dict[str, Any] = {
            "route": "acp_client",
            "observation_kind": "tool_activity",
            "tool": str(tool or "tool")[:160],
            "action": str(action or "")[:80],
            "status": str(status or "")[:80],
            "source": "acp_client_callback",
        }
        safe_path = self._safe_observation_path(path)
        if safe_path:
            detail["path"] = safe_path
        self._last_activity = time.monotonic()
        self.on_activity(
            BackendActivity(
                kind="stream_activity",
                timestamp=time.time(),
                detail=detail,
            )
        )

    def _handle_agent_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        try:
            result = self._dispatch_client_method(method, params)
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            self._agent_request_errors.append({
                "method": method[:120] or "<missing>",
                "error": type(exc).__name__[:80],
            })
            if len(self._agent_request_errors) > 64:
                del self._agent_request_errors[:-64]
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"[:1000]},
            }
        try:
            self._send(response)
        except BackendProtocolError:
            pass

    # ---------------------------------------------------------- client methods

    def _dispatch_client_method(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if method == "fs/read_text_file":
            raw_path = str(params.get("path") or "")
            try:
                path = self._safe_path(raw_path, write=False)
                text = path.read_text(encoding="utf-8")
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                self._record_file_access(
                    raw_path, operation="read", allowed=True,
                    reason="client_fs_callback", sha256=digest,
                )
            except Exception as exc:
                self._record_file_access(raw_path, operation="read", allowed=False, reason=type(exc).__name__)
                raise
            line = max(1, int(params.get("line") or 1))
            limit = max(1, min(100_000, int(params.get("limit") or 100_000)))
            self._emit_client_tool_activity(
                tool="Read", action="read", status="completed", path=raw_path,
            )
            return {"content": "\n".join(text.splitlines()[line - 1: line - 1 + limit])}
        if method == "fs/write_text_file":
            raw_path = str(params.get("path") or "")
            try:
                if not self.allow_file_writes:
                    raise PermissionError("Qoder ACP policy denies file writes")
                path = self._safe_path(raw_path, write=True)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(params.get("content") or ""), encoding="utf-8")
                self._record_file_access(raw_path, operation="write", allowed=True)
            except Exception as exc:
                self._record_file_access(raw_path, operation="write", allowed=False, reason=type(exc).__name__)
                raise
            self._emit_client_tool_activity(
                tool="Write", action="modify", status="completed", path=raw_path,
            )
            return None
        if method == "session/request_permission":
            options = params.get("options") if isinstance(params.get("options"), list) else []
            selected = self._select_permission(options)
            if selected is None:
                return {"outcome": {"outcome": "cancelled"}}
            return {"outcome": {"outcome": "selected", "optionId": selected}}
        if method == "terminal/create":
            if not self.allow_terminal:
                raise PermissionError("Qoder ACP policy denies terminal execution")
            result = self._terminal_create(params)
            self._emit_client_tool_activity(tool="Shell", action="execute", status="in_progress")
            return result
        if method == "terminal/output":
            terminal = self._terminal(str(params.get("terminalId") or ""))
            output, truncated = terminal.snapshot()
            return {"output": output, "truncated": truncated}
        if method == "terminal/wait_for_exit":
            terminal = self._terminal(str(params.get("terminalId") or ""))
            code = terminal.process.wait()
            self._emit_client_tool_activity(
                tool="Shell",
                action="execute",
                status="completed" if int(code or 0) == 0 else "failed",
            )
            return {"exitCode": code}
        if method == "terminal/kill":
            terminal = self._terminal(str(params.get("terminalId") or ""))
            terminate_process_tree(terminal.process)
            self._emit_client_tool_activity(tool="Shell", action="execute", status="cancelled")
            return None
        if method == "terminal/release":
            terminal_id = str(params.get("terminalId") or "")
            terminal = self._terminals.pop(terminal_id, None)
            if terminal is not None:
                terminate_process_tree(terminal.process)
            return None
        raise BackendProtocolError(f"Unsupported ACP client method: {method}")

    @staticmethod
    def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
        return relative_path_matches_any(path, prefixes)

    def _safe_path(self, raw: str, *, write: bool = False) -> Path:
        path = Path(raw)
        candidate = path.resolve() if path.is_absolute() else (self.cwd / path).resolve()
        try:
            rel = candidate.relative_to(self.cwd).as_posix()
        except ValueError as exc:
            raise PermissionError("ACP file access is outside the task workspace") from exc
        if self._matches(rel, self.forbidden_paths):
            raise PermissionError("ACP file access is forbidden by patch policy")
        if self.allowed_paths and not self._matches(rel, self.allowed_paths):
            raise PermissionError("ACP file access is outside the Captain allowed_paths")
        if write and not self.allow_file_writes:
            raise PermissionError("Qoder ACP policy denies file writes")
        return candidate

    def _select_permission(self, options: list[Any]) -> str | None:
        if self.read_only or not self.allow_permissions:
            return None
        # Controlled patch sessions never need a durable vendor-side allow rule:
        # host callbacks enforce every write/terminal request, so prefer the
        # narrowest one-shot approval.  Legacy ACP remains compatible when only
        # allow_always is offered.
        scored: list[tuple[int, str]] = []
        for option in options:
            if not isinstance(option, dict):
                continue
            option_id = str(option.get("optionId") or option.get("id") or "")
            kind = str(option.get("kind") or "")
            if not option_id or kind not in {"allow_once", "allow_always"}:
                # Unknown/reject options never become implicit permission.
                continue
            score = {"allow_once": 0, "allow_always": 1}[kind]
            scored.append((score, option_id))
        return min(scored)[1] if scored else None

    def _terminal_create(self, params: dict[str, Any]) -> dict[str, Any]:
        command = str(params.get("command") or "")
        args = [str(item) for item in params.get("args", [])] if isinstance(params.get("args"), list) else []
        if not command:
            raise ValueError("terminal command is required")
        cwd_raw = str(params.get("cwd") or "")
        cwd = self._safe_path(cwd_raw, write=False) if cwd_raw else self.cwd
        requested_argv = (command, *args)
        requested_rel_cwd = "." if cwd == self.cwd else cwd.relative_to(self.cwd).as_posix()
        allowed = any(
            tuple(spec.argv) == requested_argv and spec.cwd == requested_rel_cwd
            for spec in self.command_specs
        )
        if not allowed:
            raise PermissionError("Qoder terminal command is not in the Captain command whitelist")
        env_items = params.get("env")
        if isinstance(env_items, list) and env_items:
            raise PermissionError("Qoder terminal environment overrides are not authorized")
        env = os.environ.copy()
        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "env": env,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen([command, *args], **kwargs)
        terminal_id = f"term-{uuid.uuid4().hex[:16]}"
        self._terminals[terminal_id] = _Terminal(process)
        return {"terminalId": terminal_id}

    def _terminal(self, terminal_id: str) -> _Terminal:
        terminal = self._terminals.get(terminal_id)
        if terminal is None:
            raise KeyError("unknown terminalId")
        return terminal

    def _drain_stderr(self) -> None:
        stream = self.process.stderr
        if stream is None:
            return
        while not self._closed.is_set():
            chunk = stream.read(8192)
            if not chunk:
                return
            self._stderr_bytes += len(chunk)
