"""Operator-owned advisory model-routing metadata and provenance.

This module never scores or selects models.  It validates an operator-maintained
routing profile file, verifies optional trusted local evidence by path/hash, and
provides a deliberate bootstrap from the reviewed baseline shipped with
TP-Voyager.  Dispatch authorization remains owned by ``config.json.dispatch``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from agent_runtime.configuration import VoyagerUserConfig, VoyagerUserConfigError
from .model_evaluation import (
    ModelEvaluationError, ModelEvaluationSourceRegistry, validate_evidence_collection,
)
from .model_scorecard import (
    ModelScorecardError, build_scorecard, load_tier_rules, validate_scorecard, validate_scorecard_binding,
)


_SCHEMA_V1 = "tp-voyager.model_routing_profiles/v1"
_SCHEMA_V2 = "tp-voyager.model_routing_profiles/v2"
_SCHEMA = _SCHEMA_V2
_EVALUATION_STANDARD = "tp-voyager.model_evaluation/v1"
_FILE_NAME = "model_routing_profiles.json"
_BASELINE_FILE = "model_routing_profiles.baseline.json"
_TOP_LEVEL_KEYS_V1 = frozenset({"schema", "updated_at", "profiles"})
_TOP_LEVEL_KEYS_V2 = frozenset({"schema", "updated_at", "evaluation_standard", "tier_rules_status", "profiles"})
_PROFILE_KEYS = frozenset(
    {
        "canonical_family",
        "provider_identity",
        "capability_tier",
        "legacy_capability_tier",
        "tier_authority",
        "provider_tier_label",
        "scorecard",
        "standard_evidence",
        "profile_confidence",
        "specialties",
        "recommended_tasks",
        "risk_boundaries",
        "suggested_effort",
        "benchmark_evidence",
        "evidence_refs",
        # v1.0.6-rc compatibility: URL-only evidence from the first draft.
        "evidence_sources",
    }
)
_BENCHMARK_KEYS = frozenset(
    {
        "evidence_schema", "source", "release", "tested_model", "model_match", "metrics", "url",
        "agent", "effort", "harness",
    }
)
_ROUTE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}:[A-Za-z0-9][A-Za-z0-9._:+/-]{0,159}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,159}$")
_SHORT_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_EFFORT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_CONFIDENCE = frozenset({"high", "medium-high", "medium", "medium-low", "low"})
_MODEL_MATCH = frozenset({"exact", "near_exact", "family", "predecessor", "dynamic_tier", "missing"})
_EVIDENCE_REF_KINDS = frozenset({"url", "trusted_file"})
_CAPABILITY_TIERS = frozenset({"L0", "L1", "L2", "L3", "UNCLASSIFIED", "DYNAMIC"})
_TIER_AUTHORITIES = frozenset({"standard_v1", "standard_v1_uncalibrated", "provider_dynamic"})
_RETIRED_ROUTES = frozenset({"qoder:auto"})
_DYNAMIC_LABELS = {"qoder:ultimate": "Ultimate", "qoder:performance": "Performance", "qoder:efficient": "Efficient", "qoder:lite": "Lite"}
_MAX_PROFILES = 256
_MAX_LIST_ITEMS = 32
_MAX_TEXT_LENGTH = 320
_MAX_SOURCE_LENGTH = 800
_MAX_BENCHMARKS = 24
_MAX_METRICS = 32
_MAX_EVIDENCE_FILE_BYTES = 16 * 1024 * 1024


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


def _optional_short_text(value: object, field: str, *, limit: int = 160) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelRoutingProfileError(f"{field} must be a string")
    text = value.strip()
    if not text or len(text) > limit or "\x00" in text:
        raise ModelRoutingProfileError(f"{field} contains an invalid value")
    return text


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


def _relative_evidence_path(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ModelRoutingProfileError(f"{field} must be a relative path")
    raw = value.strip().replace("\\", "/")
    if not raw or len(raw) > 512 or raw.startswith(("/", "//")) or "\x00" in raw:
        raise ModelRoutingProfileError(f"{field} must be a safe relative path")
    if len(raw) >= 2 and raw[1] == ":":
        raise ModelRoutingProfileError(f"{field} must be a safe relative path")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ModelRoutingProfileError(f"{field} must be a safe relative path")
    return path.as_posix()


def _metric_value(value: object, field: str) -> object:
    if isinstance(value, bool):
        raise ModelRoutingProfileError(f"{field} contains an invalid metric")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and len(text) <= 120 and "\x00" not in text:
            return text
    if value is None:
        return None
    raise ModelRoutingProfileError(f"{field} contains an invalid metric")


def _benchmark_evidence(value: object, field: str) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > _MAX_BENCHMARKS:
        raise ModelRoutingProfileError(f"{field} must be a bounded list")
    output: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict) or set(item) - _BENCHMARK_KEYS:
            raise ModelRoutingProfileError(f"{item_field} contains unsupported fields")
        source = _optional_short_text(item.get("source"), f"{item_field}.source", limit=80)
        tested_model = _optional_short_text(item.get("tested_model"), f"{item_field}.tested_model", limit=200)
        model_match = _optional_short_text(item.get("model_match"), f"{item_field}.model_match", limit=32)
        if source is None or not _SHORT_TOKEN.fullmatch(source):
            raise ModelRoutingProfileError(f"{item_field}.source must be a short token")
        if tested_model is None:
            raise ModelRoutingProfileError(f"{item_field}.tested_model is required")
        if model_match not in _MODEL_MATCH:
            raise ModelRoutingProfileError(f"{item_field}.model_match is unsupported")
        url = _optional_short_text(item.get("url"), f"{item_field}.url", limit=_MAX_SOURCE_LENGTH)
        if url is not None and not url.startswith(("https://", "http://")):
            raise ModelRoutingProfileError(f"{item_field}.url must be http(s)")
        metrics = item.get("metrics", {})
        if not isinstance(metrics, dict) or len(metrics) > _MAX_METRICS:
            raise ModelRoutingProfileError(f"{item_field}.metrics must be a bounded object")
        safe_metrics: dict[str, object] = {}
        for key, metric in metrics.items():
            if not isinstance(key, str) or not _SHORT_TOKEN.fullmatch(key):
                raise ModelRoutingProfileError(f"{item_field}.metrics contains an invalid key")
            safe_metrics[key] = _metric_value(metric, f"{item_field}.metrics.{key}")
        normalized = {
            "evidence_schema": _optional_short_text(item.get("evidence_schema"), f"{item_field}.evidence_schema", limit=80),
            "source": source,
            "release": _optional_short_text(item.get("release"), f"{item_field}.release", limit=80),
            "tested_model": tested_model,
            "model_match": model_match,
            "metrics": safe_metrics,
            "url": url,
            "agent": _optional_short_text(item.get("agent"), f"{item_field}.agent", limit=120),
            "effort": _optional_short_text(item.get("effort"), f"{item_field}.effort", limit=80),
            "harness": _optional_short_text(item.get("harness"), f"{item_field}.harness", limit=160),
        }
        output.append({key: val for key, val in normalized.items() if val is not None})
    return tuple(output)


def _standard_evidence(value: object, field: str) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 256:
        raise ModelRoutingProfileError(f"{field} must be a bounded list")
    try:
        registry = ModelEvaluationSourceRegistry.load_bundled()
        return tuple(validate_evidence_collection(value, registry))
    except ModelEvaluationError as exc:
        raise ModelRoutingProfileError(f"{field} is invalid: {exc}") from exc


def _scorecard(value: object, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        return validate_scorecard(value)
    except ModelScorecardError as exc:
        raise ModelRoutingProfileError(f"{field} is invalid: {exc}") from exc


def _declared_ref_for_persistence(ref: Mapping[str, Any]) -> dict[str, str]:
    if ref.get("kind") == "url":
        return {"kind": "url", "url": str(ref["url"])}
    return {
        "kind": "trusted_file",
        "root_alias": str(ref["root_alias"]),
        "path": str(ref["path"]),
        "sha256": str(ref["sha256"]),
    }


def _declared_evidence_refs(value: object, field: str) -> tuple[dict[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > _MAX_LIST_ITEMS:
        raise ModelRoutingProfileError(f"{field} must be a bounded list")
    output: list[dict[str, str]] = []
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            raise ModelRoutingProfileError(f"{item_field} must be an object")
        kind = str(item.get("kind") or "").strip()
        if kind not in _EVIDENCE_REF_KINDS:
            raise ModelRoutingProfileError(f"{item_field}.kind is unsupported")
        if kind == "url":
            if set(item) != {"kind", "url"}:
                raise ModelRoutingProfileError(f"{item_field} url evidence has unsupported fields")
            url = str(item.get("url") or "").strip()
            if not url.startswith(("https://", "http://")) or len(url) > _MAX_SOURCE_LENGTH:
                raise ModelRoutingProfileError(f"{item_field}.url must be http(s)")
            output.append({"kind": "url", "url": url})
            continue
        if set(item) != {"kind", "root_alias", "path", "sha256"}:
            raise ModelRoutingProfileError(f"{item_field} trusted_file evidence has unsupported fields")
        alias = str(item.get("root_alias") or "").strip()
        digest = str(item.get("sha256") or "").strip().lower()
        if not _SHORT_TOKEN.fullmatch(alias):
            raise ModelRoutingProfileError(f"{item_field}.root_alias is invalid")
        if not _SHA256.fullmatch(digest):
            raise ModelRoutingProfileError(f"{item_field}.sha256 is invalid")
        output.append({
            "kind": "trusted_file",
            "root_alias": alias,
            "path": _relative_evidence_path(item.get("path"), f"{item_field}.path"),
            "sha256": digest,
        })
    return tuple(output)


def _load_evidence_roots(runtime_home: Path) -> tuple[dict[str, Path], dict[str, Any]]:
    try:
        config = VoyagerUserConfig.load(runtime_home)
        configured = config.trusted_roots.model_evidence_map()
        roots = {alias: Path(value).expanduser().resolve() for alias, value in configured.items()}
        config_path = runtime_home / "config.json"
        digest = hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.is_file() else None
        return roots, {
            "status": "loaded" if roots else "not_configured",
            "source": "config.json",
            "sha256": digest,
            "root_count": len(roots),
        }
    except (VoyagerUserConfigError, OSError) as exc:
        return {}, {
            "status": "invalid",
            "source": "config.json",
            "sha256": None,
            "root_count": 0,
            "error": type(exc).__name__,
        }


def _verify_evidence_refs(
    declared: tuple[dict[str, str], ...], roots: Mapping[str, Path]
) -> tuple[tuple[dict[str, Any], ...], str]:
    output: list[dict[str, Any]] = []
    states: list[str] = []
    for ref in declared:
        if ref["kind"] == "url":
            output.append({**ref, "verification": "declared"})
            states.append("declared")
            continue
        alias = ref["root_alias"]
        root = roots.get(alias)
        if root is None:
            output.append({**ref, "verification": "root_unavailable"})
            states.append("root_unavailable")
            continue
        candidate = (root / Path(*PurePosixPath(ref["path"]).parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            output.append({**ref, "verification": "path_escape"})
            states.append("path_escape")
            continue
        if not candidate.is_file():
            output.append({**ref, "verification": "missing"})
            states.append("missing")
            continue
        try:
            size = candidate.stat().st_size
            if size > _MAX_EVIDENCE_FILE_BYTES:
                output.append({**ref, "verification": "too_large", "byte_size": size})
                states.append("too_large")
                continue
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            output.append({**ref, "verification": "unreadable"})
            states.append("unreadable")
            continue
        verification = "verified" if actual == ref["sha256"] else "hash_mismatch"
        output.append({
            **ref,
            "verification": verification,
            "byte_size": size,
            "actual_sha256": actual,
        })
        states.append(verification)
    if not states:
        return tuple(output), "not_declared"
    if any(state in {"hash_mismatch", "missing"} for state in states):
        return tuple(output), "stale"
    if any(state in {"path_escape", "too_large", "unreadable"} for state in states):
        return tuple(output), "rejected"
    if any(state == "root_unavailable" for state in states):
        return tuple(output), "unverified"
    if any(state == "verified" for state in states):
        return tuple(output), "verified"
    return tuple(output), "declared"


@dataclass(frozen=True)
class ModelRoutingProfile:
    route_id: str
    canonical_family: str | None = None
    provider_identity: str | None = None
    capability_tier: str | None = None
    legacy_capability_tier: str | None = None
    tier_authority: str | None = None
    provider_tier_label: str | None = None
    scorecard: dict[str, Any] | None = None
    standard_evidence: tuple[dict[str, Any], ...] = ()
    profile_confidence: str | None = None
    specialties: tuple[str, ...] = ()
    recommended_tasks: tuple[str, ...] = ()
    risk_boundaries: tuple[str, ...] = ()
    suggested_effort: str | None = None
    benchmark_evidence: tuple[dict[str, Any], ...] = ()
    evidence_refs: tuple[dict[str, Any], ...] = ()
    evidence_status: str = "not_declared"
    evidence_sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_family": self.canonical_family,
            "provider_identity": self.provider_identity,
            "capability_tier": self.capability_tier,
            "legacy_capability_tier": self.legacy_capability_tier,
            "tier_authority": self.tier_authority,
            "provider_tier_label": self.provider_tier_label,
            "scorecard": json.loads(json.dumps(self.scorecard)) if self.scorecard is not None else None,
            "standard_evidence": [json.loads(json.dumps(item)) for item in self.standard_evidence],
            "profile_confidence": self.profile_confidence,
            "specialties": list(self.specialties),
            "recommended_tasks": list(self.recommended_tasks),
            "risk_boundaries": list(self.risk_boundaries),
            "suggested_effort": self.suggested_effort,
            "benchmark_evidence": [dict(item) for item in self.benchmark_evidence],
            "evidence_refs": [dict(item) for item in self.evidence_refs],
            "evidence_status": self.evidence_status,
            "evidence_sources": list(self.evidence_sources),
        }


@dataclass(frozen=True)
class ModelRoutingProfiles:
    profiles: tuple[ModelRoutingProfile, ...] = ()
    status: str = "not_configured"
    schema: str | None = None
    normalized_schema: str = _SCHEMA_V2
    evaluation_standard: str = _EVALUATION_STANDARD
    tier_rules_status: str = "uncalibrated"
    retired_routes: tuple[str, ...] = ()
    sha256: str | None = None
    updated_at: str | None = None
    evidence_roots: dict[str, Any] | None = None
    source: str = "operator_model_routing_profiles"

    @property
    def profile_count(self) -> int:
        return len(self.profiles)

    @classmethod
    def bundled_baseline_path(cls) -> Path:
        return Path(__file__).with_name(_BASELINE_FILE)

    @classmethod
    def initialize(cls, runtime_home: str | Path) -> dict[str, Any]:
        """Install the reviewed baseline into Runtime Home without overwriting operator data."""
        home = Path(runtime_home).expanduser().resolve()
        target = home / _FILE_NAME
        if target.exists():
            raise ModelRoutingProfileError("model_routing_profiles.json already exists; refusing to overwrite operator data")
        source = cls.bundled_baseline_path()
        if not source.is_file():
            raise ModelRoutingProfileError("bundled model routing baseline is unavailable")
        data = source.read_bytes()
        # Validate before writing.  Missing evidence roots are advisory only.
        cls._from_bytes(data, home)
        home.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            temp.write_bytes(data)
            os.replace(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        loaded = cls.load(home)
        required_aliases = sorted({
            str(ref.get("root_alias"))
            for profile in loaded.profiles
            for ref in profile.evidence_refs
            if ref.get("kind") == "trusted_file" and ref.get("root_alias")
        })
        return {
            "schema": _SCHEMA,
            "status": "installed",
            "target": str(target),
            "sha256": loaded.sha256,
            "profile_count": loaded.profile_count,
            "updated_at": loaded.updated_at,
            "evidence_roots_config": str(home / "config.json"),
            "required_evidence_root_aliases": required_aliases,
            "selection_performed": False,
            "dispatch_performed": False,
        }

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
        evidence_counts: dict[str, int] = {}
        for profile in self.profiles:
            evidence_counts[profile.evidence_status] = evidence_counts.get(profile.evidence_status, 0) + 1
        return {
            "status": self.status,
            "source": self.source,
            "sha256": self.sha256,
            "updated_at": self.updated_at,
            "profile_count": self.profile_count,
            "schema": self.schema,
            "normalized_schema": self.normalized_schema,
            "evaluation_standard": self.evaluation_standard,
            "tier_rules_status": self.tier_rules_status,
            "retired_routes": list(self.retired_routes),
            "advisory_only": True,
            "evidence_roots": dict(self.evidence_roots or {}),
            "evidence_profile_counts": evidence_counts,
        }

    @classmethod
    def load(cls, runtime_home: str | Path) -> "ModelRoutingProfiles":
        home = Path(runtime_home).expanduser().resolve()
        path = home / _FILE_NAME
        if not path.exists():
            baseline = cls.bundled_baseline_path()
            if not baseline.is_file():
                return cls()
            try:
                return cls._from_bytes(
                    baseline.read_bytes(), home,
                    status="bundled_baseline", source="bundled_model_routing_baseline",
                )
            except OSError as exc:
                raise ModelRoutingProfileError("bundled model routing baseline is invalid") from exc
        try:
            data = path.read_bytes()
            return cls._from_bytes(data, home, status="loaded", source="operator_model_routing_profiles")
        except ModelRoutingProfileError:
            raise
        except OSError as exc:
            raise ModelRoutingProfileError("model routing profiles are invalid") from exc

    @classmethod
    def _from_bytes(
        cls, data: bytes, runtime_home: Path, *,
        status: str = "loaded", source: str = "operator_model_routing_profiles",
    ) -> "ModelRoutingProfiles":
        try:
            raw = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
        except ModelRoutingProfileError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelRoutingProfileError("model routing profiles are invalid") from exc
        if not isinstance(raw, dict):
            raise ModelRoutingProfileError("model routing profiles schema is invalid")
        schema = raw.get("schema")
        if schema not in {_SCHEMA_V1, _SCHEMA_V2}:
            raise ModelRoutingProfileError("model routing profiles schema is unsupported")
        allowed_top = _TOP_LEVEL_KEYS_V1 if schema == _SCHEMA_V1 else _TOP_LEVEL_KEYS_V2
        if set(raw) - allowed_top:
            raise ModelRoutingProfileError("model routing profiles schema is invalid")
        if schema == _SCHEMA_V2:
            if raw.get("evaluation_standard") != _EVALUATION_STANDARD:
                raise ModelRoutingProfileError("evaluation_standard is unsupported")
            tier_rules_status = str(raw.get("tier_rules_status") or "").strip()
            if tier_rules_status not in {"uncalibrated", "calibrated"}:
                raise ModelRoutingProfileError("tier_rules_status is unsupported")
        else:
            tier_rules_status = "uncalibrated"
        updated_at = raw.get("updated_at")
        if updated_at is not None and (not isinstance(updated_at, str) or not updated_at.strip() or len(updated_at) > 64):
            raise ModelRoutingProfileError("updated_at must be a short non-empty string")
        raw_profiles = raw.get("profiles")
        if not isinstance(raw_profiles, dict) or len(raw_profiles) > _MAX_PROFILES:
            raise ModelRoutingProfileError("profiles must be a bounded object")

        roots, roots_meta = _load_evidence_roots(runtime_home)
        profiles: list[ModelRoutingProfile] = []
        retired_routes: list[str] = []
        for route_id, value in raw_profiles.items():
            if route_id in _RETIRED_ROUTES:
                retired_routes.append(route_id)
                continue
            if not isinstance(route_id, str) or not _ROUTE_ID.fullmatch(route_id):
                raise ModelRoutingProfileError("profiles contains an invalid backend-qualified route id")
            backend, _, _ = route_id.partition(":")
            if backend != backend.lower():
                raise ModelRoutingProfileError("profile backend must be lowercase")
            if not isinstance(value, dict) or set(value) - _PROFILE_KEYS:
                raise ModelRoutingProfileError(f"profile {route_id} contains unsupported fields")

            confidence = value.get("profile_confidence")
            if confidence is not None:
                if not isinstance(confidence, str) or confidence.strip() not in _CONFIDENCE:
                    raise ModelRoutingProfileError(f"{route_id}.profile_confidence is unsupported")
                confidence = confidence.strip()
            canonical_family = _optional_token(value.get("canonical_family"), f"{route_id}.canonical_family")
            provider_identity = _optional_token(value.get("provider_identity"), f"{route_id}.provider_identity")
            raw_capability = _optional_token(value.get("capability_tier"), f"{route_id}.capability_tier")
            is_dynamic = provider_identity == "dynamic_tier"

            evidence_sources = _bounded_strings(value.get("evidence_sources"), f"{route_id}.evidence_sources", source=True)
            declared_refs = list(_declared_evidence_refs(value.get("evidence_refs"), f"{route_id}.evidence_refs"))
            declared_refs.extend({"kind": "url", "url": url} for url in evidence_sources)
            verified_refs, evidence_status = _verify_evidence_refs(tuple(declared_refs), roots)
            legacy_benchmarks = list(_benchmark_evidence(value.get("benchmark_evidence"), f"{route_id}.benchmark_evidence"))
            if schema == _SCHEMA_V1:
                legacy_benchmarks = [
                    ({"evidence_schema": "legacy_v1", **item} if "evidence_schema" not in item else item)
                    for item in legacy_benchmarks
                ]
                legacy_capability = raw_capability
                if is_dynamic:
                    capability_tier = "DYNAMIC"
                    tier_authority = "provider_dynamic"
                    provider_tier_label = _DYNAMIC_LABELS.get(route_id, route_id.split(":", 1)[1])
                else:
                    capability_tier = "UNCLASSIFIED"
                    tier_authority = "standard_v1_uncalibrated"
                    provider_tier_label = None
                scorecard = None
                standard_evidence: tuple[dict[str, Any], ...] = ()
            else:
                capability_tier = raw_capability
                if capability_tier not in _CAPABILITY_TIERS:
                    raise ModelRoutingProfileError(f"{route_id}.capability_tier is unsupported")
                legacy_capability = _optional_token(value.get("legacy_capability_tier"), f"{route_id}.legacy_capability_tier")
                tier_authority = _optional_token(value.get("tier_authority"), f"{route_id}.tier_authority")
                provider_tier_label = _optional_short_text(value.get("provider_tier_label"), f"{route_id}.provider_tier_label", limit=120)
                scorecard = _scorecard(value.get("scorecard"), f"{route_id}.scorecard")
                standard_evidence = _standard_evidence(value.get("standard_evidence"), f"{route_id}.standard_evidence")
                if tier_authority not in _TIER_AUTHORITIES:
                    raise ModelRoutingProfileError(f"{route_id}.tier_authority is unsupported")
                if is_dynamic:
                    if capability_tier != "DYNAMIC" or tier_authority != "provider_dynamic" or not provider_tier_label or scorecard is not None:
                        raise ModelRoutingProfileError(f"{route_id} dynamic tier semantics are invalid")
                else:
                    if scorecard is None:
                        if capability_tier != "UNCLASSIFIED" or tier_authority != "standard_v1_uncalibrated":
                            raise ModelRoutingProfileError(f"{route_id} uncalibrated tier semantics are invalid")
                    else:
                        if tier_rules_status != "calibrated" or scorecard.get("rules_status") != "calibrated":
                            raise ModelRoutingProfileError(f"{route_id} persisted scorecard requires calibrated rules")
                        if tier_authority != "standard_v1" or capability_tier != scorecard.get("tier"):
                            raise ModelRoutingProfileError(f"{route_id} tier authority conflicts with persisted scorecard")
                        try:
                            registry = ModelEvaluationSourceRegistry.load_bundled()
                            tier_rules = load_tier_rules()
                            validate_scorecard_binding(
                                scorecard,
                                canonical_family=canonical_family or "",
                                evidence=standard_evidence,
                                registry=registry,
                                tier_rules=tier_rules,
                            )
                            rebuilt = build_scorecard(
                                canonical_family or "",
                                standard_evidence,
                                registry,
                                tier_rules,
                                evaluated_at=str(scorecard.get("evaluated_at") or ""),
                            )
                            if rebuilt != scorecard:
                                raise ModelScorecardError(
                                    "persisted scorecard derived output differs from deterministic rebuild"
                                )
                        except (ModelScorecardError, ModelEvaluationError) as exc:
                            raise ModelRoutingProfileError(
                                f"{route_id} scorecard binding is invalid: {exc}"
                            ) from exc

            profiles.append(
                ModelRoutingProfile(
                    route_id=route_id,
                    canonical_family=canonical_family,
                    provider_identity=provider_identity,
                    capability_tier=capability_tier,
                    legacy_capability_tier=legacy_capability,
                    tier_authority=tier_authority,
                    provider_tier_label=provider_tier_label,
                    scorecard=scorecard,
                    standard_evidence=standard_evidence,
                    profile_confidence=confidence,
                    specialties=_bounded_strings(value.get("specialties"), f"{route_id}.specialties"),
                    recommended_tasks=_bounded_strings(value.get("recommended_tasks"), f"{route_id}.recommended_tasks"),
                    risk_boundaries=_bounded_strings(value.get("risk_boundaries"), f"{route_id}.risk_boundaries"),
                    suggested_effort=_optional_token(value.get("suggested_effort"), f"{route_id}.suggested_effort", effort=True),
                    benchmark_evidence=tuple(legacy_benchmarks),
                    evidence_refs=verified_refs,
                    evidence_status=evidence_status,
                    evidence_sources=evidence_sources,
                )
            )
        profiles.sort(key=lambda item: item.route_id)
        return cls(
            profiles=tuple(profiles),
            status=status,
            schema=str(schema),
            normalized_schema=_SCHEMA_V2,
            evaluation_standard=_EVALUATION_STANDARD,
            tier_rules_status=tier_rules_status,
            retired_routes=tuple(sorted(retired_routes)),
            sha256=hashlib.sha256(data).hexdigest(),
            updated_at=updated_at.strip() if isinstance(updated_at, str) else None,
            evidence_roots=roots_meta,
            source=source,
        )

    @classmethod
    def migrate(cls, runtime_home: str | Path, *, write: bool = False) -> dict[str, Any]:
        """Normalize a materialized v1 operator file to v2 without implicit writes."""
        home = Path(runtime_home).expanduser().resolve()
        path = home / _FILE_NAME
        if not path.is_file():
            raise ModelRoutingProfileError("model_routing_profiles.json does not exist")
        data = path.read_bytes()
        try:
            raw = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelRoutingProfileError("model routing profiles are invalid") from exc
        if not isinstance(raw, dict):
            raise ModelRoutingProfileError("model routing profiles are invalid")
        source_schema = raw.get("schema")
        if source_schema == _SCHEMA_V2:
            cls._from_bytes(data, home)
            return {
                "status": "already_v2", "source_schema": _SCHEMA_V2,
                "target_schema": _SCHEMA_V2, "written": False,
                "retired_routes": [], "profile_count": len(raw.get("profiles") or {}),
            }
        if source_schema != _SCHEMA_V1:
            raise ModelRoutingProfileError("model routing profiles schema is unsupported")
        # Validate legacy before constructing persistent v2 form.
        legacy_view = cls._from_bytes(data, home)
        raw_profiles = raw.get("profiles")
        assert isinstance(raw_profiles, dict)
        migrated_profiles: dict[str, Any] = {}
        for route_id, original in raw_profiles.items():
            if route_id in _RETIRED_ROUTES:
                continue
            assert isinstance(original, dict)
            item = json.loads(json.dumps(original))
            old_tier = item.get("capability_tier")
            provider_identity = item.get("provider_identity")
            if old_tier is not None:
                item["legacy_capability_tier"] = old_tier
            if provider_identity == "dynamic_tier":
                item["capability_tier"] = "DYNAMIC"
                item["tier_authority"] = "provider_dynamic"
                item["provider_tier_label"] = _DYNAMIC_LABELS.get(route_id, route_id.split(":", 1)[1])
            else:
                item["capability_tier"] = "UNCLASSIFIED"
                item["tier_authority"] = "standard_v1_uncalibrated"
            item["scorecard"] = None
            item["standard_evidence"] = []
            benchmarks = item.get("benchmark_evidence")
            if isinstance(benchmarks, list):
                item["benchmark_evidence"] = [
                    ({"evidence_schema": "legacy_v1", **entry} if isinstance(entry, dict) and "evidence_schema" not in entry else entry)
                    for entry in benchmarks
                ]
            migrated_profiles[route_id] = item
        migrated = {
            "schema": _SCHEMA_V2,
            "updated_at": raw.get("updated_at"),
            "evaluation_standard": _EVALUATION_STANDARD,
            "tier_rules_status": "uncalibrated",
            "profiles": migrated_profiles,
        }
        if migrated["updated_at"] is None:
            migrated.pop("updated_at")
        encoded = (json.dumps(migrated, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        cls._from_bytes(encoded, home)
        result = {
            "status": "migration_ready" if not write else "migrated",
            "source_schema": _SCHEMA_V1,
            "target_schema": _SCHEMA_V2,
            "written": bool(write),
            "profile_count": len(migrated_profiles),
            "legacy_profile_count": len(raw_profiles),
            "retired_routes": sorted(set(raw_profiles) & _RETIRED_ROUTES),
            "semantic_preservation": {
                "recommended_tasks": True,
                "risk_boundaries": True,
                "suggested_effort": True,
                "benchmark_raw_values": True,
                "evidence_refs": True,
            },
        }
        if not write:
            return result
        temp = path.with_name(f".{path.name}.{os.getpid()}.migrate.tmp")
        try:
            with temp.open("wb") as fh:
                fh.write(encoded)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            reloaded = cls.load(home)
            if reloaded.schema != _SCHEMA_V2:
                raise ModelRoutingProfileError("migration post-write validation failed")
        except Exception:
            # The target had already been atomically replaced. Restore the exact
            # original v1 bytes so any post-write validation failure remains
            # failure-atomic from the operator's perspective.
            rollback = path.with_name(f".{path.name}.{os.getpid()}.rollback.tmp")
            try:
                with rollback.open("wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(rollback, path)
            except OSError as rollback_exc:
                raise ModelRoutingProfileError(
                    "migration post-write validation failed and original file could not be restored"
                ) from rollback_exc
            finally:
                try:
                    rollback.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        result["sha256"] = reloaded.sha256
        return result
