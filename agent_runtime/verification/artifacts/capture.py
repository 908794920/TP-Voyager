"""Deterministic workspace snapshot and content-addressed artifact capture."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable

from agent_runtime.verification.artifacts.normalizer import DeclaredArtifact
from agent_runtime.domain.artifact import Artifact
from agent_runtime.domain.enums import (
    ArtifactOrigin,
    CaptureState,
)
from agent_runtime.domain.ids import new_artifact_id

_MAX_CAPTURE_BYTES = 20 * 1024 * 1024
_MAX_PATCH_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class WorkspaceBaseline:
    git_root: str | None = None
    head: str | None = None
    dirty: bool = False
    status_sha256: str | None = None
    changed_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "git_root": self.git_root,
            "head": self.head,
            "dirty": self.dirty,
            "status_sha256": self.status_sha256,
            "changed_files": list(self.changed_files),
        }

    @classmethod
    def from_dict(cls, value: object) -> "WorkspaceBaseline":
        data = value if isinstance(value, dict) else {}
        files = data.get("changed_files") if isinstance(data, dict) else []
        return cls(
            git_root=str(data.get("git_root") or "") or None,
            head=str(data.get("head") or "") or None,
            dirty=bool(data.get("dirty") or False),
            status_sha256=str(data.get("status_sha256") or "") or None,
            changed_files=tuple(str(item) for item in files if isinstance(item, str))
            if isinstance(files, list)
            else (),
        )


@dataclass
class ArtifactCaptureBatch:
    artifacts: list[Artifact] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    patch_available: bool = False
    patch_line_count: int = 0
    patch_bytes: int = 0
    baseline_dirty: bool = False
    head_changed: bool = False
    rejected_paths: list[str] = field(default_factory=list)
    created_blob_paths: list[Path] = field(default_factory=list)

    def cleanup_orphans(self) -> None:
        # Content-addressed blobs may be observed and committed by another
        # concurrent task between capture and this task's DB rollback.  Never
        # delete them here; an offline garbage collector can safely reclaim
        # unreferenced hashes later.
        self.created_blob_paths.clear()

    def public_artifacts(self) -> list[dict[str, object]]:
        return [
            {
                "artifact_id": item.artifact_id,
                "kind": item.kind,
                "name": item.name,
                "capture_state": item.capture_state,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in self.artifacts
        ]


def _run_git(cwd: Path, args: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _parse_status_entries(output: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"').replace("\\", "/")
        if path and (status, path) not in entries:
            entries.append((status, path))
    return entries


def _parse_status(output: str) -> list[str]:
    files: list[str] = []
    for _, path in _parse_status_entries(output):
        if path not in files:
            files.append(path)
    return files


def capture_workspace_baseline(cwd: str | Path) -> WorkspaceBaseline:
    """Best-effort Git baseline captured before dispatch; never raises."""
    root = Path(cwd).resolve()
    try:
        top = _run_git(root, ["rev-parse", "--show-toplevel"])
        if top.returncode != 0:
            return WorkspaceBaseline()
        git_root = Path(top.stdout.decode("utf-8", "replace").strip()).resolve()
        head_result = _run_git(git_root, ["rev-parse", "HEAD"])
        head = (
            head_result.stdout.decode("ascii", "replace").strip()
            if head_result.returncode == 0
            else None
        )
        status = _run_git(git_root, ["status", "--porcelain=v1", "--untracked-files=all"])
        raw = status.stdout if status.returncode == 0 else b""
        changed = _parse_status(raw.decode("utf-8", "replace"))
        return WorkspaceBaseline(
            git_root=str(git_root),
            head=head,
            dirty=bool(changed),
            status_sha256=hashlib.sha256(raw).hexdigest(),
            changed_files=tuple(changed),
        )
    except (OSError, subprocess.SubprocessError):
        return WorkspaceBaseline()


def _resolve_workspace_path(cwd: Path, relpath: str) -> Path | None:
    normalized = relpath.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or (len(normalized) >= 2 and normalized[1] == ":")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        return None
    candidate = (cwd / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(cwd)
    except ValueError:
        return None
    return candidate


class ArtifactCaptureService:
    """Capture declared and Git-observed artifacts into an immutable blob store."""

    def __init__(self, store_root: str | Path) -> None:
        self.store_root = Path(store_root)

    def capture(
        self,
        *,
        task_id: str,
        attempt_id: str,
        cwd: str | Path,
        declarations: Iterable[DeclaredArtifact] = (),
        baseline: WorkspaceBaseline | None = None,
        observe_git: bool = True,
    ) -> ArtifactCaptureBatch:
        workspace = Path(cwd).resolve()
        baseline = baseline or WorkspaceBaseline()
        batch = ArtifactCaptureBatch(baseline_dirty=baseline.dirty)
        now = time.time()

        if observe_git:
            observed, patch, current_head = self._git_observation(workspace)
        else:
            observed, patch, current_head = [], b"", baseline.head
        batch.changed_files = observed
        batch.head_changed = bool(
            baseline.head and current_head and baseline.head != current_head
        )

        declared_by_path: dict[str, DeclaredArtifact] = {}
        for item in declarations:
            declared_by_path.setdefault(item.path, item)
        for relpath in observed:
            declared_by_path.setdefault(
                relpath,
                DeclaredArtifact(
                    path=relpath,
                    kind="file",
                    name=PurePosixPath(relpath).name,
                ),
            )

        if patch:
            batch.patch_bytes = len(patch)
            batch.patch_line_count = patch.count(b"\n") + (0 if patch.endswith(b"\n") else 1)
            patch_was_truncated = len(patch) > _MAX_PATCH_BYTES
            patch = patch[:_MAX_PATCH_BYTES]
            artifact = self._capture_bytes(
                task_id=task_id,
                attempt_id=attempt_id,
                kind="patch",
                name="workspace.patch",
                relpath=None,
                content=patch,
                metadata={
                    "source": "git_diff",
                    "truncated": patch_was_truncated,
                    "baseline_head": baseline.head,
                    "current_head": current_head,
                    "baseline_dirty": baseline.dirty,
                },
                now=now,
                batch=batch,
            )
            batch.artifacts.append(artifact)
            batch.patch_available = True

        for relpath, declaration in declared_by_path.items():
            path = _resolve_workspace_path(workspace, relpath)
            if path is None:
                batch.rejected_paths.append(relpath)
                batch.artifacts.append(
                    self._state_artifact(
                        task_id,
                        attempt_id,
                        declaration,
                        CaptureState.REJECTED.value,
                        now,
                        {"reason": "unsafe_path"},
                    )
                )
                continue
            if not path.is_file():
                batch.artifacts.append(
                    self._state_artifact(
                        task_id,
                        attempt_id,
                        declaration,
                        CaptureState.MISSING.value,
                        now,
                        {"reason": "not_a_file"},
                    )
                )
                continue
            try:
                size = path.stat().st_size
                if size > _MAX_CAPTURE_BYTES:
                    batch.artifacts.append(
                        self._state_artifact(
                            task_id,
                            attempt_id,
                            declaration,
                            CaptureState.REJECTED.value,
                            now,
                            {"reason": "size_limit", "size_bytes": size},
                        )
                    )
                    continue
                content = path.read_bytes()
            except OSError:
                batch.artifacts.append(
                    self._state_artifact(
                        task_id,
                        attempt_id,
                        declaration,
                        CaptureState.MISSING.value,
                        now,
                        {"reason": "read_failed"},
                    )
                )
                continue
            batch.artifacts.append(
                self._capture_bytes(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    kind=declaration.kind,
                    name=declaration.name or path.name,
                    relpath=relpath,
                    content=content,
                    metadata={"source": "workspace", **declaration.metadata},
                    now=now,
                    batch=batch,
                )
            )
        return batch

    def _git_observation(self, cwd: Path) -> tuple[list[str], bytes, str | None]:
        try:
            top = _run_git(cwd, ["rev-parse", "--show-toplevel"])
            if top.returncode != 0:
                return [], b"", None
            root = Path(top.stdout.decode("utf-8", "replace").strip()).resolve()
            status = _run_git(root, ["status", "--porcelain=v1", "--untracked-files=all"])
            decoded_status = status.stdout.decode("utf-8", "replace")
            entries = _parse_status_entries(decoded_status)
            changed = [path for _, path in entries]
            patch_result = _run_git(root, ["diff", "--binary", "--no-ext-diff", "HEAD"], 30.0)
            patch_parts: list[bytes] = [
                patch_result.stdout if patch_result.returncode == 0 else b""
            ]
            # ``git diff HEAD`` deliberately omits untracked files.  Capture
            # each untracked regular file as a no-index patch so a new-file-only
            # task still has deterministic patch evidence.
            for status_code, relpath in entries:
                if status_code != "??":
                    continue
                candidate = _resolve_workspace_path(root, relpath)
                if candidate is None or not candidate.is_file():
                    continue
                try:
                    if candidate.stat().st_size > _MAX_CAPTURE_BYTES:
                        continue
                except OSError:
                    continue
                untracked = _run_git(
                    root,
                    ["diff", "--binary", "--no-index", "--", os.devnull, relpath],
                    30.0,
                )
                # no-index returns 1 when differences are present.
                if untracked.returncode in {0, 1} and untracked.stdout:
                    patch_parts.append(untracked.stdout)
            patch = b"\n".join(part.rstrip(b"\n") for part in patch_parts if part)
            if patch:
                patch += b"\n"
            head_result = _run_git(root, ["rev-parse", "HEAD"])
            head = head_result.stdout.decode("ascii", "replace").strip() if head_result.returncode == 0 else None
            return changed, patch, head
        except (OSError, subprocess.SubprocessError):
            return [], b"", None

    def _capture_bytes(
        self,
        *,
        task_id: str,
        attempt_id: str,
        kind: str,
        name: str,
        relpath: str | None,
        content: bytes,
        metadata: dict[str, object],
        now: float,
        batch: ArtifactCaptureBatch,
    ) -> Artifact:
        digest = hashlib.sha256(content).hexdigest()
        storage_key = f"sha256/{digest[:2]}/{digest}"
        target = self.store_root / "sha256" / digest[:2] / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fd, temp_name = tempfile.mkstemp(prefix="artifact-", dir=str(target.parent))
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # Hard-linking a fully fsynced temporary file is an
                    # atomic create-if-absent operation.  Unlike os.replace,
                    # it never overwrites a blob another task just published.
                    os.link(temp_path, target)
                    batch.created_blob_paths.append(target)
                except FileExistsError:
                    pass
            finally:
                temp_path.unlink(missing_ok=True)
        return Artifact(
            artifact_id=new_artifact_id(),
            task_id=task_id,
            attempt_id=attempt_id,
            origin=ArtifactOrigin.RUNTIME.value,
            kind=kind,
            name=name[:512],
            workspace_relpath=relpath,
            storage_key=storage_key,
            capture_state=CaptureState.CAPTURED.value,
            sha256=digest,
            size_bytes=len(content),
            declared_at=now,
            captured_at=now,
            created_at=now,
            updated_at=now,
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        )

    @staticmethod
    def _state_artifact(
        task_id: str,
        attempt_id: str,
        declaration: DeclaredArtifact,
        state: str,
        now: float,
        metadata: dict[str, object],
    ) -> Artifact:
        return Artifact(
            artifact_id=new_artifact_id(),
            task_id=task_id,
            attempt_id=attempt_id,
            origin=ArtifactOrigin.RUNTIME.value,
            kind=declaration.kind,
            name=(declaration.name or PurePosixPath(declaration.path).name)[:512],
            workspace_relpath=declaration.path,
            storage_key=None,
            capture_state=state,
            sha256=None,
            size_bytes=None,
            declared_at=now,
            captured_at=None,
            created_at=now,
            updated_at=now,
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        )
