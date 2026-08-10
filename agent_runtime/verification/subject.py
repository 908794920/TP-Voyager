"""Apply-receipt validation and disposable verification-subject reconstruction."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from agent_runtime.application.dispatch.workspace import PatchWorkspace, PatchWorkspaceError, PatchWorkspaceService
from agent_runtime.application.task_service import parse_session_metadata
from agent_runtime.domain.dispatch import ApplyReceipt
from agent_runtime.persistence.artifact_repository import ArtifactRepository
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.session_repository import SessionRepository


class VerificationSubjectError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _git(cwd: Path, *args: str, input_bytes: bytes | None = None, env: dict[str, str] | None = None, timeout: float = 60.0) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), input=input_bytes,
            stdin=None if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            check=False, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationSubjectError("VERIFICATION_WORKSPACE_FAILED", f"git operation failed: {type(exc).__name__}") from exc


def _git_text(cwd: Path, *args: str) -> str:
    result = _git(cwd, *args)
    if result.returncode != 0:
        raise VerificationSubjectError("VERIFICATION_WORKSPACE_FAILED", f"git {' '.join(args)} failed")
    return result.stdout.decode("utf-8", "replace").strip()


def repository_identity(cwd: str | Path) -> str:
    root = Path(_git_text(Path(cwd).resolve(), "rev-parse", "--show-toplevel")).resolve()
    origin = _git(root, "remote", "get-url", "origin")
    origin_text = origin.stdout.decode("utf-8", "replace").strip() if origin.returncode == 0 else ""
    roots = _git(root, "rev-list", "--max-parents=0", "HEAD")
    root_commits = sorted(line.strip() for line in roots.stdout.decode("ascii", "replace").splitlines() if line.strip()) if roots.returncode == 0 else []
    payload = json.dumps({"origin": origin_text, "root_commits": root_commits}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def worktree_tree_hash(cwd: str | Path) -> str:
    root = Path(_git_text(Path(cwd).resolve(), "rev-parse", "--show-toplevel")).resolve()
    fd, index_name = tempfile.mkstemp(prefix="tp-voyager-index-")
    os.close(fd)
    try:
        Path(index_name).unlink(missing_ok=True)
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = index_name
        head = _git(root, "rev-parse", "HEAD")
        if head.returncode != 0:
            raise VerificationSubjectError("APPLY_RECEIPT_INVALID", "Passenger repository has no resolvable HEAD")
        read_tree = _git(root, "read-tree", head.stdout.decode("ascii", "replace").strip(), env=env)
        if read_tree.returncode != 0:
            raise VerificationSubjectError("APPLY_RECEIPT_INVALID", "failed to initialize temporary Git index")
        add = _git(root, "add", "-A", "--", ".", env=env)
        if add.returncode != 0:
            raise VerificationSubjectError("APPLY_RECEIPT_INVALID", "failed to hash Passenger worktree")
        tree = _git(root, "write-tree", env=env)
        if tree.returncode != 0:
            raise VerificationSubjectError("APPLY_RECEIPT_INVALID", "failed to write temporary tree")
        return tree.stdout.decode("ascii", "replace").strip()
    finally:
        Path(index_name).unlink(missing_ok=True)


def git_status_digest(cwd: str | Path) -> tuple[str, tuple[str, ...]]:
    root = Path(_git_text(Path(cwd).resolve(), "rev-parse", "--show-toplevel")).resolve()
    result = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if result.returncode != 0:
        raise VerificationSubjectError("APPLY_RECEIPT_INVALID", "failed to inspect Passenger Git status")
    raw = bytes(result.stdout)
    changed: list[str] = []
    entries = raw.decode("utf-8", "replace").split("\x00")
    for entry in entries:
        if len(entry) >= 4:
            path = entry[3:].strip().replace("\\", "/")
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path and path not in changed:
                changed.append(path)
    return hashlib.sha256(raw).hexdigest(), tuple(changed)


class VerificationSubjectService:
    def __init__(self, db: Database, workspace_root: str | Path) -> None:
        self.db = db
        self.workspace_root = Path(workspace_root).resolve()
        self.artifacts = ArtifactRepository(db)
        self.sessions = SessionRepository(db)

    def prepare(self, receipt: ApplyReceipt, passenger_cwd: str | Path) -> PatchWorkspace:
        canonical = json.dumps(receipt.canonical_body(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != receipt.receipt_sha256:
            raise VerificationSubjectError("APPLY_RECEIPT_HASH_MISMATCH", "apply receipt sha256 mismatch")
        if receipt.conflicts:
            raise VerificationSubjectError("APPLY_RECEIPT_CONFLICTED", "conflicted apply receipt cannot be verified")
        artifact = self.artifacts.get(receipt.patch_artifact_id)
        if artifact is None or artifact.kind != "patch" or artifact.capture_state != "captured":
            raise VerificationSubjectError("APPLY_RECEIPT_PATCH_NOT_FOUND", "referenced Patch Artifact is unavailable")
        if artifact.sha256 != receipt.patch_sha256 or not artifact.storage_key:
            raise VerificationSubjectError("APPLY_RECEIPT_PATCH_MISMATCH", "Patch Artifact hash does not match receipt")
        try:
            artifact_meta = json.loads(artifact.metadata_json or "{}")
        except json.JSONDecodeError:
            artifact_meta = {}
        if artifact_meta.get("baseline_head") and str(artifact_meta.get("baseline_head")) != receipt.base_commit:
            raise VerificationSubjectError("APPLY_RECEIPT_BASE_MISMATCH", "receipt base commit does not match Patch Artifact")
        session = self.sessions.get_by_task_id(artifact.task_id)
        if session is None:
            raise VerificationSubjectError("APPLY_RECEIPT_PATCH_NOT_FOUND", "Patch Artifact source task session is unavailable")
        metadata = parse_session_metadata(session.metadata_json)
        source_root = str(metadata.get("source_cwd") or metadata.get("cwd") or "").strip()
        base_revision = str(metadata.get("workspace_base_revision") or artifact_meta.get("baseline_head") or "").strip()
        if not source_root or not base_revision or base_revision != receipt.base_commit:
            raise VerificationSubjectError("APPLY_RECEIPT_BASE_MISMATCH", "Patch source base cannot be proven")
        source = Path(source_root).resolve()
        passenger = Path(passenger_cwd).resolve()
        if repository_identity(source) != receipt.repository_identity or repository_identity(passenger) != receipt.repository_identity:
            raise VerificationSubjectError("APPLY_RECEIPT_REPOSITORY_MISMATCH", "receipt repository identity does not match source/Passenger repository")
        base_tree = _git_text(source, "rev-parse", f"{receipt.base_commit}^{{tree}}")
        if base_tree != receipt.base_tree_hash:
            raise VerificationSubjectError("APPLY_RECEIPT_BASE_MISMATCH", "receipt base tree does not match Git object")
        passenger_tree = worktree_tree_hash(passenger)
        status_digest, changed_files = git_status_digest(passenger)
        if passenger_tree != receipt.result_tree_hash or status_digest != receipt.git_status_digest:
            raise VerificationSubjectError("APPLY_RECEIPT_SUBJECT_MISMATCH", "Passenger workspace no longer matches the Apply Receipt")
        if tuple(sorted(changed_files)) != tuple(sorted(receipt.changed_files)):
            raise VerificationSubjectError("APPLY_RECEIPT_SUBJECT_MISMATCH", "Passenger changed-file set does not match the Apply Receipt")

        patch_path = self.db.path.parent / "artifacts" / Path(*artifact.storage_key.split("/"))
        try:
            patch_bytes = patch_path.read_bytes()
        except OSError as exc:
            raise VerificationSubjectError("APPLY_RECEIPT_PATCH_NOT_FOUND", "Patch Artifact blob cannot be read") from exc
        if hashlib.sha256(patch_bytes).hexdigest() != receipt.patch_sha256:
            raise VerificationSubjectError("APPLY_RECEIPT_PATCH_MISMATCH", "Patch Artifact blob hash mismatch")

        self.workspace_root.mkdir(parents=True, exist_ok=True)
        target = (self.workspace_root / f"verification-{uuid.uuid4().hex[:24]}").resolve()
        try:
            target.relative_to(self.workspace_root)
        except ValueError as exc:
            raise VerificationSubjectError("VERIFICATION_WORKSPACE_FAILED", "invalid verification workspace path") from exc
        add = _git(source, "worktree", "add", "--detach", str(target), receipt.base_commit, timeout=60.0)
        if add.returncode != 0:
            raise VerificationSubjectError("VERIFICATION_WORKSPACE_FAILED", "failed to create disposable verification worktree")
        workspace = PatchWorkspace(str(source), str(target), receipt.base_commit, reused=False)
        try:
            applied = _git(target, "apply", "--binary", "--index", "-", input_bytes=patch_bytes, timeout=60.0)
            if applied.returncode != 0:
                raise VerificationSubjectError("APPLY_RECEIPT_SUBJECT_MISMATCH", "Patch Artifact cannot be reconstructed from the receipt base")
            rebuilt_tree = _git_text(target, "write-tree")
            if rebuilt_tree != receipt.result_tree_hash:
                raise VerificationSubjectError("APPLY_RECEIPT_SUBJECT_MISMATCH", "reconstructed verification tree does not match Apply Receipt")
            return workspace
        except BaseException:
            try:
                PatchWorkspaceService(self.workspace_root).cleanup(workspace)
            except PatchWorkspaceError:
                pass
            raise
