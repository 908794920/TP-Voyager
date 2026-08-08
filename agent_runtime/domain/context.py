"""Content-free project context manifest models.

The durable database stores only normalized relative paths, hashes, and sizes.
File bytes are never persisted by this subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextManifest:
    context_id: str
    root_hash: str
    file_count: int
    total_bytes: int
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "root_hash": self.root_hash,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ContextEntry:
    context_id: str
    relpath: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "relpath": self.relpath,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }
