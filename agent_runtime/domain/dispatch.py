"""Captain-facing TP-Voyager dispatch contracts.

The Captain chooses a Crew and supplies a bounded execution policy.  Vendor
CLI flags never cross this boundary.  Patch policy is deliberately small and
contains only host-enforceable limits; it is not a second task state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any


_MANDATORY_FORBIDDEN = (".git", ".codebuddy", ".qoder")


def _safe_relpath(value: object, *, field_name: str) -> str:
    raw = str(value or "").strip().replace("\\", "/").strip("/")
    pure = PurePosixPath(raw)
    if (
        not raw
        or str(value or "").strip().replace("\\", "/").startswith("/")
        or (len(raw) >= 2 and raw[1] == ":")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"{field_name} contains an unsafe relative path")
    if len(raw) > 512:
        raise ValueError(f"{field_name} path is too long")
    return raw


@dataclass(frozen=True)
class CommandSpec:
    """One Captain-authorized command, represented as argv (never shell text)."""

    command_id: str
    argv: tuple[str, ...]
    cwd: str = "."

    @classmethod
    def from_dict(cls, value: object) -> "CommandSpec":
        data = value if isinstance(value, dict) else {}
        command_id = str(data.get("id") or data.get("command_id") or "").strip()
        raw_argv = data.get("argv")
        if not command_id or len(command_id) > 80:
            raise ValueError("command spec requires a bounded id")
        if not isinstance(raw_argv, list) or not raw_argv or len(raw_argv) > 64:
            raise ValueError(f"command {command_id!r} requires a bounded argv list")
        argv = tuple(str(item) for item in raw_argv)
        if any((not item) or len(item) > 2_000 or "\x00" in item for item in argv):
            raise ValueError(f"command {command_id!r} contains an invalid argv item")
        cwd_raw = str(data.get("cwd") or ".").strip().replace("\\", "/")
        if cwd_raw in {"", "."}:
            cwd = "."
        else:
            cwd = _safe_relpath(cwd_raw, field_name=f"command {command_id} cwd")
        return cls(command_id=command_id, argv=argv, cwd=cwd)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.command_id, "argv": list(self.argv), "cwd": self.cwd}


@dataclass(frozen=True)
class PatchPolicy:
    """Host-enforced patch boundary supplied by the Captain.

    The policy is intentionally per task.  TP-Voyager does not infer hidden
    commands or widen paths on the worker's behalf.
    """

    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...] = _MANDATORY_FORBIDDEN
    commands: tuple[CommandSpec, ...] = ()
    verification_command_ids: tuple[str, ...] = ()
    max_changed_files: int = 8
    max_diff_lines: int = 600
    verification_timeout_seconds: int = 900

    @classmethod
    def from_dict(cls, value: object) -> "PatchPolicy":
        if not isinstance(value, dict):
            raise ValueError("patch_policy must be an object")
        raw_allowed = value.get("allowed_paths")
        if not isinstance(raw_allowed, list) or not raw_allowed:
            raise ValueError("patch_policy.allowed_paths must be a non-empty list")
        allowed: list[str] = []
        for item in raw_allowed:
            path = _safe_relpath(item, field_name="allowed_paths")
            if path not in allowed:
                allowed.append(path)

        forbidden = list(_MANDATORY_FORBIDDEN)
        raw_forbidden = value.get("forbidden_paths")
        if raw_forbidden is not None:
            if not isinstance(raw_forbidden, list):
                raise ValueError("patch_policy.forbidden_paths must be a list")
            for item in raw_forbidden:
                path = _safe_relpath(item, field_name="forbidden_paths")
                if path not in forbidden:
                    forbidden.append(path)

        raw_commands = value.get("commands") or []
        if not isinstance(raw_commands, list) or len(raw_commands) > 16:
            raise ValueError("patch_policy.commands must contain at most 16 command specs")
        commands = tuple(CommandSpec.from_dict(item) for item in raw_commands)
        ids = [item.command_id for item in commands]
        if len(ids) != len(set(ids)):
            raise ValueError("patch_policy command ids must be unique")

        raw_verify = value.get("verification_command_ids") or []
        if not isinstance(raw_verify, list) or len(raw_verify) > 16:
            raise ValueError("verification_command_ids must be a bounded list")
        verify = tuple(str(item).strip() for item in raw_verify if str(item).strip())
        unknown = sorted(set(verify) - set(ids))
        if unknown:
            raise ValueError(
                "verification_command_ids reference undeclared commands: " + ", ".join(unknown)
            )

        max_changed_files = int(value.get("max_changed_files") or 8)
        max_diff_lines = int(value.get("max_diff_lines") or 600)
        verification_timeout_seconds = int(value.get("verification_timeout_seconds") or 900)
        if max_changed_files <= 0 or max_changed_files > 100:
            raise ValueError("max_changed_files must be between 1 and 100")
        if max_diff_lines <= 0 or max_diff_lines > 20_000:
            raise ValueError("max_diff_lines must be between 1 and 20000")
        if verification_timeout_seconds <= 0 or verification_timeout_seconds > 7_200:
            raise ValueError("verification_timeout_seconds must be between 1 and 7200")

        return cls(
            allowed_paths=tuple(allowed),
            forbidden_paths=tuple(forbidden),
            commands=commands,
            verification_command_ids=verify,
            max_changed_files=max_changed_files,
            max_diff_lines=max_diff_lines,
            verification_timeout_seconds=verification_timeout_seconds,
        )

    def verification_commands(self) -> tuple[CommandSpec, ...]:
        wanted = set(self.verification_command_ids)
        return tuple(item for item in self.commands if item.command_id in wanted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "commands": [item.to_dict() for item in self.commands],
            "verification_command_ids": list(self.verification_command_ids),
            "max_changed_files": self.max_changed_files,
            "max_diff_lines": self.max_diff_lines,
            "verification_timeout_seconds": self.verification_timeout_seconds,
        }


@dataclass(frozen=True)
class CaptainDispatchRequest:
    objective: str
    crew: str
    task_kind: str
    cwd: str = ""
    model: str = ""
    access_mode: str = "read_only"
    idempotency_key: str = ""
    context_id: str = ""
    timeout_seconds: int = 300
    required_capabilities: tuple[str, ...] = ()
    patch_policy: PatchPolicy | None = None
