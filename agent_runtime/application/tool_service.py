"""Explicit, read-only Tool Runtime foundation (V1.4).

The service exposes a small built-in catalog for filesystem, Git, and SQLite
inspection.  Every invocation is caller-driven and bounded by a workspace
root.  It never dispatches a backend, mutates a task, writes project files, or
persists raw arguments/output.  Only audit metadata and SHA-256 digests are
stored in SQLite.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import ntpath
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote

from agent_runtime.domain.ids import new_tool_invocation_id
from agent_runtime.domain.tool_runtime import (
    TOOL_CATALOG_SCHEMA,
    TOOL_HISTORY_SCHEMA,
    TOOL_RESULT_SCHEMA,
    ToolDefinition,
    ToolInvocation,
)
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.context_repository import ContextRepository
from agent_runtime.persistence.task_repository import TaskRepository
from agent_runtime.persistence.tool_repository import ToolInvocationRepository


MAX_FILE_READ_BYTES = 1024 * 1024
MAX_FILE_HASH_BYTES = 64 * 1024 * 1024
MAX_GIT_DIFF_BYTES = 2 * 1024 * 1024
MAX_GIT_STATUS_BYTES = 512 * 1024
MAX_LIST_ENTRIES = 500
MAX_SQL_ROWS = 200
MAX_SQL_RESULT_BYTES = 1024 * 1024
MAX_SQL_CELL_CHARS = 4096
MAX_HISTORY_LIMIT = 200
DEFAULT_PROCESS_TIMEOUT_SECONDS = 15.0
SQLITE_QUERY_TIMEOUT_SECONDS = 5.0
MAX_SQL_QUERY_CHARS = 64 * 1024

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_ALLOWED_STATUSES = {"succeeded", "failed", "rejected"}
_ALLOWED_SQLITE_PRAGMAS = {
    "table_info",
    "table_xinfo",
    "index_list",
    "index_info",
    "index_xinfo",
    "foreign_key_list",
    "user_version",
    "schema_version",
    "quick_check",
    "integrity_check",
    "compile_options",
}


class ToolRuntimeError(RuntimeError):
    code = "tool_runtime_error"
    status = "failed"


class ToolPolicyError(ToolRuntimeError):
    code = "policy_rejected"
    status = "rejected"


class ToolNotFoundError(ToolPolicyError):
    code = "tool_not_found"


class ToolExecutionError(ToolRuntimeError):
    code = "tool_execution_failed"


class ToolReferenceError(ToolPolicyError):
    code = "invalid_reference"


_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="filesystem.list",
        version="1.0",
        category="filesystem",
        summary="List one workspace-relative directory without recursion.",
        arguments={"path": "optional relative directory", "max_entries": "1..500"},
        limits={"max_entries": MAX_LIST_ENTRIES, "external_symlinks": False},
    ),
    ToolDefinition(
        name="filesystem.stat",
        version="1.0",
        category="filesystem",
        summary="Inspect one workspace-relative file or directory.",
        arguments={"path": "required relative path"},
        limits={"external_symlinks": False},
    ),
    ToolDefinition(
        name="filesystem.sha256",
        version="1.0",
        category="filesystem",
        summary="Explicitly hash one bounded workspace-relative file.",
        arguments={"path": "required relative file", "max_bytes": "1..67108864"},
        limits={"max_bytes": MAX_FILE_HASH_BYTES, "external_symlinks": False},
    ),
    ToolDefinition(
        name="filesystem.read_text",
        version="1.0",
        category="filesystem",
        summary="Explicitly read one bounded UTF-8 text file.",
        content_returned=True,
        arguments={"path": "required relative file", "max_bytes": "1..1048576"},
        limits={"max_bytes": MAX_FILE_READ_BYTES, "external_symlinks": False},
    ),
    ToolDefinition(
        name="git.status",
        version="1.0",
        category="git",
        summary="Read Git porcelain status for the workspace repository.",
        arguments={"max_entries": "1..500"},
        limits={"max_entries": MAX_LIST_ENTRIES, "timeout_seconds": 15},
    ),
    ToolDefinition(
        name="git.diff",
        version="1.0",
        category="git",
        summary="Explicitly read a bounded Git working-tree or staged diff.",
        content_returned=True,
        arguments={
            "path": "optional relative pathspec",
            "cached": "boolean",
            "max_bytes": "1..2097152",
        },
        limits={"max_bytes": MAX_GIT_DIFF_BYTES, "timeout_seconds": 15},
    ),
    ToolDefinition(
        name="sqlite.query",
        version="1.0",
        category="database",
        summary=(
            "Run one bounded query against a workspace-relative SQLite "
            "database opened read-only."
        ),
        content_returned=True,
        arguments={
            "database": "required relative SQLite file",
            "query": "single read-only statement",
            "parameters": "optional array or object",
            "max_rows": "1..200",
            "max_result_bytes": "1..1048576",
        },
        limits={
            "max_rows": MAX_SQL_ROWS,
            "max_result_bytes": MAX_SQL_RESULT_BYTES,
            "database_mode": "read_only",
        },
    ),
)

_DEFINITIONS_BY_NAME = {item.name: item for item in _TOOL_DEFINITIONS}


class ToolRuntimeService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = ToolInvocationRepository(db)
        self.tasks = TaskRepository(db)
        self.contexts = ContextRepository(db)
        self._handlers: dict[str, Callable[[Path, dict[str, Any]], dict[str, Any]]] = {
            "filesystem.list": self._filesystem_list,
            "filesystem.stat": self._filesystem_stat,
            "filesystem.sha256": self._filesystem_sha256,
            "filesystem.read_text": self._filesystem_read_text,
            "git.status": self._git_status,
            "git.diff": self._git_diff,
            "sqlite.query": self._sqlite_query,
        }

    # ---------------------------------------------------------------- catalog

    def catalog(self, *, category: str = "", name: str = "") -> dict[str, Any]:
        category_value = str(category or "").strip().lower()
        name_value = str(name or "").strip().lower()
        if name_value and not _TOOL_NAME_RE.fullmatch(name_value):
            raise ToolPolicyError("tool name contains unsupported characters")
        definitions = [
            item
            for item in _TOOL_DEFINITIONS
            if (not category_value or item.category == category_value)
            and (not name_value or item.name == name_value)
        ]
        return {
            "ok": True,
            "schema": TOOL_CATALOG_SCHEMA,
            "tools": [item.to_dict() for item in definitions],
            "selection_performed": False,
            "dispatch_performed": False,
            "automatic_backend_access": False,
            "writes_supported": False,
        }

    # ---------------------------------------------------------------- invoke

    def invoke(
        self,
        tool_name: str,
        cwd: str,
        *,
        arguments: dict[str, Any] | None = None,
        task_id: str = "",
        context_id: str = "",
    ) -> dict[str, Any]:
        requested_at = self._db_now()
        invocation_id = new_tool_invocation_id()
        provided_arguments: Any = {} if arguments is None else arguments
        safe_arguments = (
            dict(provided_arguments) if isinstance(provided_arguments, dict) else {}
        )
        requested_name = str(tool_name or "").strip().lower()
        valid_tool_name = bool(_TOOL_NAME_RE.fullmatch(requested_name))
        definition = (
            _DEFINITIONS_BY_NAME.get(requested_name) if valid_tool_name else None
        )
        audit_tool_name = definition.name if definition is not None else "unknown"
        input_sha256 = self._canonical_hash(
            {
                "tool_name": requested_name,
                "arguments": safe_arguments,
                "task_id": str(task_id or ""),
                "context_id": str(context_id or ""),
            }
        )
        workspace_ref = self._workspace_ref_raw(cwd)
        linked_task_id: str | None = None
        linked_context_id: str | None = None
        result: dict[str, Any] | None = None
        status = "failed"
        error_code: str | None = None
        error_message: str | None = None
        bytes_returned = 0
        item_count = 0
        output_sha256: str | None = None
        try:
            if not valid_tool_name:
                raise ToolPolicyError("tool name contains unsupported characters")
            if definition is None:
                raise ToolNotFoundError("unknown Tool Runtime tool")
            if not isinstance(provided_arguments, dict):
                raise ToolPolicyError("arguments must be an object")
            root = self._root(cwd)
            workspace_ref = self._workspace_ref(root)
            linked_task_id = self._validate_task_reference(task_id)
            linked_context_id = self._validate_context_reference(context_id)
            handler = self._handlers[definition.name]
            result = handler(root, safe_arguments)
            status = "succeeded"
            bytes_returned = int(result.pop("_bytes_returned", 0))
            item_count = int(result.pop("_item_count", 0))
            output_sha256 = self._canonical_hash(result)
        except ToolRuntimeError as exc:
            status = exc.status
            error_code = exc.code
            error_message = str(exc)
        except (OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
            status = "failed"
            error_code = "tool_execution_failed"
            error_message = self._safe_operational_message(exc)
        except Exception:
            # Preserve the audit trail without leaking implementation details.
            # Programmer defects still surface as a failed, content-free tool
            # invocation rather than escaping the MCP boundary unaudited.
            status = "failed"
            error_code = "tool_internal_error"
            error_message = "tool operation failed internally"

        finished_at = max(self._db_now(), requested_at)
        metadata = {
            "category": definition.category if definition is not None else "unknown",
            "mutability": definition.mutability if definition is not None else "unknown",
            "content_returned": bool(definition and definition.content_returned and result),
            "automatic_dispatch": False,
            "backend_accessed": False,
            "task_linked": linked_task_id is not None,
            "context_linked": linked_context_id is not None,
        }
        invocation = ToolInvocation(
            invocation_id=invocation_id,
            tool_name=audit_tool_name,
            tool_version=definition.version if definition is not None else "0",
            task_id=linked_task_id,
            context_id=linked_context_id,
            status=status,
            requested_at=requested_at,
            finished_at=finished_at,
            workspace_ref=workspace_ref,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            bytes_returned=bytes_returned,
            item_count=item_count,
            error_code=error_code,
            error_message=error_message,
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        )
        with self.db.transaction() as connection:
            self.repo.create(connection, invocation)

        response: dict[str, Any] = {
            "ok": status == "succeeded",
            "schema": TOOL_RESULT_SCHEMA,
            "invocation_id": invocation_id,
            "status": status,
            "tool": definition.to_dict() if definition is not None else {
                "name": audit_tool_name,
                "version": "0",
                "category": "unknown",
                "mutability": "unknown",
                "automatic_dispatch": False,
            },
            "workspace_ref": workspace_ref,
            "task_id": linked_task_id,
            "context_id": linked_context_id,
            "audit": {
                "persisted": True,
                "raw_input_stored": False,
                "raw_output_stored": False,
                "cwd_stored": False,
                "automatic_dispatch": False,
            },
        }
        if result is not None and status == "succeeded":
            response["result"] = result
        else:
            response["error"] = {
                "code": error_code or "tool_execution_failed",
                "message": error_message or "tool invocation failed",
            }
        return response

    # --------------------------------------------------------------- history

    def history(
        self,
        *,
        tool_name: str = "",
        status: str = "",
        task_id: str = "",
        context_id: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        tool_value = str(tool_name or "").strip().lower()
        status_value = str(status or "").strip().lower()
        if tool_value and not _TOOL_NAME_RE.fullmatch(tool_value):
            raise ToolPolicyError("tool name contains unsupported characters")
        if status_value and status_value not in _ALLOWED_STATUSES:
            raise ToolPolicyError("unsupported invocation status")
        bounded_limit = self._bounded_int(
            limit,
            name="limit",
            minimum=1,
            maximum=MAX_HISTORY_LIMIT,
        )
        items = self.repo.list(
            tool_name=tool_value,
            status=status_value,
            task_id=str(task_id or "").strip(),
            context_id=str(context_id or "").strip(),
            limit=bounded_limit,
        )
        return {
            "ok": True,
            "schema": TOOL_HISTORY_SCHEMA,
            "invocations": [item.to_public_dict() for item in items],
            "raw_input_returned": False,
            "raw_output_returned": False,
        }

    def get_invocation(self, invocation_id: str) -> dict[str, Any]:
        identifier = str(invocation_id or "").strip()
        if not identifier:
            raise ToolPolicyError("invocation_id is required")
        item = self.repo.get(identifier)
        if item is None:
            raise ToolPolicyError("tool invocation not found")
        return {
            "ok": True,
            "schema": TOOL_HISTORY_SCHEMA,
            "invocation": item.to_public_dict(),
            "raw_input_returned": False,
            "raw_output_returned": False,
        }

    # ----------------------------------------------------------- filesystem

    def _filesystem_list(self, root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
        if set(arguments) - {"path", "max_entries"}:
            raise ToolPolicyError("filesystem.list received unsupported arguments")
        relpath = self._optional_relpath(arguments.get("path", ""))
        max_entries = self._bounded_int(
            arguments.get("max_entries", 100),
            name="max_entries",
            minimum=1,
            maximum=MAX_LIST_ENTRIES,
        )
        directory = self._resolved_candidate(root, relpath, require_exists=True)
        if not directory.is_dir():
            raise ToolPolicyError("requested path is not a directory")
        entries: list[dict[str, Any]] = []
        truncated = False
        try:
            children = heapq.nsmallest(
                max_entries + 1,
                directory.iterdir(),
                key=lambda item: item.name.casefold(),
            )
        except OSError as exc:
            raise ToolExecutionError("directory cannot be listed") from exc
        for child in children:
            if len(entries) >= max_entries:
                truncated = True
                break
            relative = child.relative_to(root).as_posix()
            is_symlink = child.is_symlink()
            external_symlink = False
            try:
                resolved = child.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                external_symlink = is_symlink
                resolved = None
            kind = "symlink"
            size: int | None = None
            if resolved is not None:
                if resolved.is_dir():
                    kind = "directory"
                elif resolved.is_file():
                    kind = "file"
                    try:
                        size = int(resolved.stat().st_size)
                    except OSError:
                        size = None
                else:
                    kind = "other"
            entries.append(
                {
                    "path": relative,
                    "kind": kind,
                    "size_bytes": size,
                    "is_symlink": is_symlink,
                    "external_symlink": external_symlink,
                }
            )
        return {
            "path": relpath,
            "entries": entries,
            "truncated": truncated,
            "content_returned": False,
            "_item_count": len(entries),
            "_bytes_returned": 0,
        }

    def _filesystem_stat(self, root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
        if set(arguments) - {"path"}:
            raise ToolPolicyError("filesystem.stat received unsupported arguments")
        relpath = self._required_relpath(arguments.get("path", ""))
        lexical = self._lexical_candidate(root, relpath)
        is_symlink = lexical.is_symlink()
        candidate = self._resolved_candidate(root, relpath, require_exists=True)
        try:
            stat = candidate.stat()
        except OSError as exc:
            raise ToolExecutionError("requested path cannot be inspected") from exc
        kind = "directory" if candidate.is_dir() else "file" if candidate.is_file() else "other"
        result: dict[str, Any] = {
            "path": relpath,
            "kind": kind,
            "size_bytes": int(stat.st_size) if kind == "file" else None,
            "is_symlink": is_symlink,
            "content_returned": False,
            "_item_count": 1,
            "_bytes_returned": 0,
        }
        return result

    def _filesystem_sha256(
        self, root: Path, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if set(arguments) - {"path", "max_bytes"}:
            raise ToolPolicyError("filesystem.sha256 received unsupported arguments")
        relpath = self._required_relpath(arguments.get("path", ""))
        max_bytes = self._bounded_int(
            arguments.get("max_bytes", 8 * 1024 * 1024),
            name="max_bytes",
            minimum=1,
            maximum=MAX_FILE_HASH_BYTES,
        )
        candidate = self._resolved_candidate(root, relpath, require_exists=True)
        if not candidate.is_file():
            raise ToolPolicyError("requested path is not a file")
        try:
            size = int(candidate.stat().st_size)
        except OSError as exc:
            raise ToolExecutionError("requested file cannot be inspected") from exc
        if size > max_bytes:
            raise ToolPolicyError("file exceeds explicit hash byte limit")
        return {
            "path": relpath,
            "sha256": self._file_sha256(candidate),
            "size_bytes": size,
            "content_returned": False,
            "_item_count": 1,
            "_bytes_returned": 0,
        }

    def _filesystem_read_text(
        self, root: Path, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if set(arguments) - {"path", "max_bytes"}:
            raise ToolPolicyError("filesystem.read_text received unsupported arguments")
        relpath = self._required_relpath(arguments.get("path", ""))
        max_bytes = self._bounded_int(
            arguments.get("max_bytes", 256 * 1024),
            name="max_bytes",
            minimum=1,
            maximum=MAX_FILE_READ_BYTES,
        )
        candidate = self._resolved_candidate(root, relpath, require_exists=True)
        if not candidate.is_file():
            raise ToolPolicyError("requested path is not a file")
        try:
            with candidate.open("rb") as handle:
                data = handle.read(max_bytes + 1)
        except OSError as exc:
            raise ToolExecutionError("requested file cannot be read") from exc
        if len(data) > max_bytes:
            raise ToolPolicyError("file exceeds explicit byte limit")
        if b"\x00" in data:
            raise ToolPolicyError("binary files are not supported")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolPolicyError("file is not UTF-8 text") from exc
        return {
            "path": relpath,
            "content": text,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "content_returned": True,
            "_item_count": 1,
            "_bytes_returned": len(data),
        }

    # ------------------------------------------------------------------- git

    def _git_status(self, root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments:
            unsupported = set(arguments) - {"max_entries"}
            if unsupported:
                raise ToolPolicyError("git.status received unsupported arguments")
        max_entries = self._bounded_int(
            arguments.get("max_entries", 200),
            name="max_entries",
            minimum=1,
            maximum=MAX_LIST_ENTRIES,
        )
        raw = self._run_git(
            root,
            ["status", "--porcelain=v1", "--untracked-files=normal"],
            max_bytes=MAX_GIT_STATUS_BYTES,
        )
        entries: list[dict[str, str]] = []
        truncated = False
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if len(entries) >= max_entries:
                truncated = True
                break
            if len(line) < 3:
                continue
            entries.append({"status": line[:2], "path": line[3:]})
        return {
            "clean": not entries and not truncated,
            "entries": entries,
            "truncated": truncated,
            "content_returned": False,
            "_item_count": len(entries),
            "_bytes_returned": 0,
        }

    def _git_diff(self, root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
        relpath = self._optional_relpath(arguments.get("path", ""))
        cached = self._boolean(arguments.get("cached", False), name="cached")
        max_bytes = self._bounded_int(
            arguments.get("max_bytes", 512 * 1024),
            name="max_bytes",
            minimum=1,
            maximum=MAX_GIT_DIFF_BYTES,
        )
        unsupported = set(arguments) - {"path", "cached", "max_bytes"}
        if unsupported:
            raise ToolPolicyError("git.diff received unsupported arguments")
        args = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=all",
            "--no-color",
        ]
        if cached:
            args.append("--cached")
        if relpath:
            args.extend(["--", relpath])
        raw = self._run_git(root, args, max_bytes=max_bytes)
        text = raw.decode("utf-8", errors="replace")
        return {
            "path": relpath,
            "cached": cached,
            "content": text,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            "content_returned": True,
            "_item_count": 0 if not raw else text.count("diff --git ") or 1,
            "_bytes_returned": len(raw),
        }

    # ---------------------------------------------------------------- sqlite

    def _sqlite_query(self, root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
        relpath = self._required_relpath(arguments.get("database", ""))
        raw_query = arguments.get("query", "")
        if not isinstance(raw_query, str):
            raise ToolPolicyError("query must be a string")
        query = raw_query.strip()
        if not query:
            raise ToolPolicyError("query is required")
        if len(query) > MAX_SQL_QUERY_CHARS:
            raise ToolPolicyError("query exceeds the character limit")
        max_rows = self._bounded_int(
            arguments.get("max_rows", 100),
            name="max_rows",
            minimum=1,
            maximum=MAX_SQL_ROWS,
        )
        max_result_bytes = self._bounded_int(
            arguments.get("max_result_bytes", 256 * 1024),
            name="max_result_bytes",
            minimum=1,
            maximum=MAX_SQL_RESULT_BYTES,
        )
        unsupported = set(arguments) - {
            "database", "query", "parameters", "max_rows", "max_result_bytes"
        }
        if unsupported:
            raise ToolPolicyError("sqlite.query received unsupported arguments")
        parameters = arguments.get("parameters", [])
        if not isinstance(parameters, (list, tuple, dict)):
            raise ToolPolicyError("parameters must be an array or object")
        self._validate_sql_parameters(parameters)
        self._validate_query_shape(query)
        candidate = self._resolved_candidate(root, relpath, require_exists=True)
        if not candidate.is_file():
            raise ToolPolicyError("database path is not a file")
        uri_path = quote(candidate.as_posix(), safe="/:")
        uri = f"file:{uri_path}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        except sqlite3.Error as exc:
            raise ToolExecutionError("SQLite database cannot be opened read-only") from exc
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            if hasattr(connection, "setlimit"):
                connection.setlimit(
                    sqlite3.SQLITE_LIMIT_LENGTH,
                    min(MAX_SQL_RESULT_BYTES + 64 * 1024, max_result_bytes + 64 * 1024),
                )
                connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_QUERY_CHARS)
                connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 200)
                connection.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 50)
            connection.set_authorizer(self._sqlite_authorizer)
            deadline = time.monotonic() + SQLITE_QUERY_TIMEOUT_SECONDS
            connection.set_progress_handler(
                lambda: 1 if time.monotonic() >= deadline else 0,
                1000,
            )
            cursor = connection.execute(query, parameters)
            columns = [str(item[0]) for item in cursor.description or []]
            rows: list[list[Any]] = []
            result_bytes = 0
            truncated = False
            for row in cursor.fetchmany(max_rows + 1):
                if len(rows) >= max_rows:
                    truncated = True
                    break
                projected = [self._sqlite_value(value) for value in row]
                encoded = json.dumps(
                    projected,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                if result_bytes + len(encoded) > max_result_bytes:
                    truncated = True
                    break
                rows.append(projected)
                result_bytes += len(encoded)
        except sqlite3.Error as exc:
            message = str(exc).lower()
            if "not authorized" in message:
                raise ToolPolicyError("SQLite query requests a write operation") from exc
            if "interrupted" in message:
                raise ToolExecutionError("SQLite read-only query timed out") from exc
            raise ToolExecutionError("SQLite read-only query failed") from exc
        finally:
            connection.close()
        return {
            "database": relpath,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "read_only": True,
            "content_returned": True,
            "_item_count": len(rows),
            "_bytes_returned": result_bytes,
        }

    # --------------------------------------------------------------- helpers

    def _validate_task_reference(self, task_id: str) -> str | None:
        identifier = str(task_id or "").strip()
        if not identifier:
            return None
        if self.tasks.get_by_id(identifier) is None:
            raise ToolReferenceError("task_id does not reference a durable task")
        return identifier

    def _validate_context_reference(self, context_id: str) -> str | None:
        identifier = str(context_id or "").strip()
        if not identifier:
            return None
        if self.contexts.get_manifest(identifier) is None:
            raise ToolReferenceError("context_id does not reference a context manifest")
        return identifier

    @staticmethod
    def _root(cwd: str) -> Path:
        if not isinstance(cwd, str):
            raise ToolPolicyError("cwd must be a string")
        raw = cwd.strip()
        if not raw:
            raise ToolPolicyError("cwd is required")
        try:
            root = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ToolPolicyError("cwd must be an existing directory") from exc
        if not root.is_dir():
            raise ToolPolicyError("cwd must be an existing directory")
        return root

    @staticmethod
    def _workspace_ref(root: Path) -> str:
        normalized = os.path.normcase(str(root))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _workspace_ref_raw(cwd: str) -> str:
        value = os.path.normcase(str(cwd or "").strip())
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_relpath(raw: Any, *, allow_empty: bool) -> str:
        if not isinstance(raw, str):
            raise ToolPolicyError("tool paths must be strings")
        value = raw.strip().replace("\\", "/")
        if not value and allow_empty:
            return ""
        drive, _ = ntpath.splitdrive(value)
        if not value or drive or value.startswith("/"):
            raise ToolPolicyError("tool paths must be workspace-relative")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ToolPolicyError("tool path contains unsafe segments")
        normalized = PurePosixPath(value).as_posix()
        if len(normalized) > 512:
            raise ToolPolicyError("tool path is too long")
        return normalized

    @classmethod
    def _required_relpath(cls, raw: Any) -> str:
        return cls._normalize_relpath(raw, allow_empty=False)

    @classmethod
    def _optional_relpath(cls, raw: Any) -> str:
        return cls._normalize_relpath(raw, allow_empty=True)

    @staticmethod
    def _lexical_candidate(root: Path, relpath: str) -> Path:
        if not relpath:
            return root
        return root.joinpath(*PurePosixPath(relpath).parts)

    @classmethod
    def _resolved_candidate(
        cls, root: Path, relpath: str, *, require_exists: bool
    ) -> Path:
        lexical = cls._lexical_candidate(root, relpath)
        try:
            resolved = lexical.resolve(strict=require_exists)
        except (OSError, RuntimeError) as exc:
            raise ToolPolicyError("requested path does not exist") from exc
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ToolPolicyError("requested path resolves outside cwd") from exc
        return resolved

    @staticmethod
    def _boolean(value: Any, *, name: str) -> bool:
        if not isinstance(value, bool):
            raise ToolPolicyError(f"{name} must be a boolean")
        return value

    @staticmethod
    def _bounded_int(value: Any, *, name: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolPolicyError(f"{name} must be an integer")
        number = value
        if number < minimum or number > maximum:
            raise ToolPolicyError(f"{name} must be between {minimum} and {maximum}")
        return number

    @staticmethod
    def _canonical_hash(value: Any) -> str:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        except (TypeError, ValueError):
            encoded = repr(value).encode("utf-8", errors="replace")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ToolExecutionError("requested file cannot be hashed") from exc
        return digest.hexdigest()

    @staticmethod
    def _safe_operational_message(exc: BaseException) -> str:
        if isinstance(exc, subprocess.TimeoutExpired):
            return "tool process timed out"
        return "tool operation failed"

    def _run_git(self, root: Path, args: list[str], *, max_bytes: int) -> bytes:
        try:
            environment = os.environ.copy()
            environment.update(
                {
                    "GIT_OPTIONAL_LOCKS": "0",
                    "GIT_TERMINAL_PROMPT": "0",
                }
            )
            process = subprocess.run(
                [
                    "git",
                    "--no-pager",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.untrackedCache=false",
                    *args,
                ],
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=DEFAULT_PROCESS_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ToolExecutionError("git executable is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError("git command timed out") from exc
        if process.returncode != 0:
            raise ToolExecutionError("workspace is not a readable Git repository")
        if len(process.stdout) > max_bytes:
            raise ToolPolicyError("Git output exceeds explicit byte limit")
        return bytes(process.stdout)

    @staticmethod
    def _validate_query_shape(query: str) -> None:
        stripped = query.strip()
        if "\x00" in stripped:
            raise ToolPolicyError("query contains unsupported characters")
        # sqlite3.execute itself rejects multiple statements.  This prefix gate
        # makes the caller intent explicit; the database is also opened with
        # mode=ro and PRAGMA query_only=ON, so WITH ... DELETE cannot mutate it.
        token = stripped.split(None, 1)[0].upper() if stripped else ""
        if token not in {"SELECT", "WITH", "PRAGMA", "EXPLAIN"}:
            raise ToolPolicyError("only read-only SQLite queries are supported")
        if token == "PRAGMA":
            body = stripped[len("PRAGMA"):].strip()
            if "=" in body:
                raise ToolPolicyError("SQLite PRAGMA assignments are not supported")
            name = re.split(r"[.(\s]", body, maxsplit=1)[0].lower()
            if name not in _ALLOWED_SQLITE_PRAGMAS:
                raise ToolPolicyError("SQLite PRAGMA is not in the read-only allowlist")

    @staticmethod
    def _validate_sql_parameters(parameters: list[Any] | tuple[Any, ...] | dict[Any, Any]) -> None:
        values: list[Any]
        if isinstance(parameters, dict):
            if len(parameters) > 100 or not all(isinstance(key, str) for key in parameters):
                raise ToolPolicyError("SQLite parameters exceed the supported shape")
            values = list(parameters.values())
        else:
            if len(parameters) > 100:
                raise ToolPolicyError("SQLite parameter limit is 100")
            values = list(parameters)
        for value in values:
            if not isinstance(value, (type(None), bool, int, float, str, bytes)):
                raise ToolPolicyError("SQLite parameters must be scalar values")
            if isinstance(value, (str, bytes)) and len(value) > 64 * 1024:
                raise ToolPolicyError("SQLite parameter exceeds the byte limit")

    @staticmethod
    def _sqlite_authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        denied = {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_CREATE_TEMP_INDEX,
            sqlite3.SQLITE_CREATE_TEMP_TABLE,
            sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
            sqlite3.SQLITE_CREATE_TEMP_VIEW,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_CREATE_VIEW,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_DROP_TEMP_INDEX,
            sqlite3.SQLITE_DROP_TEMP_TABLE,
            sqlite3.SQLITE_DROP_TEMP_TRIGGER,
            sqlite3.SQLITE_DROP_TEMP_VIEW,
            sqlite3.SQLITE_DROP_TRIGGER,
            sqlite3.SQLITE_DROP_VIEW,
            sqlite3.SQLITE_ALTER_TABLE,
            sqlite3.SQLITE_REINDEX,
            sqlite3.SQLITE_ANALYZE,
            sqlite3.SQLITE_ATTACH,
            sqlite3.SQLITE_DETACH,
        }
        if action in denied:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            pragma_name = str(arg1 or "").strip().lower()
            if pragma_name not in _ALLOWED_SQLITE_PRAGMAS:
                return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            table_name = str(arg1 or "").strip().lower()
            if table_name.startswith("pragma_"):
                pragma_name = table_name[len("pragma_"):]
                if pragma_name not in _ALLOWED_SQLITE_PRAGMAS:
                    return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = str(arg2 or arg1 or "").strip().lower()
            if function_name in {
                "load_extension",
                "readfile",
                "writefile",
                "pragma_database_list",
            }:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @staticmethod
    def _sqlite_value(value: Any) -> Any:
        if value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, bytes):
            return {
                "type": "blob",
                "size_bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
        text = str(value)
        if len(text) > MAX_SQL_CELL_CHARS:
            return {
                "type": "text",
                "truncated": True,
                "length": len(text),
                "value": text[:MAX_SQL_CELL_CHARS],
            }
        return text

    def _db_now(self) -> float:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT (julianday('now') - 2440587.5) * 86400.0"
            ).fetchone()
        return float(row[0])
