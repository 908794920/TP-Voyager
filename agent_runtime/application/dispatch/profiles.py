"""Trusted Worker profile resolution for Captain-controlled dispatch.

Profiles are plain UTF-8 Markdown owned by the operator.  A dispatch supplies
name/version/sha256; TP-Voyager resolves the configured store and refuses any
mismatch.  Profile content is injected into the transient worker prompt and is
never persisted in Session metadata.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.domain.dispatch import WorkerProfileRef, WorkerSkillRef

MAX_WORKER_PROFILE_BYTES = 64 * 1024


class WorkerProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedWorkerProfile:
    ref: WorkerProfileRef
    content: str


class WorkerProfileResolver:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def resolve(self, ref: WorkerProfileRef) -> ResolvedWorkerProfile:
        candidate = (self.root / ref.name / f"{ref.version}.md").resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkerProfileError("worker profile escaped the configured store") from exc
        if not candidate.is_file():
            raise WorkerProfileError("worker profile was not found")
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise WorkerProfileError("worker profile could not be read") from exc
        if not data or len(data) > MAX_WORKER_PROFILE_BYTES:
            raise WorkerProfileError("worker profile size is outside the bounded limit")
        if b"\x00" in data:
            raise WorkerProfileError("worker profile must be UTF-8 text")
        digest = hashlib.sha256(data).hexdigest()
        if digest != ref.sha256:
            raise WorkerProfileError("worker profile sha256 mismatch")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkerProfileError("worker profile must be UTF-8 text") from exc
        return ResolvedWorkerProfile(ref=ref, content=content)


@dataclass(frozen=True)
class ResolvedWorkerSkill:
    ref: WorkerSkillRef
    content: str


class WorkerSkillResolver:
    """Use the same trusted-store and hash checks as Worker profiles."""
    def __init__(self, root: str | Path) -> None:
        self._profiles = WorkerProfileResolver(root)

    def resolve(self, ref: WorkerSkillRef) -> ResolvedWorkerSkill:
        resolved = self._profiles.resolve(WorkerProfileRef(ref.name, ref.version, ref.sha256, ref.allowed_models))
        if len(resolved.content.encode("utf-8")) > ref.max_bytes:
            raise WorkerProfileError("worker skill exceeds manifest max_bytes")
        return ResolvedWorkerSkill(ref=ref, content=resolved.content)
