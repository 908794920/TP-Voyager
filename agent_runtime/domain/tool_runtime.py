"""V1.4 Tool Runtime domain models.

The Tool Runtime is an explicit, caller-driven control plane.  It does not
inject tools into backend prompts and it does not automatically dispatch a
backend.  Durable records contain only audit metadata and hashes; raw tool
inputs, workspace paths, query text, file contents, and Git diffs are never
persisted by this subsystem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


TOOL_CATALOG_SCHEMA = "workbuddy.tool_catalog/v1"
TOOL_RESULT_SCHEMA = "workbuddy.tool_result/v1"
TOOL_HISTORY_SCHEMA = "workbuddy.tool_history/v1"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    category: str
    summary: str
    mutability: str = "read_only"
    content_returned: bool = False
    arguments: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "category": self.category,
            "summary": self.summary,
            "mutability": self.mutability,
            "content_returned": self.content_returned,
            "arguments": dict(self.arguments or {}),
            "limits": dict(self.limits or {}),
            "automatic_dispatch": False,
        }


@dataclass(frozen=True)
class ToolInvocation:
    invocation_id: str
    tool_name: str
    tool_version: str
    status: str
    requested_at: float
    finished_at: float
    workspace_ref: str
    input_sha256: str
    output_sha256: str | None
    bytes_returned: int
    item_count: int
    task_id: str | None = None
    context_id: str | None = None
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
            "invocation_id": self.invocation_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "status": self.status,
            "requested_at": self.requested_at,
            "finished_at": self.finished_at,
            "workspace_ref": self.workspace_ref,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "bytes_returned": self.bytes_returned,
            "item_count": self.item_count,
            "task_id": self.task_id,
            "context_id": self.context_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": self.metadata(),
            "raw_input_stored": False,
            "raw_output_stored": False,
            "cwd_stored": False,
        }
