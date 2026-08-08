"""V1.5 Knowledge Runtime domain models.

The Knowledge Runtime indexes caller-selected project knowledge without storing
file contents or search text.  Durable rows contain source identity, hashes,
classification, and content-free resolution audit metadata only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


KNOWLEDGE_STATUS_SCHEMA = "workbuddy.knowledge_status/v1"
KNOWLEDGE_SEARCH_SCHEMA = "workbuddy.knowledge_search/v1"
KNOWLEDGE_BUNDLE_SCHEMA = "workbuddy.knowledge_bundle/v1"
KNOWLEDGE_HISTORY_SCHEMA = "workbuddy.knowledge_history/v1"


@dataclass(frozen=True)
class KnowledgeCollection:
    knowledge_id: str
    name: str
    context_id: str
    root_hash: str
    source_count: int
    total_bytes: int
    created_at: float

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "name": self.name,
            "context_id": self.context_id,
            "root_hash": self.root_hash,
            "source_count": self.source_count,
            "total_bytes": self.total_bytes,
            "created_at": self.created_at,
            "content_stored": False,
            "cwd_stored": False,
            "automatic_prompt_injection": False,
            "automatic_writeback": False,
        }


@dataclass(frozen=True)
class KnowledgeSource:
    knowledge_id: str
    context_id: str
    relpath: str
    sha256: str
    size_bytes: int
    kind: str
    ordinal: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "relpath": self.relpath,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "kind": self.kind,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True)
class KnowledgeResolution:
    resolution_id: str
    knowledge_id: str
    operation: str
    status: str
    requested_at: float
    finished_at: float
    query_sha256: str
    output_sha256: str | None
    source_count: int
    citation_count: int
    bytes_returned: int
    task_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata_json: str = "{}"

    def metadata(self) -> dict[str, Any]:
        try:
            value = json.loads(self.metadata_json or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "knowledge_id": self.knowledge_id,
            "operation": self.operation,
            "status": self.status,
            "requested_at": self.requested_at,
            "finished_at": self.finished_at,
            "query_sha256": self.query_sha256,
            "output_sha256": self.output_sha256,
            "source_count": self.source_count,
            "citation_count": self.citation_count,
            "bytes_returned": self.bytes_returned,
            "task_id": self.task_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": self.metadata(),
            "raw_query_stored": False,
            "raw_output_stored": False,
            "content_stored": False,
            "cwd_stored": False,
        }
