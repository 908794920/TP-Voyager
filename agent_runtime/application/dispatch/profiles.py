"""Trusted bounded text resolution for Worker roles, skills and Captain refs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from agent_runtime.domain.dispatch import TrustedInstructionRef, WorkerProfileRef, WorkerSkillRef

MAX_WORKER_PROFILE_BYTES = 64 * 1024


class WorkerProfileError(ValueError):
    pass


class TrustedTextError(ValueError):
    pass


class TrustedTextResolver:
    """Content-addressed UTF-8 resolver constrained to operator-owned roots."""

    def __init__(self, roots: Mapping[str, str | Path]) -> None:
        resolved: dict[str, Path] = {}
        for alias, root in roots.items():
            key = str(alias or "").strip()
            if not key:
                continue
            resolved[key] = Path(root).expanduser().resolve()
        self.roots = resolved

    def resolve(self, *, root_alias: str, relative_path: str, sha256: str, max_bytes: int) -> str:
        root = self.roots.get(str(root_alias or "").strip())
        if root is None:
            raise TrustedTextError("trusted instruction root alias is not configured")
        candidate = (root / Path(*str(relative_path).replace("\\", "/").split("/"))).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise TrustedTextError("trusted instruction escaped the configured root") from exc
        if not candidate.is_file():
            raise TrustedTextError("trusted instruction was not found")
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise TrustedTextError("trusted instruction could not be read") from exc
        if not data or len(data) > int(max_bytes):
            raise TrustedTextError("trusted instruction size is outside the bounded limit")
        if b"\x00" in data:
            raise TrustedTextError("trusted instruction must be UTF-8 text")
        if hashlib.sha256(data).hexdigest() != str(sha256).lower():
            raise TrustedTextError("trusted instruction sha256 mismatch")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TrustedTextError("trusted instruction must be UTF-8 text") from exc


@dataclass(frozen=True)
class ResolvedWorkerProfile:
    ref: WorkerProfileRef
    content: str


class WorkerProfileResolver:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._trusted = TrustedTextResolver({"worker": self.root})

    def resolve(self, ref: WorkerProfileRef) -> ResolvedWorkerProfile:
        try:
            content = self._trusted.resolve(
                root_alias="worker",
                relative_path=f"{ref.name}/{ref.version}.md",
                sha256=ref.sha256,
                max_bytes=MAX_WORKER_PROFILE_BYTES,
            )
        except TrustedTextError as exc:
            raise WorkerProfileError(str(exc).replace("trusted instruction", "worker profile")) from exc
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


def resolve_trusted_instruction_refs(
    refs: tuple[TrustedInstructionRef, ...], roots: Mapping[str, str | Path],
) -> tuple[str, ...]:
    resolver = TrustedTextResolver(roots)
    contents: list[str] = []
    for ref in refs:
        contents.append(
            resolver.resolve(
                root_alias=ref.root_alias,
                relative_path=ref.path,
                sha256=ref.sha256,
                max_bytes=ref.max_bytes,
            )
        )
    return tuple(contents)
