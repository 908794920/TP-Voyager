"""Normalized TP-Voyager Crew contracts.

These models describe what the Captain may know about a worker without
exposing vendor CLI flags, credentials, private session ids, or raw logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CrewDescriptor:
    backend: str
    display_name: str
    maturity: str
    official_sources: tuple[str, ...]
    capabilities: tuple[str, ...]
    controlled_capabilities: tuple[str, ...] = ()
    documented_routes: tuple[str, ...] = ()
    implemented_routes: tuple[str, ...] = ()
    dispatch_ready: bool = False
    model_discovery: str = "unsupported"
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "display_name": self.display_name,
            "maturity": self.maturity,
            "official_sources": list(self.official_sources),
            "capabilities": list(self.capabilities),
            "controlled_capabilities": list(self.controlled_capabilities),
            "documented_routes": list(self.documented_routes),
            "implemented_routes": list(self.implemented_routes),
            "dispatch_ready": self.dispatch_ready,
            "model_discovery": self.model_discovery,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ModelDescriptor:
    backend: str
    model_id: str
    display_name: str = ""
    available: bool | None = True
    disabled_reason: str | None = None
    source: str = "unknown"
    observed_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "model_id": self.model_id,
            "display_name": self.display_name or self.model_id,
            "available": self.available,
            "disabled_reason": self.disabled_reason,
            "source": self.source,
            "observed_at": self.observed_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CrewHealthSnapshot:
    backend: str
    installed: bool | None
    dispatch_ready: bool
    availability: str
    version: str | None = None
    sample_count: int = 0
    success_rate: float | None = None
    average_duration_seconds: float | None = None
    failure_streak: int = 0
    last_success_at: float | None = None
    last_failure_at: float | None = None
    auth_status: str = "not_probed"
    model_catalog_status: str = "unknown"
    last_successful_model: str | None = None
    last_successful_model_at: float | None = None
    last_successful_model_source: str | None = None
    probe_error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "installed": self.installed,
            "dispatch_ready": self.dispatch_ready,
            "availability": self.availability,
            "version": self.version,
            "sample_count": self.sample_count,
            "success_rate": self.success_rate,
            "average_duration_seconds": self.average_duration_seconds,
            "failure_streak": self.failure_streak,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "auth_status": self.auth_status,
            "model_catalog_status": self.model_catalog_status,
            "last_successful_model": self.last_successful_model,
            "last_successful_model_at": self.last_successful_model_at,
            "last_successful_model_source": self.last_successful_model_source,
            "probe_error": self.probe_error,
            "detail": dict(self.detail),
        }
