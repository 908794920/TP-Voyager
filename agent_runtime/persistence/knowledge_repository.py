"""Persistence for content-free Knowledge Runtime metadata and audits."""

from __future__ import annotations

import sqlite3

from agent_runtime.domain.knowledge import (
    KnowledgeCollection,
    KnowledgeResolution,
    KnowledgeSource,
)
from agent_runtime.persistence.database import Database


_RESOLUTION_COLUMNS = """
resolution_id, knowledge_id, task_id, operation, status, requested_at,
finished_at, query_sha256, output_sha256, source_count, citation_count,
bytes_returned, error_code, error_message, metadata_json
"""


class KnowledgeRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def create_collection(
        connection: sqlite3.Connection,
        collection: KnowledgeCollection,
        sources: list[KnowledgeSource],
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_collections (
                knowledge_id, name, context_id, root_hash, source_count,
                total_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                collection.knowledge_id,
                collection.name,
                collection.context_id,
                collection.root_hash,
                collection.source_count,
                collection.total_bytes,
                collection.created_at,
            ),
        )
        connection.executemany(
            """
            INSERT INTO knowledge_sources (
                knowledge_id, context_id, relpath, sha256, size_bytes, kind, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    source.knowledge_id,
                    source.context_id,
                    source.relpath,
                    source.sha256,
                    source.size_bytes,
                    source.kind,
                    source.ordinal,
                )
                for source in sources
            ],
        )

    def get_collection(self, knowledge_id: str) -> KnowledgeCollection | None:
        with self.db.connect() as connection:
            return self.get_collection_in_connection(connection, knowledge_id)

    @classmethod
    def get_collection_in_connection(
        cls, connection: sqlite3.Connection, knowledge_id: str
    ) -> KnowledgeCollection | None:
        row = connection.execute(
            """
            SELECT knowledge_id, name, context_id, root_hash, source_count,
                   total_bytes, created_at
            FROM knowledge_collections WHERE knowledge_id = ?
            """,
            (knowledge_id,),
        ).fetchone()
        return cls._collection(row) if row is not None else None

    def list_collections(self, *, limit: int = 100) -> list[KnowledgeCollection]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT knowledge_id, name, context_id, root_hash, source_count,
                       total_bytes, created_at
                FROM knowledge_collections
                ORDER BY created_at DESC, knowledge_id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._collection(row) for row in rows]

    def list_sources(self, knowledge_id: str) -> list[KnowledgeSource]:
        with self.db.connect() as connection:
            return self.list_sources_in_connection(connection, knowledge_id)

    @classmethod
    def list_sources_in_connection(
        cls, connection: sqlite3.Connection, knowledge_id: str
    ) -> list[KnowledgeSource]:
        rows = connection.execute(
            """
            SELECT knowledge_id, context_id, relpath, sha256, size_bytes, kind, ordinal
            FROM knowledge_sources
            WHERE knowledge_id = ?
            ORDER BY ordinal, relpath
            """,
            (knowledge_id,),
        ).fetchall()
        return [cls._source(row) for row in rows]

    @staticmethod
    def create_resolution(
        connection: sqlite3.Connection,
        resolution: KnowledgeResolution,
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_resolutions (
                resolution_id, knowledge_id, task_id, operation, status,
                requested_at, finished_at, query_sha256, output_sha256,
                source_count, citation_count, bytes_returned, error_code,
                error_message, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolution.resolution_id,
                resolution.knowledge_id,
                resolution.task_id,
                resolution.operation,
                resolution.status,
                resolution.requested_at,
                resolution.finished_at,
                resolution.query_sha256,
                resolution.output_sha256,
                resolution.source_count,
                resolution.citation_count,
                resolution.bytes_returned,
                resolution.error_code,
                resolution.error_message,
                resolution.metadata_json,
            ),
        )

    def get_resolution(self, resolution_id: str) -> KnowledgeResolution | None:
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {_RESOLUTION_COLUMNS} FROM knowledge_resolutions "
                "WHERE resolution_id = ?",
                (resolution_id,),
            ).fetchone()
        return self._resolution(row) if row is not None else None

    def list_resolutions(
        self,
        *,
        knowledge_id: str = "",
        operation: str = "",
        status: str = "",
        task_id: str = "",
        limit: int = 50,
    ) -> list[KnowledgeResolution]:
        clauses: list[str] = []
        values: list[object] = []
        if knowledge_id:
            clauses.append("knowledge_id = ?")
            values.append(knowledge_id)
        if operation:
            clauses.append("operation = ?")
            values.append(operation)
        if status:
            clauses.append("status = ?")
            values.append(status)
        if task_id:
            clauses.append("task_id = ?")
            values.append(task_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(int(limit))
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {_RESOLUTION_COLUMNS}
                FROM knowledge_resolutions
                {where}
                ORDER BY requested_at DESC, resolution_id DESC
                LIMIT ?
                """,
                tuple(values),
            ).fetchall()
        return [self._resolution(row) for row in rows]

    @staticmethod
    def _collection(row: sqlite3.Row) -> KnowledgeCollection:
        return KnowledgeCollection(
            knowledge_id=str(row["knowledge_id"]),
            name=str(row["name"]),
            context_id=str(row["context_id"]),
            root_hash=str(row["root_hash"]),
            source_count=int(row["source_count"]),
            total_bytes=int(row["total_bytes"]),
            created_at=float(row["created_at"]),
        )

    @staticmethod
    def _source(row: sqlite3.Row) -> KnowledgeSource:
        return KnowledgeSource(
            knowledge_id=str(row["knowledge_id"]),
            context_id=str(row["context_id"]),
            relpath=str(row["relpath"]),
            sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),
            kind=str(row["kind"]),
            ordinal=int(row["ordinal"]),
        )

    @staticmethod
    def _resolution(row: sqlite3.Row) -> KnowledgeResolution:
        return KnowledgeResolution(
            resolution_id=str(row["resolution_id"]),
            knowledge_id=str(row["knowledge_id"]),
            task_id=str(row["task_id"]) if row["task_id"] is not None else None,
            operation=str(row["operation"]),
            status=str(row["status"]),
            requested_at=float(row["requested_at"]),
            finished_at=float(row["finished_at"]),
            query_sha256=str(row["query_sha256"]),
            output_sha256=(
                str(row["output_sha256"])
                if row["output_sha256"] is not None
                else None
            ),
            source_count=int(row["source_count"]),
            citation_count=int(row["citation_count"]),
            bytes_returned=int(row["bytes_returned"]),
            error_code=str(row["error_code"]) if row["error_code"] else None,
            error_message=(
                str(row["error_message"]) if row["error_message"] else None
            ),
            metadata_json=str(row["metadata_json"] or "{}"),
        )
