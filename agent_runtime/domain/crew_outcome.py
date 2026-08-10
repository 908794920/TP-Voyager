"""Machine-decidable Crew work-package outcome (not a Task lifecycle state)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.domain.dispatch import CommandSpec, _safe_relpath

CREW_OUTCOME_SCHEMA = "tp-voyager.crew_outcome/v1"
CREW_OUTCOME_MARKER = "TP_VOYAGER_CREW_OUTCOME_JSON="
_ALLOWED = frozenset({"COMPLETED", "NEEDS_CONTEXT", "NEEDS_AUTHORIZATION", "NEEDS_FIX", "BLOCKED"})


@dataclass(frozen=True)
class CrewOutcome:
    status: str
    summary: str
    requested_files: tuple[str, ...] = ()
    requested_commands: tuple[CommandSpec, ...] = ()
    findings: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: object) -> "CrewOutcome":
        if not isinstance(value, dict) or value.get("schema") != CREW_OUTCOME_SCHEMA:
            raise ValueError("CrewOutcome schema is invalid")
        status = str(value.get("status") or "").strip().upper()
        if status not in _ALLOWED:
            raise ValueError("CrewOutcome status is invalid")
        summary = str(value.get("summary") or "").strip()
        if not summary or len(summary) > 4000:
            raise ValueError("CrewOutcome summary is invalid")
        raw_files = value.get("requested_files") or []
        if not isinstance(raw_files, list) or len(raw_files) > 64:
            raise ValueError("CrewOutcome requested_files is invalid")
        files: list[str] = []
        for item in raw_files:
            path = _safe_relpath(item, field_name="CrewOutcome requested_files")
            if path not in files:
                files.append(path)
        raw_commands = value.get("requested_commands") or []
        if not isinstance(raw_commands, list) or len(raw_commands) > 16:
            raise ValueError("CrewOutcome requested_commands is invalid")
        commands = tuple(CommandSpec.from_dict(item) for item in raw_commands)
        def strings(name: str, limit: int, item_limit: int) -> tuple[str, ...]:
            raw = value.get(name) or []
            if not isinstance(raw, list) or len(raw) > limit:
                raise ValueError(f"CrewOutcome {name} is invalid")
            out: list[str] = []
            for item in raw:
                text = str(item or "").strip()
                if not text or len(text) > item_limit:
                    raise ValueError(f"CrewOutcome {name} contains invalid text")
                if text not in out:
                    out.append(text)
            return tuple(out)
        return cls(
            status=status,
            summary=summary,
            requested_files=tuple(files),
            requested_commands=commands,
            findings=strings("findings", 64, 2000),
            evidence_refs=strings("evidence_refs", 64, 160),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CREW_OUTCOME_SCHEMA,
            "available": True,
            "status": self.status,
            "summary": self.summary,
            "requested_files": list(self.requested_files),
            "requested_commands": [item.to_dict() for item in self.requested_commands],
            "findings": list(self.findings),
            "evidence_refs": list(self.evidence_refs),
        }


def unavailable_outcome(reason: str = "not_returned") -> dict[str, Any]:
    return {"schema": CREW_OUTCOME_SCHEMA, "available": False, "status": None, "reason": reason}


def parse_crew_outcome(answer: str) -> dict[str, Any]:
    """Parse only an explicit marker line.  Never infer status from prose."""
    marker_line = ""
    for line in str(answer or "").splitlines():
        if line.startswith(CREW_OUTCOME_MARKER):
            marker_line = line[len(CREW_OUTCOME_MARKER):].strip()
    if not marker_line:
        return unavailable_outcome()
    try:
        payload = json.loads(marker_line)
        return CrewOutcome.from_dict(payload).to_dict()
    except (json.JSONDecodeError, ValueError, TypeError):
        return unavailable_outcome("invalid")


OUTCOME_PROMPT_CONTRACT = f'''\n\n[TP-Voyager Crew Outcome Contract]\nAt the very end of your response, emit exactly one single-line machine outcome marker:\n{CREW_OUTCOME_MARKER}{{"schema":"{CREW_OUTCOME_SCHEMA}","status":"COMPLETED|NEEDS_CONTEXT|NEEDS_AUTHORIZATION|NEEDS_FIX|BLOCKED","summary":"bounded summary","requested_files":[],"requested_commands":[],"findings":[],"evidence_refs":[]}}\nDo not omit the marker. requested_commands must use objects with id, argv array, and cwd; never shell strings.\n'''
