"""Normalize optional backend result metadata into a runtime-owned shape.

Backends may return arbitrary dictionaries.  The runtime accepts only bounded,
well-typed engineering metadata and never trusts backend-provided storage keys,
hashes, absolute paths, or verification states.
"""

from __future__ import annotations

import hashlib
import os
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

_MAX_ITEMS = 256
_MAX_TEXT = 2_000


@dataclass(frozen=True)
class DeclaredArtifact:
    path: str
    kind: str = "file"
    name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedExecutionMetadata:
    changed_files: list[str] = field(default_factory=list)
    artifacts: list[DeclaredArtifact] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    claims: list[Any] = field(default_factory=list)
    rejected_count: int = 0


def _bounded_text(value: Any) -> str:
    text = str(value or "").strip()
    return text[:_MAX_TEXT]


def _safe_relpath(value: Any) -> str | None:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or raw.startswith("//"):
        return None
    if len(raw) >= 2 and raw[1] == ":":
        return None
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _normalize_path_list(value: Any) -> tuple[list[str], int]:
    if value is None:
        return [], 0
    items = value if isinstance(value, list) else [value]
    output: list[str] = []
    rejected = max(0, len(items) - _MAX_ITEMS)
    for item in items[:_MAX_ITEMS]:
        path = _safe_relpath(item)
        if path is None:
            rejected += 1
        elif path not in output:
            output.append(path)
    return output, rejected


def _normalize_tests(value: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(value, list):
        return [], 0 if value in (None, "") else 1
    output: list[dict[str, Any]] = []
    rejected = max(0, len(value) - _MAX_ITEMS)
    for item in value[:_MAX_ITEMS]:
        if isinstance(item, str):
            output.append({"name": _bounded_text(item), "declared": True})
            continue
        if not isinstance(item, dict):
            rejected += 1
            continue
        record: dict[str, Any] = {}
        for key in ("name", "status", "summary"):
            if key in item:
                record[key] = _bounded_text(item.get(key))
        command = item.get("command")
        if isinstance(command, str) and command.strip():
            record["command_sha256"] = hashlib.sha256(
                command.encode("utf-8")
            ).hexdigest()
            try:
                tokens = shlex.split(command, posix=(os.name != "nt"))
            except ValueError:
                tokens = []
            if tokens and "name" not in record:
                record["name"] = PurePosixPath(tokens[0].replace("\\", "/")).name[:160]
        if isinstance(item.get("exit_code"), int):
            record["exit_code"] = int(item["exit_code"])
        if record:
            record["declared"] = True
            output.append(record)
        else:
            rejected += 1
    return output, rejected


def _normalize_text_list(value: Any) -> tuple[list[str], int]:
    if value is None:
        return [], 0
    items = value if isinstance(value, list) else [value]
    output: list[str] = []
    rejected = max(0, len(items) - _MAX_ITEMS)
    for item in items[:_MAX_ITEMS]:
        if isinstance(item, (str, int, float, bool)):
            text = _bounded_text(item)
            if text:
                output.append(text)
            else:
                rejected += 1
        else:
            rejected += 1
    return output, rejected


def _normalize_claims(value: Any) -> tuple[list[Any], int]:
    if value is None:
        return [], 0
    items = value if isinstance(value, list) else [value]
    output: list[Any] = []
    rejected = max(0, len(items) - _MAX_ITEMS)
    for item in items[:_MAX_ITEMS]:
        if isinstance(item, (str, int, float, bool)):
            text = _bounded_text(item)
            if text:
                output.append(text)
            else:
                rejected += 1
        elif isinstance(item, dict):
            safe = {
                str(key)[:80]: _bounded_text(val)
                for key, val in item.items()
                if isinstance(key, str)
                and isinstance(val, (str, int, float, bool))
            }
            if safe:
                output.append(safe)
            else:
                rejected += 1
        else:
            rejected += 1
    return output, rejected


def _normalize_artifacts(value: Any) -> tuple[list[DeclaredArtifact], int]:
    if value is None:
        return [], 0
    items = value if isinstance(value, list) else [value]
    output: list[DeclaredArtifact] = []
    rejected = max(0, len(items) - _MAX_ITEMS)
    allowed_kinds = {"file", "patch", "report", "build", "log"}
    for item in items[:_MAX_ITEMS]:
        if isinstance(item, str):
            path = _safe_relpath(item)
            if path is None:
                rejected += 1
                continue
            output.append(DeclaredArtifact(path=path, name=PurePosixPath(path).name))
            continue
        if not isinstance(item, dict):
            rejected += 1
            continue
        path = _safe_relpath(
            item.get("path") or item.get("workspace_relpath") or item.get("file")
        )
        if path is None:
            rejected += 1
            continue
        kind = str(item.get("kind") or "file").strip().lower()
        if kind not in allowed_kinds:
            kind = "file"
            rejected += 1
        name = _bounded_text(item.get("name")) or PurePosixPath(path).name
        metadata = {
            str(key)[:80]: value
            for key, value in item.items()
            if key in {"media_type", "description", "role"}
            and isinstance(value, (str, int, float, bool))
        }
        output.append(
            DeclaredArtifact(path=path, kind=kind, name=name, metadata=metadata)
        )
    return output, rejected


def normalize_backend_result(payload: dict[str, Any] | None) -> NormalizedExecutionMetadata:
    """Return a bounded, path-safe metadata view from a backend result."""
    source = payload if isinstance(payload, dict) else {}
    changed, rejected_changed = _normalize_path_list(
        source.get("changed_files") or source.get("changedFiles") or source.get("files")
    )
    artifacts, rejected_artifacts = _normalize_artifacts(source.get("artifacts"))
    tests, rejected_tests = _normalize_tests(source.get("tests"))
    risks, rejected_risks = _normalize_text_list(source.get("risks"))
    claims, rejected_claims = _normalize_claims(source.get("claims"))

    for path in changed:
        if not any(item.path == path for item in artifacts):
            artifacts.append(
                DeclaredArtifact(path=path, kind="file", name=PurePosixPath(path).name)
            )

    return NormalizedExecutionMetadata(
        changed_files=changed,
        artifacts=artifacts,
        tests=tests,
        risks=risks,
        claims=claims,
        rejected_count=(
            rejected_changed
            + rejected_artifacts
            + rejected_tests
            + rejected_risks
            + rejected_claims
        ),
    )
