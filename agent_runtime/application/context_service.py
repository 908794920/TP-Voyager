"""Explicit, bounded project-context manifest service.

Registration persists only normalized relative paths, SHA-256 values, sizes,
and a deterministic root hash.  Reading file content requires the explicit
``render`` operation and validates the current bytes against the manifest.
Nothing here injects content into a task or dispatches a backend.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from agent_runtime.domain.context import ContextEntry, ContextManifest
from agent_runtime.domain.dispatch import ReadScope
from agent_runtime.domain.ids import new_context_manifest_id
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.context_repository import ContextRepository


MAX_CONTEXT_FILES = 256
MAX_CONTEXT_TOTAL_BYTES = 8 * 1024 * 1024
DEFAULT_RENDER_BYTES = 2 * 1024 * 1024
_CONTEXT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class ContextError(ValueError):
    pass


class ContextNotFoundError(ContextError):
    pass


class ContextConflictError(ContextError):
    pass


class ContextDriftError(ContextError):
    pass


@dataclass(frozen=True)
class ContextRegistrationResult:
    manifest: dict[str, Any]
    replayed: bool


class ProjectContextService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = ContextRepository(db)

    def resolve_read_scope(self, cwd: str, scope: ReadScope) -> list[str]:
        """Expand one normalized read scope into a bounded concrete file set.

        CodeBuddy and Qoder receive the same concrete set.  Mandatory vendor
        state directories are always excluded; external symlinks are rejected.
        """
        root = self._root(cwd)
        mandatory_forbidden = (".git", ".codebuddy", ".qoder")

        def forbidden(relpath: str) -> bool:
            return any(
                relpath == prefix or relpath.startswith(prefix + "/")
                for prefix in mandatory_forbidden
            )

        resolved: set[str] = set()

        for relpath in scope.files:
            normalized = self._normalize_relpath(relpath)
            if forbidden(normalized):
                raise ContextError("read_scope includes a mandatory forbidden path")
            candidate = self._resolved_candidate(
                root, normalized, allow_external_symlinks=False
            )
            if not candidate.is_file():
                raise ContextError(f"read_scope file does not exist: {normalized}")
            resolved.add(normalized)

        for relpath in scope.directories:
            normalized = self._normalize_relpath(relpath)
            if forbidden(normalized):
                raise ContextError("read_scope includes a mandatory forbidden path")
            directory = self._resolved_candidate(
                root, normalized, allow_external_symlinks=False
            )
            if not directory.is_dir():
                raise ContextError(f"read_scope directory does not exist: {normalized}")
            for candidate in directory.rglob("*"):
                if not candidate.is_file():
                    continue
                try:
                    rel = candidate.resolve(strict=True).relative_to(root).as_posix()
                except (OSError, RuntimeError, ValueError) as exc:
                    raise ContextError("read_scope directory contains an external symlink") from exc
                if not forbidden(rel):
                    resolved.add(rel)
                if len(resolved) > MAX_CONTEXT_FILES:
                    raise ContextError(f"read_scope file limit is {MAX_CONTEXT_FILES}")

        for pattern in scope.globs:
            matched = False
            try:
                candidates = root.glob(pattern)
            except (OSError, ValueError) as exc:
                raise ContextError("read_scope glob could not be evaluated") from exc
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    rel = candidate.resolve(strict=True).relative_to(root).as_posix()
                except (OSError, RuntimeError, ValueError) as exc:
                    raise ContextError("read_scope glob resolved outside cwd") from exc
                if forbidden(rel):
                    continue
                matched = True
                resolved.add(rel)
                if len(resolved) > MAX_CONTEXT_FILES:
                    raise ContextError(f"read_scope file limit is {MAX_CONTEXT_FILES}")
            if not matched:
                raise ContextError(f"read_scope glob matched no files: {pattern}")

        if not resolved:
            raise ContextError("read_scope resolved to no readable files")
        ordered = sorted(resolved)
        # Use the existing capture boundary to enforce existence, symlink and
        # aggregate byte limits for both Crew families.
        self._capture(root, ordered, allow_external_symlinks=False)
        return ordered

    def register(
        self,
        cwd: str,
        files: Iterable[str],
        *,
        context_id: str = "",
        allow_external_symlinks: bool = False,
    ) -> ContextRegistrationResult:
        root = self._root(cwd)
        normalized = self._normalize_file_list(files)
        entries = self._capture(
            root,
            normalized,
            allow_external_symlinks=bool(allow_external_symlinks),
        )
        identifier = self._context_id(context_id)
        root_hash = self._root_hash(entries)
        total_bytes = sum(item.size_bytes for item in entries)
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            existing = self.repo.get_manifest_in_connection(connection, identifier)
            if existing is not None:
                existing_entries = self.repo.list_entries_in_connection(
                    connection, identifier,
                )
                if (
                    existing.root_hash == root_hash
                    and [
                        (item.relpath, item.sha256, item.size_bytes)
                        for item in existing_entries
                    ]
                    == [
                        (item.relpath, item.sha256, item.size_bytes)
                        for item in entries
                    ]
                ):
                    return ContextRegistrationResult(
                        self._projection(existing, existing_entries), replayed=True,
                    )
                raise ContextConflictError(
                    "context_id already exists with a different manifest"
                )
            durable_entries = [
                ContextEntry(
                    context_id=identifier,
                    relpath=item.relpath,
                    sha256=item.sha256,
                    size_bytes=item.size_bytes,
                )
                for item in entries
            ]
            manifest = ContextManifest(
                context_id=identifier,
                root_hash=root_hash,
                file_count=len(durable_entries),
                total_bytes=total_bytes,
                created_at=db_now,
            )
            self.repo.create(connection, manifest, durable_entries)
            return ContextRegistrationResult(
                self._projection(manifest, durable_entries), replayed=False,
            )

    def get(self, context_id: str) -> dict[str, Any]:
        identifier = self._required_context_id(context_id)
        manifest = self.repo.get_manifest(identifier)
        if manifest is None:
            raise ContextNotFoundError("context manifest not found")
        return self._projection(manifest, self.repo.list_entries(identifier))

    def verify(
        self,
        context_id: str,
        cwd: str,
        *,
        allow_external_symlinks: bool = False,
    ) -> dict[str, Any]:
        expected = self.get(context_id)
        root = self._root(cwd)
        current: list[ContextEntry] = []
        issues: list[dict[str, str]] = []
        for item in expected["entries"]:
            relpath = str(item["relpath"])
            try:
                captured = self._capture_one(
                    root,
                    relpath,
                    allow_external_symlinks=bool(allow_external_symlinks),
                )
            except ContextError as exc:
                issues.append({"relpath": relpath, "issue": str(exc)})
                continue
            current.append(captured)
            if captured.sha256 != item["sha256"]:
                issues.append({"relpath": relpath, "issue": "sha256_mismatch"})
            if captured.size_bytes != item["size_bytes"]:
                issues.append({"relpath": relpath, "issue": "size_mismatch"})
        current_hash = self._root_hash(current) if len(current) == len(expected["entries"]) else ""
        if current_hash and current_hash != expected["root_hash"]:
            issues.append({"relpath": "", "issue": "root_hash_mismatch"})
        return {
            "context_id": expected["context_id"],
            "valid": not issues,
            "expected_root_hash": expected["root_hash"],
            "current_root_hash": current_hash or None,
            "checked_file_count": len(current),
            "issues": issues,
            "content_returned": False,
        }

    def render(
        self,
        context_id: str,
        cwd: str,
        *,
        allow_external_symlinks: bool = False,
        max_total_bytes: int = DEFAULT_RENDER_BYTES,
    ) -> dict[str, Any]:
        limit = int(max_total_bytes)
        if limit <= 0 or limit > MAX_CONTEXT_TOTAL_BYTES:
            raise ContextError(
                f"max_total_bytes must be between 1 and {MAX_CONTEXT_TOTAL_BYTES}"
            )
        manifest = self.get(context_id)
        if int(manifest["total_bytes"]) > limit:
            raise ContextError("context exceeds explicit render byte limit")
        root = self._root(cwd)
        sections: list[str] = []
        rendered_total = 0
        for item in manifest["entries"]:
            candidate = self._resolved_candidate(
                root,
                str(item["relpath"]),
                allow_external_symlinks=bool(allow_external_symlinks),
            )
            remaining = limit - rendered_total
            try:
                with candidate.open("rb") as handle:
                    data = handle.read(remaining + 1)
            except OSError as exc:
                raise ContextDriftError(
                    f"context file cannot be read: {item['relpath']}"
                ) from exc
            if len(data) > remaining:
                raise ContextError("context exceeds explicit render byte limit")
            rendered_total += len(data)
            if (
                len(data) != int(item["size_bytes"])
                or hashlib.sha256(data).hexdigest() != str(item["sha256"])
            ):
                raise ContextDriftError(
                    f"context file changed: {item['relpath']}"
                )
            if b"\x00" in data:
                raise ContextError(f"context file is not text: {item['relpath']}")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ContextError(
                    f"context file is not UTF-8: {item['relpath']}"
                ) from exc
            sections.append(
                f"## {item['relpath']}\n\n{text.rstrip()}\n"
            )
        body = (
            f"# Project Context {manifest['context_id']}\n\n"
            f"Root-Hash: `{manifest['root_hash']}`\n\n"
            + "\n".join(sections)
        )
        return {
            "context_id": manifest["context_id"],
            "root_hash": manifest["root_hash"],
            "file_count": manifest["file_count"],
            "total_bytes": manifest["total_bytes"],
            "content": body,
            "content_returned": True,
            "injected_into_task": False,
        }

    @staticmethod
    def _root(cwd: str) -> Path:
        raw = str(cwd or "").strip()
        if not raw:
            raise ContextError("cwd is required")
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise ContextError("cwd must be an existing directory")
        return root

    @classmethod
    def _normalize_file_list(cls, files: Iterable[str]) -> list[str]:
        if isinstance(files, (str, bytes)):
            raise ContextError("files must be a list of relative paths")
        result: list[str] = []
        seen: set[str] = set()
        for raw in files:
            relpath = cls._normalize_relpath(raw)
            if relpath in seen:
                raise ContextError(f"duplicate context file: {relpath}")
            seen.add(relpath)
            result.append(relpath)
            if len(result) > MAX_CONTEXT_FILES:
                raise ContextError(f"context file limit is {MAX_CONTEXT_FILES}")
        if not result:
            raise ContextError("at least one context file is required")
        return sorted(result)

    @staticmethod
    def _normalize_relpath(raw: str) -> str:
        value = str(raw or "").strip().replace("\\", "/")
        drive, _ = ntpath.splitdrive(value)
        if not value or drive or value.startswith("/"):
            raise ContextError("context paths must be relative")
        raw_parts = value.split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise ContextError("context path contains unsafe segments")
        path = PurePosixPath(value)
        normalized = path.as_posix()
        if len(normalized) > 512:
            raise ContextError("context path is too long")
        return normalized

    @classmethod
    def _capture(
        cls,
        root: Path,
        files: list[str],
        *,
        allow_external_symlinks: bool,
    ) -> list[ContextEntry]:
        entries: list[ContextEntry] = []
        total = 0
        for relpath in files:
            entry = cls._capture_one(
                root,
                relpath,
                allow_external_symlinks=allow_external_symlinks,
            )
            total += entry.size_bytes
            if total > MAX_CONTEXT_TOTAL_BYTES:
                raise ContextError(
                    f"context total byte limit is {MAX_CONTEXT_TOTAL_BYTES}"
                )
            entries.append(entry)
        return entries

    @classmethod
    def _capture_one(
        cls,
        root: Path,
        relpath: str,
        *,
        allow_external_symlinks: bool,
    ) -> ContextEntry:
        normalized = cls._normalize_relpath(relpath)
        candidate = cls._resolved_candidate(
            root,
            normalized,
            allow_external_symlinks=allow_external_symlinks,
        )
        if not candidate.is_file():
            raise ContextError(f"context file does not exist: {normalized}")
        digest = hashlib.sha256()
        size = 0
        try:
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > MAX_CONTEXT_TOTAL_BYTES:
                        raise ContextError("context file exceeds total byte limit")
                    digest.update(chunk)
        except OSError as exc:
            raise ContextError(f"context file cannot be read: {normalized}") from exc
        return ContextEntry(
            context_id="",
            relpath=normalized,
            sha256=digest.hexdigest(),
            size_bytes=size,
        )

    @classmethod
    def _resolved_candidate(
        cls,
        root: Path,
        relpath: str,
        *,
        allow_external_symlinks: bool,
    ) -> Path:
        normalized = cls._normalize_relpath(relpath)
        lexical = root.joinpath(*PurePosixPath(normalized).parts)
        try:
            resolved = lexical.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ContextError(f"context file does not exist: {normalized}") from exc
        if not allow_external_symlinks:
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ContextError(
                    f"context path resolves outside cwd: {normalized}"
                ) from exc
        return resolved

    @staticmethod
    def _root_hash(entries: list[ContextEntry]) -> str:
        digest = hashlib.sha256()
        for entry in sorted(entries, key=lambda item: item.relpath):
            record = json.dumps(
                [entry.relpath, entry.sha256, entry.size_bytes],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            digest.update(record.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    @staticmethod
    def _projection(
        manifest: ContextManifest,
        entries: list[ContextEntry],
    ) -> dict[str, Any]:
        return {
            **manifest.to_dict(),
            "entries": [entry.to_dict() for entry in entries],
            "content_stored": False,
            "cwd_stored": False,
            "automatic_injection": False,
        }

    @staticmethod
    def _context_id(value: str) -> str:
        identifier = str(value or "").strip() or new_context_manifest_id()
        if not _CONTEXT_ID_RE.fullmatch(identifier):
            raise ContextError("context_id contains unsupported characters")
        return identifier

    @staticmethod
    def _required_context_id(value: str) -> str:
        identifier = str(value or "").strip()
        if not _CONTEXT_ID_RE.fullmatch(identifier):
            raise ContextError("valid context_id is required")
        return identifier
