"""Versioned Structured Result envelope and compatibility parser (PR4-B2).

The final Result remains in ``tasks.result_json``.  New runtime finalization
writes ``workbuddy.result/v1`` while readers continue to understand the legacy
unversioned dictionary shape.  Unknown schemas and malformed payloads fail
closed so the public Result API never reports success with empty material.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

RESULT_SCHEMA = "workbuddy.result/v1"


class StructuredResultParseError(ValueError):
    """Persisted Result JSON is malformed or uses an unsupported schema."""


@dataclass(frozen=True)
class StructuredResult:
    """Final task output as a versioned, attempt-bound envelope.

    ``output`` carries ``BackendResult.result`` verbatim; ``observability``
    is the private persisted object.  Prompt text, raw thoughts, credentials,
    environment values and host absolute paths must never be added by the
    runtime finalization mapping.
    """

    schema: str
    attempt_id: str
    answer: str
    backend: str
    stop_reason: str
    output: dict[str, Any] = field(default_factory=dict)
    observability: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    reasoning_effort_requested: str | None = None
    reasoning_effort_applied: bool | None = None
    context_window_tokens_requested: int | None = None
    context_window_tokens_applied: bool | None = None
    changed_files: list[str] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    claims: list[Any] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    crew_outcome: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the frozen wire shape stored in ``tasks.result_json``."""
        if self.schema != RESULT_SCHEMA:
            raise ValueError(f"unsupported structured result schema: {self.schema}")
        return {
            "schema": self.schema,
            "attempt_id": self.attempt_id,
            "answer": self.answer,
            "backend": self.backend,
            "stopReason": self.stop_reason,
            "title": self.title,
            "reasoning_effort_requested": self.reasoning_effort_requested,
            "reasoning_effort_applied": self.reasoning_effort_applied,
            "context_window_tokens_requested": self.context_window_tokens_requested,
            "context_window_tokens_applied": self.context_window_tokens_applied,
            "observability": dict(self.observability),
            "output": dict(self.output),
            "changed_files": list(self.changed_files),
            "tests": [dict(item) for item in self.tests],
            "artifacts": [dict(item) for item in self.artifacts],
            "risks": list(self.risks),
            "claims": list(self.claims),
            "verification": dict(self.verification),
            "usage": dict(self.usage),
            "crew_outcome": dict(self.crew_outcome),
        }


@dataclass(frozen=True)
class ParsedResult:
    """Normalized read view for legacy and ``workbuddy.result/v1`` payloads."""

    schema: str | None
    legacy: bool
    attempt_id: str | None
    answer: str
    backend: str
    stop_reason: str
    title: str
    reasoning_effort_requested: str | None
    reasoning_effort_applied: bool | None
    context_window_tokens_requested: int | None
    context_window_tokens_applied: bool | None
    observability: dict[str, Any]
    output: dict[str, Any]
    changed_files: list[str]
    tests: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    risks: list[str]
    claims: list[Any]
    verification: dict[str, Any]
    usage: dict[str, Any]
    crew_outcome: dict[str, Any]
    raw: dict[str, Any]

    def summary_source(self) -> dict[str, Any]:
        """Return the legacy-like shape consumed by the existing safe summary."""
        if self.legacy:
            return dict(self.raw)
        return {
            "backend": self.backend,
            "stopReason": self.stop_reason,
            "reasoning_effort_requested": self.reasoning_effort_requested,
            "reasoning_effort_applied": self.reasoning_effort_applied,
            "context_window_tokens_requested": self.context_window_tokens_requested,
            "context_window_tokens_applied": self.context_window_tokens_applied,
            "observability": dict(self.observability),
        }


def _dict_field(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


def parse_structured_result(result_json: str) -> ParsedResult:
    """Parse a persisted Result without silently accepting corrupt data.

    Legacy payloads are unversioned dictionaries.  V1 payloads must use the
    exact frozen schema name.  Any other ``schema`` value, malformed JSON, or
    non-object top-level value raises :class:`StructuredResultParseError`.
    """
    try:
        payload = json.loads(result_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StructuredResultParseError("Persisted Result JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise StructuredResultParseError("Persisted Result must be a JSON object")

    if "schema" not in payload:
        return ParsedResult(
            schema=None,
            legacy=True,
            attempt_id=None,
            answer=str(payload.get("answer") or ""),
            backend=str(payload.get("backend") or ""),
            stop_reason=str(payload.get("stopReason") or ""),
            title=str(payload.get("title") or ""),
            reasoning_effort_requested=(
                str(payload["reasoning_effort_requested"])
                if payload.get("reasoning_effort_requested") not in (None, "")
                else None
            ),
            reasoning_effort_applied=(
                bool(payload.get("reasoning_effort_applied"))
                if "reasoning_effort_applied" in payload
                and payload.get("reasoning_effort_applied") is not None
                else None
            ),
            context_window_tokens_requested=(
                int(payload["context_window_tokens_requested"])
                if isinstance(payload.get("context_window_tokens_requested"), int)
                and not isinstance(payload.get("context_window_tokens_requested"), bool)
                else None
            ),
            context_window_tokens_applied=(
                bool(payload.get("context_window_tokens_applied"))
                if "context_window_tokens_applied" in payload
                and payload.get("context_window_tokens_applied") is not None
                else None
            ),
            observability=_dict_field(payload, "observability"),
            output=dict(payload),
            changed_files=[str(item) for item in payload.get("changed_files", []) if isinstance(item, str)] if isinstance(payload.get("changed_files"), list) else [],
            tests=[dict(item) for item in payload.get("tests", []) if isinstance(item, dict)] if isinstance(payload.get("tests"), list) else [],
            artifacts=[dict(item) for item in payload.get("artifacts", []) if isinstance(item, dict)] if isinstance(payload.get("artifacts"), list) else [],
            risks=[str(item) for item in payload.get("risks", []) if isinstance(item, str)] if isinstance(payload.get("risks"), list) else [],
            claims=list(payload.get("claims", [])) if isinstance(payload.get("claims"), list) else [],
            verification=_dict_field(payload, "verification"),
            usage=_dict_field(payload, "usage"),
            crew_outcome=_dict_field(payload, "crew_outcome"),
            raw=dict(payload),
        )

    schema = payload.get("schema")
    if schema != RESULT_SCHEMA:
        raise StructuredResultParseError(
            f"Unsupported persisted Result schema: {schema}"
        )

    attempt_id = payload.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise StructuredResultParseError("Structured Result attempt_id is missing")
    output = payload.get("output")
    observability = payload.get("observability")
    if not isinstance(output, dict) or not isinstance(observability, dict):
        raise StructuredResultParseError(
            "Structured Result output and observability must be objects"
        )

    requested = payload.get("reasoning_effort_requested")
    if requested is not None and not isinstance(requested, str):
        raise StructuredResultParseError(
            "Structured Result reasoning_effort_requested must be a string or null"
        )
    applied = payload.get("reasoning_effort_applied")
    if applied is not None and not isinstance(applied, bool):
        raise StructuredResultParseError(
            "Structured Result reasoning_effort_applied must be a boolean or null"
        )
    context_requested = payload.get("context_window_tokens_requested")
    if context_requested is not None and (isinstance(context_requested, bool) or not isinstance(context_requested, int)):
        raise StructuredResultParseError(
            "Structured Result context_window_tokens_requested must be an integer or null"
        )
    context_applied = payload.get("context_window_tokens_applied")
    if context_applied is not None and not isinstance(context_applied, bool):
        raise StructuredResultParseError(
            "Structured Result context_window_tokens_applied must be a boolean or null"
        )

    list_fields = ("changed_files", "tests", "artifacts", "risks", "claims")
    for key in list_fields:
        value = payload.get(key, [])
        if not isinstance(value, list):
            raise StructuredResultParseError(
                f"Structured Result {key} must be an array"
            )
    for key in ("verification", "usage", "crew_outcome"):
        value = payload.get(key, {})
        if not isinstance(value, dict):
            raise StructuredResultParseError(
                f"Structured Result {key} must be an object"
            )

    return ParsedResult(
        schema=RESULT_SCHEMA,
        legacy=False,
        attempt_id=attempt_id,
        answer=str(payload.get("answer") or ""),
        backend=str(payload.get("backend") or ""),
        stop_reason=str(payload.get("stopReason") or ""),
        title=str(payload.get("title") or ""),
        reasoning_effort_requested=requested,
        reasoning_effort_applied=applied,
        context_window_tokens_requested=context_requested,
        context_window_tokens_applied=context_applied,
        observability=dict(observability),
        output=dict(output),
        changed_files=[str(item) for item in payload.get("changed_files", []) if isinstance(item, str)],
        tests=[dict(item) for item in payload.get("tests", []) if isinstance(item, dict)],
        artifacts=[dict(item) for item in payload.get("artifacts", []) if isinstance(item, dict)],
        risks=[str(item) for item in payload.get("risks", []) if isinstance(item, str)],
        claims=list(payload.get("claims", [])),
        verification=dict(payload.get("verification", {})),
        usage=dict(payload.get("usage", {})),
        crew_outcome=dict(payload.get("crew_outcome", {})),
        raw=dict(payload),
    )
