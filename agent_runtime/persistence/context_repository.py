"""Persistence for content-free project context manifests."""

from __future__ import annotations

import sqlite3

from agent_runtime.domain.context import ContextEntry, ContextManifest
from agent_runtime.persistence.database import Database


class ContextRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        connection: sqlite3.Connection,
        manifest: ContextManifest,
        entries: list[ContextEntry],
    ) -> None:
        connection.execute(
            """
            INSERT INTO context_manifests
                (context_id, root_hash, file_count, total_bytes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                manifest.context_id,
                manifest.root_hash,
                manifest.file_count,
                manifest.total_bytes,
                manifest.created_at,
            ),
        )
        connection.executemany(
            """
            INSERT INTO context_entries
                (context_id, relpath, sha256, size_bytes)
            VALUES (?, ?, ?, ?)
            """,
            [
                (entry.context_id, entry.relpath, entry.sha256, entry.size_bytes)
                for entry in entries
            ],
        )

    def get_manifest(self, context_id: str) -> ContextManifest | None:
        with self.db.connect() as connection:
            return self.get_manifest_in_connection(connection, context_id)

    @staticmethod
    def get_manifest_in_connection(
        connection: sqlite3.Connection,
        context_id: str,
    ) -> ContextManifest | None:
        row = connection.execute(
            """
            SELECT context_id, root_hash, file_count, total_bytes, created_at
            FROM context_manifests WHERE context_id = ?
            """,
            (context_id,),
        ).fetchone()
        if row is None:
            return None
        return ContextManifest(
            context_id=str(row["context_id"]),
            root_hash=str(row["root_hash"]),
            file_count=int(row["file_count"]),
            total_bytes=int(row["total_bytes"]),
            created_at=float(row["created_at"]),
        )

    def list_entries(self, context_id: str) -> list[ContextEntry]:
        with self.db.connect() as connection:
            return self.list_entries_in_connection(connection, context_id)

    @staticmethod
    def list_entries_in_connection(
        connection: sqlite3.Connection,
        context_id: str,
    ) -> list[ContextEntry]:
        rows = connection.execute(
            """
            SELECT context_id, relpath, sha256, size_bytes
            FROM context_entries
            WHERE context_id = ?
            ORDER BY relpath
            """,
            (context_id,),
        ).fetchall()
        return [
            ContextEntry(
                context_id=str(row["context_id"]),
                relpath=str(row["relpath"]),
                sha256=str(row["sha256"]),
                size_bytes=int(row["size_bytes"]),
            )
            for row in rows
        ]
