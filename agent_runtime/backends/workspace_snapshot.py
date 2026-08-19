"""Disposable workspace snapshots for read-only Crew routes.

A workspace snapshot is workspace-exposure isolation, not an OS sandbox: the
local vendor process still runs with the host user's privileges, but its cwd
is a temp copy of the Passenger repository from which sensitive paths (VCS
metadata, tool config, credential files, private keys) have been physically
excluded.  Vendor native tools therefore scan only the snapshot, so sensitive
content cannot reach their output regardless of which built-in tool performs
the read.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from agent_runtime.domain.dispatch import (
    _MANDATORY_FORBIDDEN,
    _MANDATORY_SENSITIVE_FILES,
    sensitive_path_matches,
)


def materialize_workspace_snapshot(
    source_cwd: str,
) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Copy the source workspace into a temp dir excluding sensitive paths.

    Returns ``(temporary_directory, snapshot_root)``.  The caller owns cleanup
    of the returned ``TemporaryDirectory``.
    """
    source_root = Path(source_cwd).resolve(strict=True)
    temp = tempfile.TemporaryDirectory(prefix="tp-voyager-readonly-workspace-")
    snapshot_root = Path(temp.name)
    try:
        _copy_tree_excluding_sensitive(source_root, snapshot_root)
        return temp, snapshot_root
    except Exception:
        temp.cleanup()
        raise


def _copy_tree_excluding_sensitive(source_root: Path, snapshot_root: Path) -> None:
    for current, dirnames, filenames in os.walk(source_root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(source_root) if current_path != source_root else None

        # Prune sensitive directories in-place so os.walk never descends into
        # them (this also avoids copying large VCS object stores).
        kept_dirs: list[str] = []
        for name in dirnames:
            rel = name if rel_dir is None else (rel_dir / name).as_posix()
            if not sensitive_path_matches(rel, _MANDATORY_FORBIDDEN, _MANDATORY_SENSITIVE_FILES):
                kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in filenames:
            rel = name if rel_dir is None else (rel_dir / name).as_posix()
            if sensitive_path_matches(rel, _MANDATORY_FORBIDDEN, _MANDATORY_SENSITIVE_FILES):
                continue
            src = current_path / name
            if src.is_symlink():
                continue
            dest = snapshot_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest, follow_symlinks=False)
