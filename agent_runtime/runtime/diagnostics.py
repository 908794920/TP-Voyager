"""Read-only diagnostics and deterministic task-report export.

This module is operational tooling around the durable Runtime database.  It
never dispatches, resumes, cancels, retries, migrates, or mutates a task.  The
safe default view mirrors the public MCP boundary: no prompt, backend session
id, command text, event payload, local blob path, or final answer is exposed.
Final material is available only through an explicit ``include_result`` flag.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from agent_runtime.domain.structured_result import (
    StructuredResultParseError,
    parse_structured_result,
)
from agent_runtime.persistence.migrations import SCHEMA_VERSION
from agent_runtime.application.outcome_service import assess_task_result


class RuntimeDiagnosticsError(RuntimeError):
    """The Runtime database cannot be safely inspected."""


def _readonly_uri(path: Path) -> str:
    # SQLite URI paths use forward slashes on every platform.  Preserve the
    # drive colon on Windows while quoting spaces, #, ? and non-ASCII text.
    value = path.expanduser().resolve().as_posix()
    return f"file:{quote(value, safe='/:')}?mode=ro"


def _iso8601(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


@dataclass(frozen=True)
class RuntimeOverview:
    database: str
    schema_version: int
    supported_schema_version: int
    task_count: int
    result_count: int
    artifact_count: int
    evidence_count: int
    event_count: int
    tool_invocation_count: int
    knowledge_collection_count: int
    knowledge_resolution_count: int
    plan_execution_count: int
    plan_result_count: int
    plan_execution_status_counts: dict[str, int]
    status_counts: dict[str, int]
    runtime_counts: dict[str, int]
    quick_check: str
    foreign_key_violation_count: int

    @property
    def schema_supported(self) -> bool:
        return self.schema_version == self.supported_schema_version

    @property
    def integrity_ok(self) -> bool:
        return self.quick_check == "ok" and self.foreign_key_violation_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "schema_version": self.schema_version,
            "supported_schema_version": self.supported_schema_version,
            "schema_supported": self.schema_supported,
            "task_count": self.task_count,
            "result_count": self.result_count,
            "artifact_count": self.artifact_count,
            "evidence_count": self.evidence_count,
            "event_count": self.event_count,
            "tool_invocation_count": self.tool_invocation_count,
            "knowledge_collection_count": self.knowledge_collection_count,
            "knowledge_resolution_count": self.knowledge_resolution_count,
            "plan_execution_count": self.plan_execution_count,
            "plan_result_count": self.plan_result_count,
            "plan_execution_status_counts": dict(self.plan_execution_status_counts),
            "status_counts": dict(self.status_counts),
            "runtime_counts": dict(self.runtime_counts),
            "quick_check": self.quick_check,
            "foreign_key_violation_count": self.foreign_key_violation_count,
            "integrity_ok": self.integrity_ok,
        }


@dataclass(frozen=True)
class ArtifactStoreAudit:
    captured_reference_count: int
    valid_reference_count: int
    missing_blob_count: int
    unsafe_storage_key_count: int
    size_mismatch_count: int
    hash_mismatch_count: int
    orphan_blob_count: int
    issues: tuple[dict[str, str], ...]

    @property
    def integrity_ok(self) -> bool:
        return not any((
            self.missing_blob_count,
            self.unsafe_storage_key_count,
            self.size_mismatch_count,
            self.hash_mismatch_count,
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "integrity_ok": self.integrity_ok,
            "captured_reference_count": self.captured_reference_count,
            "valid_reference_count": self.valid_reference_count,
            "missing_blob_count": self.missing_blob_count,
            "unsafe_storage_key_count": self.unsafe_storage_key_count,
            "size_mismatch_count": self.size_mismatch_count,
            "hash_mismatch_count": self.hash_mismatch_count,
            "orphan_blob_count": self.orphan_blob_count,
            "issues": [dict(item) for item in self.issues],
        }


_STORAGE_KEY_RE = re.compile(r"^sha256/([0-9a-f]{2})/([0-9a-f]{64})$")


class RuntimeInspector:
    """Read-only projection over one existing Runtime SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path).expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise RuntimeDiagnosticsError(
                f"Runtime database does not exist: {self.path}"
            )
        try:
            connection = sqlite3.connect(_readonly_uri(self.path), uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            return connection
        except sqlite3.Error as exc:
            raise RuntimeDiagnosticsError(
                f"Cannot open Runtime database read-only: {exc}"
            ) from exc

    def _rows(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(sql, parameters).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeDiagnosticsError(f"Runtime diagnostic query failed: {exc}") from exc
        finally:
            connection.close()

    def _row(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        rows = self._rows(sql, parameters)
        return rows[0] if rows else None

    def _table_exists(self, name: str) -> bool:
        return self._row(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (name,),
        ) is not None

    @staticmethod
    def _bounded_limit(value: int, *, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("limit must be an integer")
        if value <= 0 or value > maximum:
            raise ValueError(f"limit must be between 1 and {maximum}")
        return value

    def overview(self) -> RuntimeOverview:
        connection = self._connect()
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            known_tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            counts = {
                table: (
                    int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                    )
                    if table in known_tables
                    else 0
                )
                for table in (
                    "tasks",
                    "artifacts",
                    "evidences",
                    "events",
                    "tool_invocations",
                    "knowledge_collections",
                    "knowledge_resolutions",
                    "plan_executions",
                    "plan_results",
                )
            }
            result_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE result_available = 1"
                ).fetchone()[0]
            )
            status_counts = {
                str(row["status"]): int(row["count"])
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status ORDER BY status"
                ).fetchall()
            }
            runtime_counts = {
                str(row["task_type"]): int(row["count"])
                for row in connection.execute(
                    "SELECT task_type, COUNT(*) AS count "
                    "FROM tasks GROUP BY task_type ORDER BY task_type"
                ).fetchall()
            }
            plan_execution_status_counts = (
                {
                    str(row["status"]): int(row["count"])
                    for row in connection.execute(
                        "SELECT status, COUNT(*) AS count "
                        "FROM plan_executions GROUP BY status ORDER BY status"
                    ).fetchall()
                }
                if "plan_executions" in known_tables
                else {}
            )
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            foreign_key_violation_count = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        except sqlite3.Error as exc:
            raise RuntimeDiagnosticsError(f"Runtime diagnostic query failed: {exc}") from exc
        finally:
            connection.close()
        return RuntimeOverview(
            database=str(self.path),
            schema_version=version,
            supported_schema_version=SCHEMA_VERSION,
            task_count=counts["tasks"],
            result_count=result_count,
            artifact_count=counts["artifacts"],
            evidence_count=counts["evidences"],
            event_count=counts["events"],
            tool_invocation_count=counts["tool_invocations"],
            knowledge_collection_count=counts["knowledge_collections"],
            knowledge_resolution_count=counts["knowledge_resolutions"],
            plan_execution_count=counts["plan_executions"],
            plan_result_count=counts["plan_results"],
            plan_execution_status_counts=plan_execution_status_counts,
            status_counts=status_counts,
            runtime_counts=runtime_counts,
            quick_check=quick_check,
            foreign_key_violation_count=foreign_key_violation_count,
        )

    def audit_artifact_store(self, *, issue_limit: int = 20) -> ArtifactStoreAudit:
        """Verify captured Artifact references without deleting or rewriting blobs.

        Unreferenced content-addressed blobs are reported as offline-GC
        candidates but do not make the reference integrity check fail.
        """
        if issue_limit < 0 or issue_limit > 1000:
            raise ValueError("issue_limit must be between 0 and 1000")
        rows = self._rows(
            """
            SELECT artifact_id, storage_key, sha256, size_bytes
            FROM artifacts
            WHERE capture_state = 'captured'
            ORDER BY artifact_id
            """
        )
        store_root = (self.path.parent / "artifacts").resolve()
        referenced_keys: set[str] = set()
        issues: list[dict[str, str]] = []
        missing = unsafe = size_mismatch = hash_mismatch = valid = 0

        def add_issue(artifact_id: str, kind: str) -> None:
            if len(issues) < issue_limit:
                issues.append({"artifact_id": artifact_id, "issue": kind})

        for row in rows:
            artifact_id = str(row["artifact_id"])
            storage_key = str(row["storage_key"] or "")
            digest = str(row["sha256"] or "").lower()
            match = _STORAGE_KEY_RE.fullmatch(storage_key)
            if (
                match is None
                or match.group(1) != digest[:2]
                or match.group(2) != digest
            ):
                unsafe += 1
                add_issue(artifact_id, "unsafe_or_inconsistent_storage_key")
                continue
            referenced_keys.add(storage_key)
            candidate = (store_root / Path(*storage_key.split("/"))).resolve()
            try:
                candidate.relative_to(store_root)
            except ValueError:
                unsafe += 1
                add_issue(artifact_id, "storage_key_escapes_store")
                continue
            if not candidate.is_file():
                missing += 1
                add_issue(artifact_id, "missing_blob")
                continue
            expected_size = row["size_bytes"]
            if expected_size is not None and candidate.stat().st_size != int(expected_size):
                size_mismatch += 1
                add_issue(artifact_id, "size_mismatch")
                continue
            hasher = hashlib.sha256()
            with candidate.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            if hasher.hexdigest() != digest:
                hash_mismatch += 1
                add_issue(artifact_id, "hash_mismatch")
                continue
            valid += 1

        orphan_count = 0
        sha_root = store_root / "sha256"
        if sha_root.is_dir():
            for blob in sha_root.glob("[0-9a-f][0-9a-f]/[0-9a-f]*"):
                if not blob.is_file() or not re.fullmatch(r"[0-9a-f]{64}", blob.name):
                    continue
                key = f"sha256/{blob.parent.name}/{blob.name}"
                if key not in referenced_keys:
                    orphan_count += 1
        return ArtifactStoreAudit(
            captured_reference_count=len(rows),
            valid_reference_count=valid,
            missing_blob_count=missing,
            unsafe_storage_key_count=unsafe,
            size_mismatch_count=size_mismatch,
            hash_mismatch_count=hash_mismatch,
            orphan_blob_count=orphan_count,
            issues=tuple(issues),
        )

    def list_tasks(
        self,
        *,
        runtime: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses: list[str] = []
        parameters: list[Any] = []
        if runtime.strip():
            clauses.append("t.task_type = ?")
            parameters.append(runtime.strip().lower())
        if status.strip():
            clauses.append("t.status = ?")
            parameters.append(status.strip().lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        rows = self._rows(
            f"""
            SELECT t.task_id, t.task_type, t.status, t.route, t.created_at,
                   t.updated_at, t.finished_at, t.current_attempt_id,
                   t.result_available, t.terminal_reason, t.error_code,
                   l.parent_task_id, l.root_task_id, l.context_id,
                   l.agent_profile, l.execution_mode
            FROM tasks t
            LEFT JOIN task_lineage l ON l.child_task_id = t.task_id
            {where}
            ORDER BY t.created_at DESC, t.task_id DESC
            LIMIT ?
            """,
            tuple(parameters),
        )
        return [self._safe_task_row(row) for row in rows]

    def list_knowledge_collections(self, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded = self._bounded_limit(limit, maximum=200)
        if not self._table_exists("knowledge_collections"):
            return []
        rows = self._rows(
            """
            SELECT knowledge_id, name, context_id, root_hash, source_count,
                   total_bytes, created_at
            FROM knowledge_collections
            ORDER BY created_at DESC, knowledge_id DESC
            LIMIT ?
            """,
            (bounded,),
        )
        return [
            {
                "knowledge_id": str(row["knowledge_id"]),
                "name": str(row["name"]),
                "context_id": str(row["context_id"]),
                "root_hash": str(row["root_hash"]),
                "source_count": int(row["source_count"]),
                "total_bytes": int(row["total_bytes"]),
                "created_at": _iso8601(row["created_at"]),
                "content_stored": False,
                "cwd_stored": False,
            }
            for row in rows
        ]

    def list_knowledge_resolutions(
        self,
        *,
        knowledge_id: str = "",
        operation: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        bounded = self._bounded_limit(limit, maximum=200)
        if not self._table_exists("knowledge_resolutions"):
            return []
        clauses: list[str] = []
        parameters: list[Any] = []
        if knowledge_id:
            clauses.append("knowledge_id = ?")
            parameters.append(str(knowledge_id))
        if operation:
            clauses.append("operation = ?")
            parameters.append(str(operation))
        if status:
            clauses.append("status = ?")
            parameters.append(str(status))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(bounded)
        rows = self._rows(
            f"""
            SELECT resolution_id, knowledge_id, task_id, operation, status,
                   requested_at, finished_at, query_sha256, output_sha256,
                   source_count, citation_count, bytes_returned, error_code,
                   error_message
            FROM knowledge_resolutions
            {where}
            ORDER BY requested_at DESC, resolution_id DESC
            LIMIT ?
            """,
            tuple(parameters),
        )
        return [
            {
                "resolution_id": str(row["resolution_id"]),
                "knowledge_id": str(row["knowledge_id"]),
                "task_id": str(row["task_id"]) if row["task_id"] is not None else None,
                "operation": str(row["operation"]),
                "status": str(row["status"]),
                "requested_at": _iso8601(row["requested_at"]),
                "finished_at": _iso8601(row["finished_at"]),
                "query_sha256": str(row["query_sha256"]),
                "output_sha256": str(row["output_sha256"]) if row["output_sha256"] is not None else None,
                "source_count": int(row["source_count"]),
                "citation_count": int(row["citation_count"]),
                "bytes_returned": int(row["bytes_returned"]),
                "error_code": str(row["error_code"]) if row["error_code"] else None,
                "error_message": str(row["error_message"]) if row["error_message"] else None,
                "raw_query_stored": False,
                "raw_output_stored": False,
            }
            for row in rows
        ]

    def list_tool_invocations(
        self,
        *,
        tool_name: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Read content-free V1.4 Tool Runtime audit records."""
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses: list[str] = []
        parameters: list[Any] = []
        if tool_name.strip():
            clauses.append("tool_name = ?")
            parameters.append(tool_name.strip().lower())
        if status.strip():
            clauses.append("status = ?")
            parameters.append(status.strip().lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        rows = self._rows(
            f"""
            SELECT invocation_id, tool_name, tool_version, task_id, context_id,
                   status, requested_at, finished_at, workspace_ref,
                   input_sha256, output_sha256, bytes_returned, item_count,
                   error_code, error_message, metadata_json
            FROM tool_invocations
            {where}
            ORDER BY requested_at DESC, invocation_id DESC
            LIMIT ?
            """,
            tuple(parameters),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except json.JSONDecodeError:
                metadata = {}
            result.append({
                "invocation_id": str(row["invocation_id"]),
                "tool_name": str(row["tool_name"]),
                "tool_version": str(row["tool_version"]),
                "task_id": str(row["task_id"]) if row["task_id"] else None,
                "context_id": str(row["context_id"]) if row["context_id"] else None,
                "status": str(row["status"]),
                "requested_at": float(row["requested_at"]),
                "finished_at": float(row["finished_at"]),
                "workspace_ref": str(row["workspace_ref"]),
                "input_sha256": str(row["input_sha256"]),
                "output_sha256": str(row["output_sha256"]) if row["output_sha256"] else None,
                "bytes_returned": int(row["bytes_returned"]),
                "item_count": int(row["item_count"]),
                "error_code": str(row["error_code"]) if row["error_code"] else None,
                "error_message": str(row["error_message"]) if row["error_message"] else None,
                "metadata": metadata if isinstance(metadata, dict) else {},
                "raw_input_returned": False,
                "raw_output_returned": False,
                "cwd_returned": False,
            })
        return result

    def task_assessment(self, task_id: str) -> dict[str, Any]:
        """Return a content-free execution/work-product assessment."""
        canonical = task_id.strip()
        if not canonical:
            raise ValueError("task_id must not be empty")
        row = self._row(
            """
            SELECT task_id, status, terminal_reason, timeout_reason,
                   result_available, result_json
            FROM tasks WHERE task_id = ?
            """,
            (canonical,),
        )
        if row is None:
            raise RuntimeDiagnosticsError(f"Unknown task_id: {canonical}")
        return assess_task_result(
            task_id=row["task_id"],
            execution_status=row["status"],
            terminal_reason=row["terminal_reason"],
            timeout_reason=row["timeout_reason"],
            result_available=bool(row["result_available"]),
            result_json=row["result_json"],
        )

    def task_snapshot(
        self,
        task_id: str,
        *,
        include_result: bool = False,
    ) -> dict[str, Any]:
        canonical = task_id.strip()
        if not canonical:
            raise ValueError("task_id must not be empty")
        row = self._row(
            """
            SELECT t.task_id, t.task_type, t.status, t.route, t.created_at,
                   t.updated_at, t.started_at, t.finished_at,
                   t.cancel_requested_at, t.cancel_confirmed_at,
                   t.current_attempt_id, t.result_available, t.result_json,
                   t.terminal_reason, t.timeout_reason, t.error_code,
                   t.lost_at, t.orphaned_at,
                   l.parent_task_id, l.root_task_id, l.context_id,
                   l.agent_profile, l.execution_mode
            FROM tasks t
            LEFT JOIN task_lineage l ON l.child_task_id = t.task_id
            WHERE t.task_id = ?
            """,
            (canonical,),
        )
        if row is None:
            raise RuntimeDiagnosticsError(f"Unknown task_id: {canonical}")

        task = self._safe_task_row(row)
        task.update(
            {
                "started_at": _iso8601(row["started_at"]),
                "cancel_requested_at": _iso8601(row["cancel_requested_at"]),
                "cancel_confirmed_at": _iso8601(row["cancel_confirmed_at"]),
                "timeout_reason": row["timeout_reason"],
                "lost_at": _iso8601(row["lost_at"]),
                "orphaned_at": _iso8601(row["orphaned_at"]),
            }
        )
        attempts = [
            {
                "attempt_id": item["attempt_id"],
                "attempt_no": int(item["attempt_no"]),
                "backend": item["backend"],
                "route": item["route"],
                "status": item["status"],
                "created_at": _iso8601(item["created_at"]),
                "started_at": _iso8601(item["started_at"]),
                "finished_at": _iso8601(item["finished_at"]),
                "error_code": item["error_code"],
            }
            for item in self._rows(
                """
                SELECT attempt_id, attempt_no, backend, route, status,
                       created_at, started_at, finished_at, error_code
                FROM attempts WHERE task_id = ? ORDER BY attempt_no
                """,
                (canonical,),
            )
        ]
        events = [
            {
                "seq": int(item["seq"]),
                "event_type": item["event_type"],
                "event_time": _iso8601(item["event_time"]),
                "attempt_id": item["attempt_id"],
            }
            for item in self._rows(
                """
                SELECT seq, event_type, event_time, attempt_id
                FROM events
                WHERE task_id = ? AND visibility = 'public'
                ORDER BY seq
                """,
                (canonical,),
            )
        ]
        artifacts = [
            {
                "artifact_id": item["artifact_id"],
                "attempt_id": item["attempt_id"],
                "kind": item["kind"],
                "name": item["name"],
                "capture_state": item["capture_state"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in self._rows(
                """
                SELECT artifact_id, attempt_id, kind, name, capture_state,
                       sha256, size_bytes
                FROM artifacts WHERE task_id = ? ORDER BY created_at, artifact_id
                """,
                (canonical,),
            )
        ]
        evidence = [
            {
                "evidence_id": item["evidence_id"],
                "attempt_id": item["attempt_id"],
                "type": item["evidence_type"],
                "trust_state": item["trust_state"],
                "origin": item["origin"],
                "summary": item["summary"],
                "captured_at": _iso8601(item["captured_at"]),
                "subject_evidence_id": item["subject_evidence_id"],
                "artifact_id": item["artifact_id"],
            }
            for item in self._rows(
                """
                SELECT evidence_id, attempt_id, evidence_type, trust_state,
                       origin, summary, captured_at, subject_evidence_id,
                       artifact_id
                FROM evidences WHERE task_id = ? ORDER BY created_at, evidence_id
                """,
                (canonical,),
            )
        ]
        result = self._result_projection(
            row["result_json"], include_result=include_result
        )
        return {
            "task": task,
            "attempts": attempts,
            "events": events,
            "artifacts": artifacts,
            "evidence": evidence,
            "result": result,
        }

    @staticmethod
    def _safe_task_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "runtime": row["task_type"],
            "status": row["status"],
            "route": row["route"],
            "created_at": _iso8601(row["created_at"]),
            "updated_at": _iso8601(row["updated_at"]),
            "finished_at": _iso8601(row["finished_at"]),
            "current_attempt_id": row["current_attempt_id"],
            "result_available": bool(row["result_available"]),
            "terminal_reason": row["terminal_reason"],
            "error_code": row["error_code"],
            "parent_task_id": row["parent_task_id"],
            "root_task_id": row["root_task_id"] or row["task_id"],
            "context_id": row["context_id"],
            "agent_profile": row["agent_profile"],
            "execution_mode": row["execution_mode"] or "background",
        }

    @staticmethod
    def _safe_tests(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep engineering outcomes while recursively omitting command/output material."""
        blocked = {
            "command", "cmd", "argv", "stdout", "stderr", "output",
            "cwd", "working_directory", "environment", "env",
        }

        def sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: sanitize(item)
                    for key, item in value.items()
                    if key not in blocked
                }
            if isinstance(value, list):
                return [sanitize(item) for item in value]
            return value

        return [sanitize(item) for item in values]

    @staticmethod
    def _result_projection(
        result_json: str | None,
        *,
        include_result: bool,
    ) -> dict[str, Any] | None:
        if not result_json:
            return None
        try:
            parsed = parse_structured_result(result_json)
        except StructuredResultParseError as exc:
            return {"readable": False, "error": str(exc)}
        projection: dict[str, Any] = {
            "readable": True,
            "schema": parsed.schema or "legacy",
            "attempt_id": parsed.attempt_id,
            "backend": parsed.backend,
            "stop_reason": parsed.stop_reason,
            "verification": dict(parsed.verification),
            "usage": dict(parsed.usage),
            "changed_file_count": len(parsed.changed_files),
            "test_count": len(parsed.tests),
            "artifact_count": len(parsed.artifacts),
            "risk_count": len(parsed.risks),
            "claim_count": len(parsed.claims),
            "material_included": include_result,
        }
        if include_result:
            projection.update(
                {
                    "title": parsed.title,
                    "answer": parsed.answer,
                    "changed_files": list(parsed.changed_files),
                    "tests": RuntimeInspector._safe_tests(parsed.tests),
                    "artifacts": list(parsed.artifacts),
                    "risks": list(parsed.risks),
                    "claims": list(parsed.claims),
                }
            )
        return projection


def render_task_markdown(snapshot: dict[str, Any]) -> str:
    """Render a stable Markdown report from :meth:`task_snapshot`."""
    task = snapshot["task"]
    result = snapshot.get("result") or {}
    verification = result.get("verification") if isinstance(result, dict) else {}
    verification_status = (
        verification.get("status", "") if isinstance(verification, dict) else ""
    )
    lines = [
        f"# Sub-Agent Task Report: {task['task_id']}",
        "",
        "## Task",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    for label, key in (
        ("Runtime", "runtime"),
        ("Route", "route"),
        ("Status", "status"),
        ("Terminal reason", "terminal_reason"),
        ("Created", "created_at"),
        ("Finished", "finished_at"),
        ("Parent task", "parent_task_id"),
        ("Root task", "root_task_id"),
        ("Agent profile", "agent_profile"),
        ("Execution mode", "execution_mode"),
    ):
        value = task.get(key)
        lines.append(f"| {label} | {value if value not in (None, '') else '-'} |")

    lines.extend(["", "## Result summary", ""])
    if not result:
        lines.append("No persisted result is available.")
    elif result.get("readable") is False:
        lines.append(f"Persisted result is unreadable: {result.get('error', 'unknown error')}")
    else:
        lines.extend(
            [
                f"- Schema: `{result.get('schema', '-')}`",
                f"- Backend: `{result.get('backend', '-')}`",
                f"- Stop reason: `{result.get('stop_reason', '-')}`",
                f"- Verification: `{verification_status or '-'}`",
                f"- Changed files: {result.get('changed_file_count', 0)}",
                f"- Tests: {result.get('test_count', 0)}",
                f"- Artifacts declared in result: {result.get('artifact_count', 0)}",
                f"- Risks: {result.get('risk_count', 0)}",
            ]
        )
        if result.get("material_included"):
            lines.extend(["", "### Final answer", "", result.get("answer") or "(empty)"])

    lines.extend(["", "## Attempts", ""])
    attempts = snapshot.get("attempts", [])
    if attempts:
        lines.extend([
            "| # | Attempt | Backend | Route | Status |",
            "| ---: | --- | --- | --- | --- |",
        ])
        for item in attempts:
            lines.append(
                f"| {item['attempt_no']} | `{item['attempt_id']}` | "
                f"{item['backend']} | {item['route']} | {item['status']} |"
            )
    else:
        lines.append("No attempts recorded.")

    lines.extend(["", "## Artifacts", ""])
    artifacts = snapshot.get("artifacts", [])
    if artifacts:
        lines.extend([
            "| Name | Kind | State | SHA-256 | Size |",
            "| --- | --- | --- | --- | ---: |",
        ])
        for item in artifacts:
            lines.append(
                f"| {item['name']} | {item['kind']} | {item['capture_state']} | "
                f"`{item.get('sha256') or '-'}` | {item.get('size_bytes') or 0} |"
            )
    else:
        lines.append("No artifacts recorded.")

    lines.extend(["", "## Evidence", ""])
    evidence = snapshot.get("evidence", [])
    if evidence:
        lines.extend([
            "| Type | Trust | Origin | Summary |",
            "| --- | --- | --- | --- |",
        ])
        for item in evidence:
            summary = str(item.get("summary") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {item['type']} | {item['trust_state']} | {item['origin']} | {summary} |"
            )
    else:
        lines.append("No evidence recorded.")

    lines.extend(["", "## Public event timeline", ""])
    events = snapshot.get("events", [])
    if events:
        for item in events:
            lines.append(
                f"- {item['event_time'] or '-'} — `{item['event_type']}`"
                + (f" (`{item['attempt_id']}`)" if item.get("attempt_id") else "")
            )
    else:
        lines.append("No public events recorded.")

    lines.extend(
        [
            "",
            "> This report is generated from the durable SQLite source of truth. "
            "Private event payloads, backend session ids, command text, blob paths, "
            "and prompts are intentionally omitted.",
            "",
        ]
    )
    return "\n".join(lines)
