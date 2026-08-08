"""Read-only backend capability catalog and explicit requirement matching.

V1.2 deliberately does not select or dispatch a backend.  This service only
projects declarations already owned by registered backend adapters and tells a
caller which adapters satisfy an explicit set of requirements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.backends.base import BackendCapabilities
from agent_runtime.backends.errors import BackendError
from agent_runtime.backends.registry import BackendRegistry


class CapabilityQueryError(ValueError):
    """An explicit capability query is invalid or cannot be evaluated."""


@dataclass(frozen=True)
class CapabilityRequirements:
    runtime: str = ""
    route: str = ""
    require_resume: bool = False
    require_streaming: bool = False
    require_cancel: bool = False
    require_reasoning_effort: bool = False

    def normalized(self) -> "CapabilityRequirements":
        runtime = self.runtime.strip().lower()
        route = self.route.strip().lower()
        for label, value in (("runtime", runtime), ("route", route)):
            if len(value) > 64:
                raise CapabilityQueryError(f"{label} is too long")
            if value and not all(ch.isalnum() or ch in "_-" for ch in value):
                raise CapabilityQueryError(f"{label} contains unsupported characters")
        return CapabilityRequirements(
            runtime=runtime,
            route=route,
            require_resume=bool(self.require_resume),
            require_streaming=bool(self.require_streaming),
            require_cancel=bool(self.require_cancel),
            require_reasoning_effort=bool(self.require_reasoning_effort),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "route": self.route,
            "require_resume": self.require_resume,
            "require_streaming": self.require_streaming,
            "require_cancel": self.require_cancel,
            "require_reasoning_effort": self.require_reasoning_effort,
        }


class BackendCapabilityService:
    """Inspect registered adapters without probing, selecting, or dispatching."""

    def __init__(self, registry: BackendRegistry) -> None:
        self.registry = registry

    def query(self, requirements: CapabilityRequirements | None = None) -> dict[str, Any]:
        requested = (requirements or CapabilityRequirements()).normalized()
        entries: list[dict[str, Any]] = []
        matches: list[str] = []
        for registered_name in self.registry.names():
            if requested.runtime and registered_name != requested.runtime:
                continue
            try:
                backend = self.registry.resolve(registered_name)
                capability_method = getattr(backend, "capabilities", None)
                if not callable(capability_method):
                    raise BackendError("backend does not declare capabilities")
                declared = capability_method()
                if not isinstance(declared, BackendCapabilities):
                    raise BackendError("backend returned an invalid capability declaration")
                item = declared.to_dict()
                item["registered_name"] = registered_name
                item["declaration_ok"] = True
                item["match"] = self._matches(declared, requested)
                item["mismatch_reasons"] = self._mismatches(declared, requested)
            except Exception as exc:
                item = {
                    "registered_name": registered_name,
                    "runtime": registered_name,
                    "routes": [],
                    "supports_resume": False,
                    "supports_streaming": False,
                    "supports_cancel": False,
                    "supports_reasoning_effort": False,
                    "observability": "unknown",
                    "declaration_ok": False,
                    "match": False,
                    "mismatch_reasons": ["capability_declaration_unavailable"],
                    "error_type": type(exc).__name__,
                }
            if item["match"]:
                matches.append(registered_name)
            entries.append(item)
        return {
            "requirements": requested.to_dict(),
            "backends": entries,
            "matches": matches,
            "match_count": len(matches),
            "selection_performed": False,
            "dispatch_performed": False,
        }

    @staticmethod
    def _mismatches(
        capabilities: BackendCapabilities,
        requirements: CapabilityRequirements,
    ) -> list[str]:
        reasons: list[str] = []
        if requirements.runtime and capabilities.runtime.lower() != requirements.runtime:
            reasons.append("runtime")
        routes = {route.lower() for route in capabilities.routes}
        if requirements.route and requirements.route not in routes:
            reasons.append("route")
        if requirements.require_resume and not capabilities.supports_resume:
            reasons.append("resume")
        if requirements.require_streaming and not capabilities.supports_streaming:
            reasons.append("streaming")
        if requirements.require_cancel and not capabilities.supports_cancel:
            reasons.append("cancel")
        if requirements.require_reasoning_effort and not capabilities.supports_reasoning_effort:
            reasons.append("reasoning_effort")
        return reasons

    @classmethod
    def _matches(
        cls,
        capabilities: BackendCapabilities,
        requirements: CapabilityRequirements,
    ) -> bool:
        return not cls._mismatches(capabilities, requirements)
