"""Operator-owned advisory model-routing metadata.

The file loaded here is deliberately *not* a dispatch policy.  It gives the
Captain maintainable model knowledge (capability tier, suitable work, risk
boundaries and an effort suggestion) while authorization remains owned by
``dispatch_model_policy.json`` and explicit Captain dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


_SCHEMA = "tp-voyager.model_routing_profiles/v1"
_FILE_NAME = "model_routing_profiles.json"
_TOP_LEVEL_KEYS = frozenset({"schema", "updated_at", "profiles"})
_PROFILE_KEYS = frozenset(
    {
        "canonical_family",
        "capability_tier",
        "recommended_tasks",
        "risk_boundaries",
        "suggested_effort",
        "evidence_sources",
    }
)
_ROUTE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}:[A-Za-z0-9][A-Za-z0-9._:+/-]{0,159}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,159}$")
_EFFORT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_MAX_PROFILES = 256
_MAX_LIST_ITEMS = 32
_MAX_TEXT_LENGTH = 240
_MAX_SOURCE_LENGTH = 500


class ModelRoutingProfileError(ValueError):
    """Operator routing-profile configuration is malformed or unsafe."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ModelRoutingProfileError(f"model routing profiles contain duplicate key: {key}")
        output[key] = value
    return output


def _optional_token(value: object, field: str, *, effort: bool = False) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelRoutingProfileError(f"{field} must be a string")
    normalized = value.strip()
    pattern = _EFFORT if effort else _TOKEN
    if not normalized or not pattern.fullmatch(normalized):
        raise ModelRoutingProfileError(f"{field} contains an invalid token")
    return normalized


def _bounded_strings(value: object, field: str, *, source: bool = False) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > _MAX_LIST_ITEMS:
        raise ModelRoutingProfileError(f"{field} must be a bounded list")
    limit = _MAX_SOURCE_LENGTH if source else _MAX_TEXT_LENGTH
    output: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ModelRoutingProfileError(f"{field} must contain only strings")
        text = item.strip()
        if not text or len(text) > limit or text in output:
            raise ModelRoutingProfileError(f"{field} contains an invalid or duplicate value")
        if source and not text.startswith(("https://", "http://")):
            raise ModelRoutingProfileError(f"{field} must contain http(s) evidence URLs")
        output.append(text)
    return tuple(output)


@dataclass(frozen=True)
class ModelRoutingProfile:
    route_id: str
    canonical_family: str | None = None
    capability_tier: str | None = None
    recommended_tasks: tuple[str, ...] = ()
    risk_boundaries: tuple[str, ...] = ()
    suggested_effort: str | None = None
    evidence_sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_family": self.canonical_family,
            "capability_tier": self.capability_tier,
            "recommended_tasks": list(self.recommended_tasks),
            "risk_boundaries": list(self.risk_boundaries),
            "suggested_effort": self.suggested_effort,
            "evidence_sources": list(self.evidence_sources),
        }


@dataclass(frozen=True)
class ModelRoutingProfiles:
    profiles: tuple[ModelRoutingProfile, ...] = ()
    status: str = "not_configured"
    sha256: str | None = None
    updated_at: str | None = None

    @property
    def profile_count(self) -> int:
        return len(self.profiles)

    def get(self, route_id: str) -> dict[str, Any] | None:
        for profile in self.profiles:
            if profile.route_id == route_id:
                return profile.to_dict()
        return None

    def route_ids(self, backend: str = "") -> tuple[str, ...]:
        normalized = str(backend or "").strip().lower()
        prefix = f"{normalized}:" if normalized else ""
        return tuple(
            profile.route_id
            for profile in self.profiles
            if not prefix or profile.route_id.startswith(prefix)
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source": "operator_model_routing_profiles",
            "sha256": self.sha256,
            "updated_at": self.updated_at,
            "profile_count": self.profile_count,
            "advisory_only": True,
        }

    @classmethod
    def load(cls, runtime_home: str | Path) -> "ModelRoutingProfiles":
        path = Path(runtime_home) / _FILE_NAME
        if not path.exists():
            return cls()
        try:
            data = path.read_bytes()
            raw = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
        except ModelRoutingProfileError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelRoutingProfileError("model routing profiles are invalid") from exc
        if not isinstance(raw, dict) or set(raw) - _TOP_LEVEL_KEYS:
            raise ModelRoutingProfileError("model routing profiles schema is invalid")
        if raw.get("schema") != _SCHEMA:
            raise ModelRoutingProfileError("model routing profiles schema is unsupported")
        updated_at = raw.get("updated_at")
        if updated_at is not None and (not isinstance(updated_at, str) or not updated_at.strip() or len(updated_at) > 64):
            raise ModelRoutingProfileError("updated_at must be a short non-empty string")
        raw_profiles = raw.get("profiles")
        if not isinstance(raw_profiles, dict) or len(raw_profiles) > _MAX_PROFILES:
            raise ModelRoutingProfileError("profiles must be a bounded object")
        profiles: list[ModelRoutingProfile] = []
        for route_id, value in raw_profiles.items():
            if not isinstance(route_id, str) or not _ROUTE_ID.fullmatch(route_id):
                raise ModelRoutingProfileError("profiles contains an invalid backend-qualified route id")
            backend, _, _ = route_id.partition(":")
            if backend != backend.lower():
                raise ModelRoutingProfileError("profile backend must be lowercase")
            if not isinstance(value, dict) or set(value) - _PROFILE_KEYS:
                raise ModelRoutingProfileError(f"profile {route_id} contains unsupported fields")
            profiles.append(
                ModelRoutingProfile(
                    route_id=route_id,
                    canonical_family=_optional_token(value.get("canonical_family"), f"{route_id}.canonical_family"),
                    capability_tier=_optional_token(value.get("capability_tier"), f"{route_id}.capability_tier"),
                    recommended_tasks=_bounded_strings(value.get("recommended_tasks"), f"{route_id}.recommended_tasks"),
                    risk_boundaries=_bounded_strings(value.get("risk_boundaries"), f"{route_id}.risk_boundaries"),
                    suggested_effort=_optional_token(value.get("suggested_effort"), f"{route_id}.suggested_effort", effort=True),
                    evidence_sources=_bounded_strings(value.get("evidence_sources"), f"{route_id}.evidence_sources", source=True),
                )
            )
        profiles.sort(key=lambda item: item.route_id)
        return cls(
            profiles=tuple(profiles),
            status="loaded",
            sha256=hashlib.sha256(data).hexdigest(),
            updated_at=updated_at.strip() if isinstance(updated_at, str) else None,
        )
