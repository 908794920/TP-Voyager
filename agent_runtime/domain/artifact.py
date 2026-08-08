"""PR4 Artifact Declaration Registry domain model.

PR4-B/C only record what the agent/backend *declared* produced: no physical
file copy, no hash computation, no content storage.  ``capture_state`` stays
``declared`` (or ``rejected``); PR4-D performs the real capture and flips it
to ``captured``/``missing``.  Until then ``storage_key`` may be NULL.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Artifact:
    """One artifact declaration, always bound to exactly one Attempt.

    Mutable (unlike Evidence) because PR4-D will transition ``capture_state``.
    ``workspace_relpath`` is runtime-validated before persistence; host
    absolute paths and UNC paths are never stored.
    """

    artifact_id: str
    task_id: str
    attempt_id: str
    origin: str
    kind: str
    name: str
    capture_state: str
    declared_at: float
    created_at: float
    updated_at: float
    workspace_relpath: str | None = None
    storage_key: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    captured_at: float | None = None
    metadata_json: str = "{}"
