"""PR4 Evidence domain model.

Evidence is an immutable, traceable structured record expressing what the
agent declared, what the backend adapter directly observed, what the runtime
itself observed, and (PR5) what a verifier later confirmed.  Evidence is
append-only: the repository exposes no update/delete, the row has no
``updated_at``, and a future Verifier appends ``verified_*`` rows that
reference the original via ``subject_evidence_id``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    """One immutable evidence record, always bound to exactly one Attempt.

    ``detail_json`` is a pre-serialized JSON string (runtime-safe whitelist);
    ``captured_at`` is the fenced transaction's database clock.
    """

    evidence_id: str
    task_id: str
    attempt_id: str
    evidence_type: str
    trust_state: str
    origin: str
    summary: str
    detail_json: str
    captured_at: float
    created_at: float
    subject_evidence_id: str | None = None
    artifact_id: str | None = None
