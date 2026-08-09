"""Bounded, fail-closed input Artifact reads for Captain dispatch."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.application.task_service import TaskService
from agent_runtime.domain.dispatch import InputArtifactRef
from agent_runtime.domain.enums import CaptureState, TaskStatus


class ArtifactInputError(ValueError):
    pass


_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer|api[_-]?key\s*[:=]|password\s*[:=]|token\s*[:=]|-----begin private key-----)"
)
_INSTRUCTION_PATTERN = re.compile(
    r"(?im)(?:^\s*(?:system|assistant|developer|captain|runtime)\s*:|"
    r"\b(?:ignore|disregard|override|bypass)\b.{0,80}\b(?:instruction|constraint|policy|rule)s?\b|"
    r"\bexecute\b.{0,80}\b(?:command|tool|terminal|script)\b|"
    r"\bfollow\b.{0,40}\bthese instructions\b)"
)


@dataclass(frozen=True)
class ResolvedInputArtifact:
    ref: InputArtifactRef
    content: str


class ArtifactInputResolver:
    """Read each content-addressed blob once and use that verified snapshot."""
    def __init__(self, tasks: TaskService, store_root: str | Path) -> None:
        self._tasks = tasks
        self._root = Path(store_root).resolve()

    def resolve(self, refs: tuple[InputArtifactRef, ...]) -> tuple[ResolvedInputArtifact, ...]:
        result: list[ResolvedInputArtifact] = []
        for ref in refs:
            source_task = self._tasks.get_task(ref.source_task_id)
            if source_task is None:
                raise ArtifactInputError("ARTIFACT_SOURCE_TASK_NOT_FOUND")
            if str(getattr(source_task, "status", "") or "") != TaskStatus.COMPLETED.value:
                raise ArtifactInputError("ARTIFACT_SOURCE_TASK_NOT_COMPLETED")
            if not bool(getattr(source_task, "result_available", False)) or not getattr(source_task, "result_json", None):
                raise ArtifactInputError("ARTIFACT_SOURCE_TASK_NOT_VERIFIED")
            try:
                source_result = json.loads(str(source_task.result_json))
                verification = source_result.get("verification") if isinstance(source_result, dict) else None
                verification_status = str((verification or {}).get("status") or "").upper()
            except (TypeError, ValueError):
                raise ArtifactInputError("ARTIFACT_SOURCE_TASK_NOT_VERIFIED")
            if verification_status != "PASSED":
                raise ArtifactInputError("ARTIFACT_SOURCE_TASK_NOT_VERIFIED")
            artifact = self._tasks.get_artifact(ref.artifact_id)
            if artifact is None or artifact.task_id != ref.source_task_id:
                raise ArtifactInputError("ARTIFACT_INPUT_NOT_FOUND")
            if artifact.capture_state != CaptureState.CAPTURED.value or artifact.sha256 != ref.sha256:
                raise ArtifactInputError("ARTIFACT_INPUT_CHANGED")
            if artifact.size_bytes != ref.byte_size or not artifact.storage_key:
                raise ArtifactInputError("ARTIFACT_INPUT_CHANGED")
            try:
                metadata = json.loads(str(artifact.metadata_json or "{}"))
            except (TypeError, ValueError):
                raise ArtifactInputError("ARTIFACT_INPUT_METADATA_INVALID")
            if not isinstance(metadata, dict):
                raise ArtifactInputError("ARTIFACT_INPUT_METADATA_INVALID")
            persisted_kind = str(metadata.get("input_kind") or "").strip()
            if not persisted_kind or persisted_kind != ref.kind:
                raise ArtifactInputError("ARTIFACT_TYPE_NOT_ALLOWED_AS_INPUT")
            allowed_semantics = (
                {"bounded_text"}
                if artifact.kind == "file"
                else {"research_summary", "technical_report", "structured_evidence", "bounded_text"}
                if artifact.kind == "report"
                else set()
            )
            if ref.kind not in allowed_semantics:
                raise ArtifactInputError("ARTIFACT_TYPE_NOT_ALLOWED_AS_INPUT")
            if bool(metadata.get("credential_bearing")) or str(metadata.get("security_scan") or "").lower() in {"failed", "rejected"}:
                raise ArtifactInputError("ARTIFACT_INPUT_CREDENTIAL_BEARING")
            key = str(artifact.storage_key).replace("\\", "/")
            if not key.startswith("sha256/") or ".." in key.split("/"):
                raise ArtifactInputError("ARTIFACT_INPUT_UNAVAILABLE")
            path = (self._root / key).resolve()
            try:
                path.relative_to(self._root)
                data = path.read_bytes()
            except (OSError, ValueError) as exc:
                raise ArtifactInputError("ARTIFACT_INPUT_UNAVAILABLE") from exc
            if len(data) != ref.byte_size or hashlib.sha256(data).hexdigest() != ref.sha256:
                raise ArtifactInputError("ARTIFACT_INPUT_CHANGED")
            if b"\x00" in data:
                raise ArtifactInputError("ARTIFACT_INPUT_BINARY")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ArtifactInputError("ARTIFACT_INPUT_BINARY") from exc
            if any(ord(character) < 32 and character not in "\t\n\r" for character in content):
                raise ArtifactInputError("ARTIFACT_INPUT_BINARY")
            lowered = content.lower()
            if any(marker in lowered for marker in (
                "workspace.patch", "raw transcript",
                "[trusted worker profile]", "[trusted worker skills]", "[untrusted input artifacts]",
                "# assigned bounded task", "ignore previous instructions",
            )) or _CREDENTIAL_PATTERN.search(content) or _INSTRUCTION_PATTERN.search(content):
                raise ArtifactInputError("ARTIFACT_INPUT_UNTRUSTED")
            result.append(ResolvedInputArtifact(ref, content))
        return tuple(result)
