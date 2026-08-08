"""Isolated Git worktree support for TP-Voyager patch tasks.

The service is intentionally stateless.  The durable Task session metadata
stores the source root/base revision/workspace mode; SQLite remains the task
source of truth.  A dirty source tree is rejected because uncommitted state
cannot be deterministically attributed to a patch worker.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import subprocess
import uuid


class PatchWorkspaceError(RuntimeError):
    pass


class PatchWorkspaceCleanupError(PatchWorkspaceError):
    """Runtime-owned patch workspace could not be fully retired."""



@dataclass(frozen=True)
class PatchWorkspace:
    source_root: str
    worktree_root: str
    base_revision: str
    reused: bool = False


class PatchWorkspaceService:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def _git(cwd: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PatchWorkspaceError(f"git operation failed: {type(exc).__name__}") from exc

    def prepare(self, cwd: str | Path, *, idempotency_key: str = "") -> PatchWorkspace:
        requested = Path(cwd or Path.cwd()).resolve()
        if not requested.is_dir():
            raise PatchWorkspaceError("cwd is not a directory")
        top = self._git(requested, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            raise PatchWorkspaceError("patch mode requires a Git working tree")
        source = Path(top.stdout.strip()).resolve()
        head = self._git(source, "rev-parse", "HEAD")
        if head.returncode != 0 or not head.stdout.strip():
            raise PatchWorkspaceError("patch mode requires a resolvable Git HEAD")
        base_revision = head.stdout.strip()
        status = self._git(source, "status", "--porcelain=v1", "--untracked-files=all")
        if status.returncode != 0:
            raise PatchWorkspaceError("failed to inspect source Git status")
        if status.stdout.strip():
            raise PatchWorkspaceError("patch mode requires a clean source working tree")

        self.root.mkdir(parents=True, exist_ok=True)
        key = str(idempotency_key or "").strip()
        if key:
            digest = hashlib.sha256(f"{source}\0{key}".encode("utf-8")).hexdigest()[:24]
        else:
            digest = uuid.uuid4().hex[:24]
        worktree = (self.root / f"patch-{digest}").resolve()
        try:
            worktree.relative_to(self.root)
        except ValueError as exc:  # pragma: no cover - defensive
            raise PatchWorkspaceError("invalid runtime workspace path") from exc

        if worktree.exists():
            check = self._git(worktree, "rev-parse", "--show-toplevel")
            if check.returncode == 0:
                existing_head = self._git(worktree, "rev-parse", "HEAD")
                if existing_head.returncode == 0:
                    return PatchWorkspace(
                        source_root=str(source),
                        worktree_root=str(worktree),
                        base_revision=existing_head.stdout.strip() or base_revision,
                        reused=True,
                    )
            # Never reuse an unrelated path.
            raise PatchWorkspaceError("runtime patch workspace path already exists and is invalid")

        result = self._git(source, "worktree", "add", "--detach", str(worktree), base_revision, timeout=60.0)
        if result.returncode != 0:
            raise PatchWorkspaceError("failed to create isolated Git worktree")
        return PatchWorkspace(
            source_root=str(source),
            worktree_root=str(worktree),
            base_revision=base_revision,
            reused=False,
        )

    def cleanup(self, workspace: PatchWorkspace | str, *, source_root: str | None = None) -> None:
        """Remove a Runtime-owned patch worktree and verify retirement.

        Cleanup is part of the patch completion contract, not best-effort
        housekeeping.  A task must never become externally ``completed`` while
        its isolated worktree is still live or still registered with Git.
        """
        if isinstance(workspace, PatchWorkspace):
            worktree = Path(workspace.worktree_root).resolve()
            source = Path(workspace.source_root).resolve()
        else:
            worktree = Path(workspace).resolve()
            source = Path(source_root or "").resolve() if source_root else None
        try:
            worktree.relative_to(self.root)
        except ValueError as exc:
            raise PatchWorkspaceCleanupError(
                "refusing to clean a patch workspace outside the Runtime workspace root"
            ) from exc

        removal_error: str | None = None
        if source is not None and source.is_dir():
            result = self._git(
                source, "worktree", "remove", "--force", str(worktree), timeout=60.0
            )
            if result.returncode != 0:
                removal_error = "git worktree remove failed"

        if worktree.exists():
            try:
                # Only paths already proven to be under self.root may reach this
                # fallback.  Do not ignore errors: silent leftovers are exactly
                # what the completion contract must prevent.
                shutil.rmtree(worktree)
            except OSError as exc:
                raise PatchWorkspaceCleanupError(
                    f"patch workspace directory removal failed: {type(exc).__name__}"
                ) from exc

        if source is not None and source.is_dir():
            prune = self._git(source, "worktree", "prune", timeout=30.0)
            if prune.returncode != 0:
                raise PatchWorkspaceCleanupError("git worktree prune failed")
            listed = self._git(source, "worktree", "list", "--porcelain", timeout=30.0)
            if listed.returncode != 0:
                raise PatchWorkspaceCleanupError("git worktree list failed after cleanup")
            registrations = [
                Path(line.split(" ", 1)[1].strip()).resolve()
                for line in listed.stdout.splitlines()
                if line.startswith("worktree ")
            ]
            if any(item == worktree for item in registrations):
                raise PatchWorkspaceCleanupError("patch workspace is still registered after cleanup")

        if worktree.exists():
            raise PatchWorkspaceCleanupError("patch workspace still exists after cleanup")
        if removal_error and source is None:
            raise PatchWorkspaceCleanupError(removal_error)
