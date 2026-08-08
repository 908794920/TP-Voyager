"""Durable session record linking a runtime task to one backend session."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Session:
    """Runtime session row.

    ``backend_session_id`` is the private Crew-side session identifier and must
    never appear in public projections.
    ``metadata_json`` holds only safe, necessary, recoverable routing metadata
    (cwd, model, identity, ...) — never prompts, secrets, thoughts, or raw tool
    output.
    """

    session_id: str
    task_id: str
    backend: str
    route: str
    created_at: float
    updated_at: float
    backend_session_id: str | None = None
    metadata_json: str = "{}"
    # Lease/fencing: which Runtime instance owns this session, its
    # generation (bumped on every ownership change), and when the lease
    # expires.  A stale owner's writes are rejected by generation checks.
    owner_instance_id: str | None = None
    owner_generation: int = 0
    lease_expires_at: float | None = None
