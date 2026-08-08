"""Explicit V1.5 Knowledge Runtime over caller-selected project documents.

The service builds on immutable Project Context manifests.  Registration stores
only paths, hashes, sizes, and classifications.  Search and bundle operations
read and verify current UTF-8 files only when explicitly invoked; raw queries,
file content, snippets, and workspace roots are never persisted.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from agent_runtime.domain.ids import (
    new_knowledge_id,
    new_knowledge_resolution_id,
)
from agent_runtime.domain.knowledge import (
    KNOWLEDGE_BUNDLE_SCHEMA,
    KNOWLEDGE_HISTORY_SCHEMA,
    KNOWLEDGE_SEARCH_SCHEMA,
    KNOWLEDGE_STATUS_SCHEMA,
    KnowledgeCollection,
    KnowledgeResolution,
    KnowledgeSource,
)
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.knowledge_repository import KnowledgeRepository
from agent_runtime.persistence.task_repository import TaskRepository
from agent_runtime.application.context_service import (
    MAX_CONTEXT_TOTAL_BYTES,
    ContextError,
    ProjectContextService,
)


MAX_KNOWLEDGE_SOURCES = 256
MAX_QUERY_CHARS = 512
MAX_QUERY_TERMS = 32
MAX_SEARCH_RESULTS = 50
DEFAULT_SEARCH_RESULTS = 10
MAX_SNIPPET_CHARS = 4_000
DEFAULT_SNIPPET_CHARS = 800
MAX_BUNDLE_BYTES = 512 * 1024
DEFAULT_BUNDLE_BYTES = 128 * 1024
MAX_HISTORY_LIMIT = 200
_KNOWLEDGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,120}$")
_KIND_VALUES = frozenset(
    {"overview", "rules", "architecture", "decision", "experience", "reference"}
)
_WORD_RE = re.compile(r"[A-Za-z0-9_./:-]+|[\u3400-\u9fff]+")


class KnowledgeError(ValueError):
    code = "knowledge_error"
    status = "failed"


class KnowledgePolicyError(KnowledgeError):
    code = "policy_rejected"
    status = "rejected"


class KnowledgeNotFoundError(KnowledgePolicyError):
    code = "knowledge_not_found"


class KnowledgeConflictError(KnowledgePolicyError):
    code = "knowledge_conflict"


class KnowledgeDriftError(KnowledgeError):
    code = "knowledge_drift"


@dataclass(frozen=True)
class KnowledgeRegistrationResult:
    collection: dict[str, Any]
    replayed: bool


class KnowledgeRuntimeService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = KnowledgeRepository(db)
        self.contexts = ProjectContextService(db)
        self.tasks = TaskRepository(db)

    # ------------------------------------------------------------- collection

    def register(
        self,
        cwd: str,
        files: Iterable[str],
        *,
        knowledge_id: str = "",
        name: str = "",
        source_kinds: dict[str, str] | None = None,
        allow_external_symlinks: bool = False,
    ) -> KnowledgeRegistrationResult:
        identifier = self._knowledge_id(knowledge_id)
        display_name = self._name(name or identifier)
        kind_map = self._kind_map(source_kinds)
        try:
            normalized_files = self.contexts._normalize_file_list(files)
        except ContextError as exc:
            raise KnowledgePolicyError(str(exc)) from exc
        unknown_kind_paths = sorted(set(kind_map) - set(normalized_files))
        if unknown_kind_paths:
            raise KnowledgePolicyError("source_kinds references an unregistered source")
        context_id = self._context_id(identifier)
        try:
            registered = self.contexts.register(
                cwd,
                normalized_files,
                context_id=context_id,
                allow_external_symlinks=allow_external_symlinks,
            )
        except ContextError as exc:
            raise KnowledgePolicyError(str(exc)) from exc
        manifest = registered.manifest
        entries = list(manifest["entries"])
        if len(entries) > MAX_KNOWLEDGE_SOURCES:
            raise KnowledgePolicyError("knowledge source count exceeds limit")
        sources = [
            KnowledgeSource(
                knowledge_id=identifier,
                context_id=context_id,
                relpath=str(item["relpath"]),
                sha256=str(item["sha256"]),
                size_bytes=int(item["size_bytes"]),
                kind=kind_map.get(
                    str(item["relpath"]), self._infer_kind(str(item["relpath"]))
                ),
                ordinal=index,
            )
            for index, item in enumerate(entries)
        ]
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            existing = self.repo.get_collection_in_connection(connection, identifier)
            if existing is not None:
                existing_sources = self.repo.list_sources_in_connection(connection, identifier)
                if (
                    existing.name == display_name
                    and existing.context_id == context_id
                    and existing.root_hash == str(manifest["root_hash"])
                    and [
                        (x.relpath, x.sha256, x.size_bytes, x.kind, x.ordinal)
                        for x in existing_sources
                    ]
                    == [
                        (x.relpath, x.sha256, x.size_bytes, x.kind, x.ordinal)
                        for x in sources
                    ]
                ):
                    return KnowledgeRegistrationResult(
                        self._status_projection(existing, existing_sources), replayed=True
                    )
                raise KnowledgeConflictError(
                    "knowledge_id already exists with a different collection"
                )
            collection = KnowledgeCollection(
                knowledge_id=identifier,
                name=display_name,
                context_id=context_id,
                root_hash=str(manifest["root_hash"]),
                source_count=len(sources),
                total_bytes=int(manifest["total_bytes"]),
                created_at=db_now,
            )
            self.repo.create_collection(connection, collection, sources)
        return KnowledgeRegistrationResult(
            self._status_projection(collection, sources), replayed=False
        )

    def status(self, knowledge_id: str) -> dict[str, Any]:
        collection, sources = self._required_collection(knowledge_id)
        return self._status_projection(collection, sources)

    def list(self, *, limit: int = 100) -> dict[str, Any]:
        bounded = self._bounded_int(limit, "limit", 1, MAX_HISTORY_LIMIT)
        return {
            "ok": True,
            "schema": KNOWLEDGE_STATUS_SCHEMA,
            "collections": [
                item.to_public_dict()
                for item in self.repo.list_collections(limit=bounded)
            ],
            "content_returned": False,
            "automatic_prompt_injection": False,
        }

    def verify(
        self,
        knowledge_id: str,
        cwd: str,
        *,
        allow_external_symlinks: bool = False,
    ) -> dict[str, Any]:
        collection, _ = self._required_collection(knowledge_id)
        try:
            result = self.contexts.verify(
                collection.context_id,
                cwd,
                allow_external_symlinks=allow_external_symlinks,
            )
        except ContextError as exc:
            raise KnowledgeDriftError(str(exc)) from exc
        return {
            "ok": True,
            "schema": KNOWLEDGE_STATUS_SCHEMA,
            "knowledge_id": collection.knowledge_id,
            **result,
            "content_returned": False,
            "automatic_prompt_injection": False,
        }

    # -------------------------------------------------------------- resolution

    def search(
        self,
        knowledge_id: str,
        cwd: str,
        query: str,
        *,
        kind: str = "",
        max_results: int = DEFAULT_SEARCH_RESULTS,
        max_snippet_chars: int = DEFAULT_SNIPPET_CHARS,
        allow_external_symlinks: bool = False,
        task_id: str = "",
    ) -> dict[str, Any]:
        return self._resolve(
            operation="search",
            knowledge_id=knowledge_id,
            cwd=cwd,
            query=query,
            kind=kind,
            max_results=max_results,
            max_snippet_chars=max_snippet_chars,
            max_total_bytes=0,
            allow_external_symlinks=allow_external_symlinks,
            task_id=task_id,
        )

    def bundle(
        self,
        knowledge_id: str,
        cwd: str,
        query: str,
        *,
        kind: str = "",
        max_sources: int = DEFAULT_SEARCH_RESULTS,
        max_snippet_chars: int = DEFAULT_SNIPPET_CHARS,
        max_total_bytes: int = DEFAULT_BUNDLE_BYTES,
        allow_external_symlinks: bool = False,
        task_id: str = "",
    ) -> dict[str, Any]:
        return self._resolve(
            operation="bundle",
            knowledge_id=knowledge_id,
            cwd=cwd,
            query=query,
            kind=kind,
            max_results=max_sources,
            max_snippet_chars=max_snippet_chars,
            max_total_bytes=max_total_bytes,
            allow_external_symlinks=allow_external_symlinks,
            task_id=task_id,
        )

    def _resolve(
        self,
        *,
        operation: str,
        knowledge_id: str,
        cwd: str,
        query: str,
        kind: str,
        max_results: int,
        max_snippet_chars: int,
        max_total_bytes: int,
        allow_external_symlinks: bool,
        task_id: str,
    ) -> dict[str, Any]:
        requested_at = self._db_now()
        resolution_id = new_knowledge_resolution_id()
        query_value = str(query or "").strip()
        query_hash = self._hash_text(query_value)
        linked_task_id: str | None = None
        collection: KnowledgeCollection | None = None
        status = "failed"
        error_code: str | None = None
        error_message: str | None = None
        output_hash: str | None = None
        source_count = 0
        citation_count = 0
        bytes_returned = 0
        response: dict[str, Any] | None = None
        try:
            collection, sources = self._required_collection(knowledge_id)
            linked_task_id = self._task_reference(task_id)
            query_value = self._query(query_value)
            query_hash = self._hash_text(query_value)
            kind_value = self._kind_filter(kind)
            result_limit = self._bounded_int(
                max_results, "max_results", 1, MAX_SEARCH_RESULTS
            )
            snippet_limit = self._bounded_int(
                max_snippet_chars,
                "max_snippet_chars",
                64,
                MAX_SNIPPET_CHARS,
            )
            bundle_limit = 0
            if operation == "bundle":
                bundle_limit = self._bounded_int(
                    max_total_bytes,
                    "max_total_bytes",
                    256,
                    MAX_BUNDLE_BYTES,
                )
            citations = self._search_sources(
                collection,
                sources,
                cwd,
                query_value,
                kind=kind_value,
                max_results=result_limit,
                max_snippet_chars=snippet_limit,
                allow_external_symlinks=allow_external_symlinks,
            )
            source_count = len({item["relpath"] for item in citations})
            citation_count = len(citations)
            if operation == "search":
                response = {
                    "ok": True,
                    "schema": KNOWLEDGE_SEARCH_SCHEMA,
                    "resolution_id": resolution_id,
                    "knowledge_id": collection.knowledge_id,
                    "root_hash": collection.root_hash,
                    "query_sha256": query_hash,
                    "citations": citations,
                    "source_count": source_count,
                    "citation_count": citation_count,
                    "content_returned": bool(citations),
                    "content_stored": False,
                    "query_stored": False,
                    "injected_into_task": False,
                    "automatic_writeback": False,
                }
            else:
                body, included = self._bundle_body(
                    collection, query_hash, citations, bundle_limit
                )
                citations = included
                source_count = len({item["relpath"] for item in citations})
                citation_count = len(citations)
                response = {
                    "ok": True,
                    "schema": KNOWLEDGE_BUNDLE_SCHEMA,
                    "resolution_id": resolution_id,
                    "knowledge_id": collection.knowledge_id,
                    "root_hash": collection.root_hash,
                    "query_sha256": query_hash,
                    "content": body,
                    "citations": citations,
                    "source_count": source_count,
                    "citation_count": citation_count,
                    "content_returned": True,
                    "content_stored": False,
                    "query_stored": False,
                    "injected_into_task": False,
                    "automatic_writeback": False,
                }
            encoded = json.dumps(response, ensure_ascii=False, sort_keys=True).encode("utf-8")
            bytes_returned = len(encoded)
            output_hash = hashlib.sha256(encoded).hexdigest()
            status = "succeeded"
        except KnowledgeError as exc:
            status = exc.status
            error_code = exc.code
            error_message = str(exc)
        except ContextError as exc:
            status = "failed"
            error_code = "knowledge_drift"
            error_message = str(exc)
        except (OSError, UnicodeError):
            status = "failed"
            error_code = "knowledge_read_failed"
            error_message = "knowledge source could not be read"
        except Exception:
            status = "failed"
            error_code = "knowledge_internal_error"
            error_message = "knowledge resolution failed internally"

        finished_at = max(self._db_now(), requested_at)
        if collection is not None:
            resolution = KnowledgeResolution(
                resolution_id=resolution_id,
                knowledge_id=collection.knowledge_id,
                task_id=linked_task_id,
                operation=operation,
                status=status,
                requested_at=requested_at,
                finished_at=finished_at,
                query_sha256=query_hash,
                output_sha256=output_hash,
                source_count=source_count,
                citation_count=citation_count,
                bytes_returned=bytes_returned,
                error_code=error_code,
                error_message=error_message,
                metadata_json=json.dumps(
                    {
                        "content_returned": status == "succeeded" and bytes_returned > 0,
                        "content_stored": False,
                        "query_stored": False,
                        "cwd_stored": False,
                        "automatic_prompt_injection": False,
                        "automatic_writeback": False,
                        "task_linked": linked_task_id is not None,
                    },
                    sort_keys=True,
                ),
            )
            with self.db.transaction() as connection:
                self.repo.create_resolution(connection, resolution)

        if status == "succeeded" and response is not None:
            return response
        return {
            "ok": False,
            "schema": (
                KNOWLEDGE_BUNDLE_SCHEMA if operation == "bundle" else KNOWLEDGE_SEARCH_SCHEMA
            ),
            "resolution_id": resolution_id if collection is not None else None,
            "knowledge_id": collection.knowledge_id if collection is not None else None,
            "status": status,
            "error": {
                "code": error_code or "knowledge_error",
                "message": error_message or "knowledge resolution failed",
            },
            "query_stored": False,
            "content_stored": False,
            "injected_into_task": False,
        }

    # ------------------------------------------------------------------ audit

    def history(
        self,
        *,
        knowledge_id: str = "",
        operation: str = "",
        status: str = "",
        task_id: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        identifier = str(knowledge_id or "").strip()
        if identifier:
            self._required_collection(identifier)
        operation_value = str(operation or "").strip().lower()
        if operation_value and operation_value not in {"search", "bundle"}:
            raise KnowledgePolicyError("operation must be search or bundle")
        status_value = str(status or "").strip().lower()
        if status_value and status_value not in {"succeeded", "failed", "rejected"}:
            raise KnowledgePolicyError("unsupported resolution status")
        task_value = str(task_id or "").strip()
        bounded = self._bounded_int(limit, "limit", 1, MAX_HISTORY_LIMIT)
        return {
            "ok": True,
            "schema": KNOWLEDGE_HISTORY_SCHEMA,
            "resolutions": [
                item.to_public_dict()
                for item in self.repo.list_resolutions(
                    knowledge_id=identifier,
                    operation=operation_value,
                    status=status_value,
                    task_id=task_value,
                    limit=bounded,
                )
            ],
            "raw_query_stored": False,
            "raw_output_stored": False,
        }

    def get_resolution(self, resolution_id: str) -> dict[str, Any]:
        identifier = str(resolution_id or "").strip()
        if not identifier or len(identifier) > 80:
            raise KnowledgePolicyError("invalid resolution_id")
        value = self.repo.get_resolution(identifier)
        if value is None:
            raise KnowledgeNotFoundError("knowledge resolution not found")
        return {"ok": True, "schema": KNOWLEDGE_HISTORY_SCHEMA, **value.to_public_dict()}

    # ---------------------------------------------------------------- helpers

    def _search_sources(
        self,
        collection: KnowledgeCollection,
        sources: list[KnowledgeSource],
        cwd: str,
        query: str,
        *,
        kind: str,
        max_results: int,
        max_snippet_chars: int,
        allow_external_symlinks: bool,
    ) -> list[dict[str, Any]]:
        root = self.contexts._root(cwd)
        terms = self._terms(query)
        matches: list[dict[str, Any]] = []
        scanned = 0
        for source in sources:
            if kind and source.kind != kind:
                continue
            scanned += source.size_bytes
            if scanned > MAX_CONTEXT_TOTAL_BYTES:
                raise KnowledgePolicyError("knowledge scan exceeds total byte limit")
            candidate = self.contexts._resolved_candidate(
                root,
                source.relpath,
                allow_external_symlinks=allow_external_symlinks,
            )
            data = candidate.read_bytes()
            if len(data) != source.size_bytes or hashlib.sha256(data).hexdigest() != source.sha256:
                raise KnowledgeDriftError(f"knowledge source changed: {source.relpath}")
            if b"\x00" in data:
                raise KnowledgePolicyError(f"knowledge source is not text: {source.relpath}")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise KnowledgePolicyError(
                    f"knowledge source is not UTF-8: {source.relpath}"
                ) from exc
            lines = text.splitlines()
            path_text = source.relpath.lower()
            path_score = sum(8 for term in terms if term in path_text)
            for index, line in enumerate(lines):
                lowered = line.lower()
                occurrences = sum(lowered.count(term) for term in terms)
                distinct = sum(1 for term in terms if term in lowered)
                if not occurrences and (not path_score or index != 0):
                    continue
                score = occurrences * 3 + distinct * 4 + path_score
                if line.lstrip().startswith("#"):
                    score += 3
                start = max(0, index - 1)
                end = min(len(lines), index + 2)
                snippet = "\n".join(lines[start:end]).strip()
                snippet = self._truncate_chars(snippet, max_snippet_chars)
                matches.append(
                    {
                        "knowledge_id": collection.knowledge_id,
                        "relpath": source.relpath,
                        "sha256": source.sha256,
                        "kind": source.kind,
                        "line_start": start + 1,
                        "line_end": end,
                        "score": score,
                        "snippet": snippet,
                    }
                )
        matches.sort(
            key=lambda item: (
                -int(item["score"]),
                str(item["relpath"]),
                int(item["line_start"]),
            )
        )
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int]] = set()
        for item in matches:
            key = (str(item["relpath"]), int(item["line_start"]), int(item["line_end"]))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= max_results:
                break
        return deduped

    @staticmethod
    def _bundle_body(
        collection: KnowledgeCollection,
        query_hash: str,
        citations: list[dict[str, Any]],
        max_total_bytes: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        prefix = (
            f"# Knowledge Bundle {collection.knowledge_id}\n\n"
            f"Root-Hash: `{collection.root_hash}`\n\n"
            f"Query-SHA256: `{query_hash}`\n\n"
        )
        body = prefix
        included: list[dict[str, Any]] = []
        for index, item in enumerate(citations, start=1):
            section = (
                f"## [{index}] {item['relpath']}:{item['line_start']}-{item['line_end']}\n\n"
                f"Kind: `{item['kind']}`  \n"
                f"SHA-256: `{item['sha256']}`\n\n"
                f"{item['snippet']}\n\n"
            )
            if len((body + section).encode("utf-8")) > max_total_bytes:
                break
            body += section
            included.append(item)
        return body, included

    def _required_collection(
        self, knowledge_id: str
    ) -> tuple[KnowledgeCollection, list[KnowledgeSource]]:
        identifier = self._required_knowledge_id(knowledge_id)
        collection = self.repo.get_collection(identifier)
        if collection is None:
            raise KnowledgeNotFoundError("knowledge collection not found")
        sources = self.repo.list_sources(identifier)
        return collection, sources

    def _status_projection(
        self,
        collection: KnowledgeCollection,
        sources: list[KnowledgeSource],
    ) -> dict[str, Any]:
        return {
            "schema": KNOWLEDGE_STATUS_SCHEMA,
            **collection.to_public_dict(),
            "sources": [item.to_public_dict() for item in sources],
            "search_mode": "explicit_live_verified_lexical",
            "vector_database_used": False,
            "embeddings_stored": False,
        }

    def _task_reference(self, task_id: str) -> str | None:
        value = str(task_id or "").strip()
        if not value:
            return None
        if len(value) > 80 or self.tasks.get_by_id(value) is None:
            raise KnowledgePolicyError("task_id does not reference a durable task")
        return value

    @staticmethod
    def _knowledge_id(value: str) -> str:
        raw = str(value or "").strip() or new_knowledge_id()
        if not _KNOWLEDGE_ID_RE.fullmatch(raw):
            raise KnowledgePolicyError("invalid knowledge_id")
        return raw

    @staticmethod
    def _required_knowledge_id(value: str) -> str:
        raw = str(value or "").strip()
        if not _KNOWLEDGE_ID_RE.fullmatch(raw):
            raise KnowledgePolicyError("invalid knowledge_id")
        return raw

    @staticmethod
    def _context_id(knowledge_id: str) -> str:
        digest = hashlib.sha256(knowledge_id.encode("utf-8")).hexdigest()[:24]
        return f"kctx-{digest}"

    @staticmethod
    def _name(value: str) -> str:
        raw = str(value or "").strip()
        if not _NAME_RE.fullmatch(raw):
            raise KnowledgePolicyError("invalid knowledge collection name")
        return raw

    @staticmethod
    def _kind_map(value: dict[str, str] | None) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise KnowledgePolicyError("source_kinds must be an object")
        result: dict[str, str] = {}
        for key, kind in value.items():
            relpath = str(key or "").strip()
            kind_value = str(kind or "").strip().lower()
            if not relpath or kind_value not in _KIND_VALUES:
                raise KnowledgePolicyError("source_kinds contains an invalid entry")
            result[relpath] = kind_value
        return result

    @staticmethod
    def _kind_filter(value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw and raw not in _KIND_VALUES:
            raise KnowledgePolicyError("unsupported knowledge source kind")
        return raw

    @staticmethod
    def _infer_kind(relpath: str) -> str:
        value = relpath.lower().replace("\\", "/")
        name = value.rsplit("/", 1)[-1]
        if name in {"readme.md", "project.md", "overview.md"}:
            return "overview"
        if name in {"soul.md", "rules.md", "coding-rules.md"} or "/rules/" in f"/{value}":
            return "rules"
        if any(token in value for token in ("architecture", "/design/", "design.md")):
            return "architecture"
        if (
            name.startswith("adr-")
            or name.startswith("adr_")
            or any(token in value for token in ("/adr/", "/decisions/", "decision"))
        ):
            return "decision"
        if any(token in value for token in ("experience", "lessons", "incident", "troubleshoot")):
            return "experience"
        return "reference"

    @staticmethod
    def _query(value: str) -> str:
        if not value or len(value) > MAX_QUERY_CHARS:
            raise KnowledgePolicyError(
                f"query must contain 1..{MAX_QUERY_CHARS} characters"
            )
        if any(ord(ch) < 32 and ch not in "\t\r\n" for ch in value):
            raise KnowledgePolicyError("query contains control characters")
        return value

    @staticmethod
    def _terms(query: str) -> list[str]:
        terms: list[str] = []
        for token in _WORD_RE.findall(query.lower()):
            if token not in terms:
                terms.append(token)
            if any("\u3400" <= ch <= "\u9fff" for ch in token) and len(token) > 2:
                for index in range(len(token) - 1):
                    pair = token[index : index + 2]
                    if pair not in terms:
                        terms.append(pair)
            if len(terms) >= MAX_QUERY_TERMS:
                break
        if not terms:
            raise KnowledgePolicyError("query contains no searchable terms")
        return terms

    @staticmethod
    def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise KnowledgePolicyError(f"{name} must be an integer")
        if value < minimum or value > maximum:
            raise KnowledgePolicyError(f"{name} must be between {minimum} and {maximum}")
        return value

    @staticmethod
    def _truncate_chars(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def _hash_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _db_now(self) -> float:
        with self.db.connect() as connection:
            return float(
                connection.execute(
                    "SELECT (julianday('now') - 2440587.5) * 86400.0"
                ).fetchone()[0]
            )
