"""Read-only Agent observability projection for the current TP-Voyager runtime.

This module deliberately does not participate in durable Task truth.  SQLite
Task / Session / Event / Evidence rows remain authoritative for control state.
The bounded in-memory stream managed here is best-effort UI/debug telemetry
used only to make Crew execution visible to a human Captain host.  It is
process-local and intentionally disappears when the Runtime restarts.

Observation payloads are allow-listed.  Prompts, system messages, private
reasoning / chain-of-thought, secrets, and raw tool output are never accepted
as fields.  Provider-visible assistant text may be recorded because it is the
same user-facing output the Crew is producing for the delegated task.
"""

from __future__ import annotations

from collections import deque
import re
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from agent_runtime.application.task_service import parse_session_metadata
from agent_runtime.domain.enums import TERMINAL_STATUS_VALUES


OBSERVATION_SCHEMA = "tp-voyager.agent_observation/v1"
PRESENCE_SCHEMA = "tp-voyager.agent_presence/v1"
TRACE_SCHEMA = "tp-voyager.agent_trace/v1"
DETAIL_SCHEMA = "tp-voyager.agent_detail/v1"

_ALLOWED_KINDS = frozenset(
    {
        "agent_started",
        "assistant_message",
        "reasoning_summary",
        "tool_activity",
        "file_change",
        "usage",
        "agent_completed",
        "agent_failed",
        "agent_cancelled",
        "status",
    }
)
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SAFE_TEXT_KEYS = ("text", "reason", "summary")
_SAFE_SHORT_KEYS = (
    "crew",
    "model",
    "status",
    "tool",
    "action",
    "phase",
    "provider",
    "source",
    "currency",
)


def _safe_relative_path(value: Any) -> str | None:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or "\x00" in raw or raw.startswith("/"):
        return None
    # Reject Windows absolute / drive paths and traversal.  The projection is
    # intentionally workspace-relative; host machine paths never belong here.
    if len(raw) >= 2 and raw[1] == ":":
        return None
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    normalized = path.as_posix()
    return normalized[:1024] if normalized else None


def _bounded_string(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    if not text:
        return None
    return text[:limit]


def observation_event_from_backend_activity(activity: Any) -> dict[str, Any] | None:
    """Project an explicit backend observation marker into the safe event shape.

    Backends may keep transport-specific diagnostics in ``detail``.  Only
    fields named here cross into the human-facing observation stream.
    """
    detail = getattr(activity, "detail", None)
    if not isinstance(detail, dict):
        return None
    kind = str(detail.get("observation_kind") or "").strip().lower()
    if kind not in _ALLOWED_KINDS:
        return None
    event: dict[str, Any] = {
        "kind": kind,
        "timestamp": float(getattr(activity, "timestamp", 0.0) or time.time()),
    }
    for key in (*_SAFE_SHORT_KEYS, *_SAFE_TEXT_KEYS, "path", "usage", "input_tokens", "output_tokens", "duration_ms", "turns", "files_changed"):
        if key in detail:
            event[key] = detail[key]
    return event


def _safe_usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed: dict[str, Any] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "credits_used",
        "reported_cost",
        "duration_ms",
        "turns",
    ):
        item = value.get(key)
        if isinstance(item, (int, float)) and item >= 0:
            allowed[key] = item
    currency = _bounded_string(value.get("currency"), 16)
    if currency:
        allowed["currency"] = currency
    return allowed or None


class AgentObservationStore:
    """Bounded in-memory per-task stream for non-authoritative observations.

    Assistant output is intentionally transient.  The optional ``root`` value
    is retained only as a diagnostic namespace for callers/tests; it is never
    created or written.  Durable Task/Event/Evidence rows remain the only
    restart-surviving Runtime truth.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_text_chars: int = 16_384,
        max_events_per_task: int = 1000,
    ) -> None:
        self.root = Path(root) if root is not None else None
        self.max_text_chars = max(32, int(max_text_chars))
        self.max_events_per_task = max(32, min(int(max_events_per_task), 5000))
        self._lock = threading.RLock()
        self._events: dict[str, deque[dict[str, Any]]] = {}

    @staticmethod
    def _validate_task_id(task_id: str) -> str:
        value = str(task_id or "").strip()
        if not _TASK_ID_RE.fullmatch(value):
            raise ValueError("invalid task_id for observation stream")
        return value

    def append(self, task_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """Append one allow-listed transient observation and return it."""
        if not isinstance(event, dict):
            raise ValueError("observation event must be an object")
        canonical_task_id = self._validate_task_id(task_id)
        kind = str(event.get("kind") or "").strip().lower()
        if kind not in _ALLOWED_KINDS:
            raise ValueError(f"unsupported observation kind: {kind or '<empty>'}")

        normalized: dict[str, Any] = {
            "schema": OBSERVATION_SCHEMA,
            "task_id": canonical_task_id,
            "kind": kind,
            "timestamp": float(event.get("timestamp") or time.time()),
        }
        for key in _SAFE_SHORT_KEYS:
            value = _bounded_string(event.get(key), 256)
            if value:
                normalized[key] = value
        for key in _SAFE_TEXT_KEYS:
            value = _bounded_string(event.get(key), self.max_text_chars)
            if value:
                normalized[key] = value
        path = _safe_relative_path(event.get("path"))
        if path:
            normalized["path"] = path

        usage = _safe_usage(event.get("usage"))
        if usage:
            normalized["usage"] = usage

        for key in ("input_tokens", "output_tokens", "duration_ms", "turns", "files_changed"):
            value = event.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                normalized[key] = value

        with self._lock:
            stream = self._events.get(canonical_task_id)
            if stream is None:
                stream = deque(maxlen=self.max_events_per_task)
                self._events[canonical_task_id] = stream
            stream.append(dict(normalized))
        return normalized

    def read(self, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        canonical_task_id = self._validate_task_id(task_id)
        bounded_limit = max(1, min(int(limit), 1000))
        with self._lock:
            stream = self._events.get(canonical_task_id)
            if not stream:
                return []
            return [dict(item) for item in list(stream)[-bounded_limit:]]


class AgentObservationRecorder:
    """Best-effort bridge from live Runtime facts into the UI projection.

    Every method is deliberately side-effect limited to ``AgentObservationStore``
    and never raises into the durable task lifecycle.  The Runtime can therefore
    lose observation telemetry without losing Task truth.
    """

    def __init__(self, store: AgentObservationStore) -> None:
        self.store = store

    @staticmethod
    def _identity(task: Any) -> dict[str, Any]:
        return {
            "crew": str(getattr(task, "runtime", "") or getattr(task, "task_type", "") or "") or None,
            "model": str(getattr(task, "model", "") or "") or None,
        }

    def _append(self, task: Any, event: dict[str, Any]) -> None:
        try:
            self.store.append(str(task.task_id), event)
        except (OSError, ValueError, TypeError):
            return

    def started(self, task: Any, *, timestamp: float | None = None) -> None:
        self._append(
            task,
            {
                "kind": "agent_started",
                "timestamp": timestamp or time.time(),
                "status": "running",
                **self._identity(task),
            },
        )

    def activity(self, task: Any, activity: Any) -> None:
        event = observation_event_from_backend_activity(activity)
        if event is None:
            return
        self._append(task, {**event, **{k: v for k, v in self._identity(task).items() if v}})

    def usage(self, task: Any, usage: Any, *, timestamp: float | None = None) -> None:
        payload = usage.to_dict() if hasattr(usage, "to_dict") else {}
        usage_values = payload.get("usage") if isinstance(payload, dict) else None
        event: dict[str, Any] = {
            "kind": "usage",
            "timestamp": timestamp or time.time(),
            "provider": payload.get("provider") if isinstance(payload, dict) else None,
            "model": payload.get("model") if isinstance(payload, dict) else None,
            "source": payload.get("source") if isinstance(payload, dict) else None,
            "usage": usage_values if isinstance(usage_values, dict) else {},
        }
        self._append(task, event)

    def completed(
        self, task: Any, *, answer: str = "", timestamp: float | None = None
    ) -> None:
        try:
            prior = self.store.read(str(task.task_id), limit=1000)
        except (OSError, ValueError):
            prior = []
        if str(answer or "").strip() and not any(
            item.get("kind") == "assistant_message" for item in prior
        ):
            self._append(
                task,
                {
                    "kind": "assistant_message",
                    "timestamp": timestamp or time.time(),
                    "text": answer,
                    **self._identity(task),
                },
            )
        self._append(
            task,
            {
                "kind": "agent_completed",
                "timestamp": timestamp or time.time(),
                "status": "completed",
                **self._identity(task),
            },
        )

    def failed(
        self,
        task: Any,
        *,
        reason: str = "failed",
        phase: str | None = None,
        timestamp: float | None = None,
    ) -> None:
        self._append(
            task,
            {
                "kind": "agent_failed",
                "timestamp": timestamp or time.time(),
                "status": "failed",
                "reason": reason,
                "phase": phase,
                **self._identity(task),
            },
        )

    def cancelled(self, task: Any, *, timestamp: float | None = None) -> None:
        self._append(
            task,
            {
                "kind": "agent_cancelled",
                "timestamp": timestamp or time.time(),
                "status": "cancelled",
                **self._identity(task),
            },
        )


class VoyageAgentProjection:
    """Human-facing read model over durable Task truth + transient observations."""

    def __init__(self, task_service: Any, observation_store: AgentObservationStore) -> None:
        self._task_service = task_service
        self._observations = observation_store

    def presence(self, task_id: str = "", *, limit: int = 5) -> dict[str, Any]:
        requested = str(task_id or "").strip()
        if requested:
            task = self._task_service.get_task(requested)
            if task is None:
                return self._not_found(requested, PRESENCE_SCHEMA)
            tasks = [task]
        else:
            tasks = list(self._task_service.list_tasks())
            tasks.sort(
                key=lambda item: (
                    str(item.status) in TERMINAL_STATUS_VALUES,
                    -(float(item.updated_at or item.created_at or 0.0)),
                )
            )
            tasks = tasks[: max(1, min(int(limit), 20))]
        return {
            "ok": True,
            "schema": PRESENCE_SCHEMA,
            "scope": "current_runtime",
            "tasks": [self._task_ref(item) for item in tasks],
        }

    def trace(self, task_id: str, *, limit: int = 200) -> dict[str, Any]:
        task = self._task_service.get_task(str(task_id or "").strip())
        if task is None:
            return self._not_found(str(task_id or "").strip(), TRACE_SCHEMA)
        events = self._observations.read(task.task_id, limit=limit)
        return {
            "ok": True,
            "schema": TRACE_SCHEMA,
            "task": self._task_ref(task),
            "timeline": self._timeline(events),
            "conversation": self._conversation(events),
        }

    def detail(self, task_id: str, *, limit: int = 200) -> dict[str, Any]:
        canonical = str(task_id or "").strip()
        task = self._task_service.get_task(canonical)
        if task is None:
            return self._not_found(canonical, DETAIL_SCHEMA)
        events = self._observations.read(task.task_id, limit=limit)
        try:
            usage = self._task_service.latest_usage_evidence(task.task_id)
        except (ValueError, RuntimeError):
            usage = {}
        try:
            artifacts = list(self._task_service.list_artifacts(task.task_id))
        except (ValueError, RuntimeError):
            artifacts = []
        return {
            "ok": True,
            "schema": DETAIL_SCHEMA,
            "scope": "current_runtime",
            "task": self._task_ref(task),
            "conversation": self._conversation(events),
            "timeline": self._timeline(events),
            "files": self._files(events, artifacts),
            "usage": usage if isinstance(usage, dict) else {},
            "error": self._error(task, events),
        }

    def _task_ref(self, task: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        try:
            session = self._task_service.get_session(task.task_id)
        except (AttributeError, RuntimeError):
            session = None
        if session is not None:
            metadata = parse_session_metadata(str(getattr(session, "metadata_json", "{}") or "{}"))

        latest = self._observations.read(task.task_id, limit=50)
        observed_model = next(
            (str(item.get("model") or "") for item in reversed(latest) if item.get("model")),
            "",
        )
        observed_crew = next(
            (str(item.get("crew") or "") for item in reversed(latest) if item.get("crew")),
            "",
        )
        state = str(task.status)
        return {
            "task_id": task.task_id,
            "crew": observed_crew or str(metadata.get("runtime") or getattr(task, "task_type", "") or "") or None,
            "model": observed_model or str(metadata.get("model") or "") or None,
            "route": str(getattr(task, "route", "") or "") or None,
            "state": state,
            "active": state not in TERMINAL_STATUS_VALUES,
            "created_at": getattr(task, "created_at", None),
            "updated_at": getattr(task, "updated_at", None),
            "started_at": getattr(task, "started_at", None),
            "finished_at": getattr(task, "finished_at", None),
            "result_available": bool(getattr(task, "result_available", False)),
        }

    @staticmethod
    def _conversation(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for event in events:
            if event.get("kind") not in {"assistant_message", "reasoning_summary"}:
                continue
            content = str(event.get("text") or event.get("summary") or "").strip()
            if not content:
                continue
            role = "assistant" if event.get("kind") == "assistant_message" else "reasoning_summary"
            # Stream chunks are naturally consecutive; coalesce them to avoid
            # turning a token stream into hundreds of UI rows.
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += content
                messages[-1]["timestamp"] = event.get("timestamp")
            else:
                messages.append(
                    {
                        "role": role,
                        "content": content,
                        "timestamp": event.get("timestamp"),
                    }
                )
        return messages

    @staticmethod
    def _timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for event in events:
            if event.get("kind") in {"assistant_message", "reasoning_summary", "usage"}:
                continue
            row = {
                key: event[key]
                for key in (
                    "kind",
                    "timestamp",
                    "status",
                    "tool",
                    "action",
                    "path",
                    "phase",
                    "reason",
                    "summary",
                )
                if key in event
            }
            output.append(row)
        return output

    @staticmethod
    def _files(events: list[dict[str, Any]], artifacts: list[Any]) -> list[dict[str, Any]]:
        by_path: dict[str, dict[str, Any]] = {}
        for event in events:
            kind = str(event.get("kind") or "")
            action = str(event.get("action") or "").lower()
            is_file_change = kind == "file_change"
            is_mutating_tool = kind == "tool_activity" and action in {
                "modify", "write", "edit", "create", "delete", "rename",
            }
            if not (is_file_change or is_mutating_tool) or not event.get("path"):
                continue
            path = str(event["path"])
            by_path[path] = {
                "path": path,
                "action": event.get("action") or "changed",
                "source": "observation",
                "timestamp": event.get("timestamp"),
            }
        for artifact in artifacts:
            path = _safe_relative_path(getattr(artifact, "workspace_relpath", None))
            if not path:
                continue
            current = by_path.setdefault(path, {"path": path, "source": "artifact"})
            current.update(
                {
                    "artifact_id": getattr(artifact, "artifact_id", None),
                    "kind": getattr(artifact, "kind", None),
                    "capture_state": getattr(artifact, "capture_state", None),
                    "sha256": getattr(artifact, "sha256", None),
                    "size_bytes": getattr(artifact, "size_bytes", None),
                }
            )
        return list(by_path.values())

    @staticmethod
    def _error(task: Any, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        if str(getattr(task, "status", "")) not in {"failed", "lost", "orphaned"}:
            return None
        observed_reason = next(
            (
                _bounded_string(item.get("reason"), 256)
                for item in reversed(events)
                if item.get("kind") == "agent_failed" and item.get("reason")
            ),
            None,
        )
        code = _bounded_string(getattr(task, "error_code", None), 160)
        terminal_reason = _bounded_string(getattr(task, "terminal_reason", None), 160)
        observed_stage = next(
            (
                _bounded_string(item.get("phase"), 160)
                for item in reversed(events)
                if item.get("kind") == "agent_failed" and item.get("phase")
            ),
            None,
        )
        return {
            "code": code,
            # Durable backend error text may contain local paths or provider
            # details. The panel uses only an allow-listed observation category
            # or bounded control-plane category, never the raw exception text.
            "message": observed_reason or terminal_reason or code or "runtime_error",
            "terminal_reason": terminal_reason,
            "stage": observed_stage,
        }

    @staticmethod
    def _not_found(task_id: str, schema: str) -> dict[str, Any]:
        return {
            "ok": False,
            "schema": schema,
            "reason_code": "TASK_NOT_FOUND",
            "task_id": task_id or None,
        }
