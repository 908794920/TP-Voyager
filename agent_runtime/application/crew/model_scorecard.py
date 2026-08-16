"""Persistable Model Evaluation Standard v1 scorecard construction.

Scorecards are generated deliberately during evaluation maintenance and then
stored in routing profiles.  Runtime profile loading validates the persisted
snapshot; it does not recalculate model scores on every read.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .model_evaluation import (
    ModelEvaluationError,
    ModelEvaluationSourceRegistry,
    validate_evidence_collection,
)

SCORECARD_SCHEMA = "tp-voyager.model_scorecard/v1"
BUILDER_VERSION = "tp-voyager.model_scorecard_builder/v1"
TIER_RULES_SCHEMA = "tp-voyager.model_tier_rules/v1"
_RULES_FILE = "model_tier_rules.baseline.json"
_DIMENSIONS = (
    "repository_engineering",
    "terminal_agentic",
    "codebase_understanding",
    "general_coding",
    "multimodal_coding",
)
_TIERS = frozenset({"L0", "L1", "L2", "L3", "UNCLASSIFIED", "DYNAMIC"})


class ModelScorecardError(ValueError):
    """Scorecard or tier rules are invalid."""


def load_tier_rules(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else Path(__file__).with_name(_RULES_FILE)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ModelScorecardError("tier rules are invalid") from exc
    if not isinstance(raw, dict) or raw.get("schema") != TIER_RULES_SCHEMA:
        raise ModelScorecardError("tier rules schema is unsupported")
    if raw.get("status") not in {"uncalibrated", "calibrated"}:
        raise ModelScorecardError("tier rules status is unsupported")
    if tuple(raw.get("dimensions") or ()) != _DIMENSIONS:
        raise ModelScorecardError("tier rules dimensions are invalid")
    if not isinstance(raw.get("benchmark_dimensions"), dict) or not isinstance(raw.get("tiers"), dict):
        raise ModelScorecardError("tier rules are incomplete")
    accepted = raw.get("accepted_versions")
    if not isinstance(accepted, dict):
        raise ModelScorecardError("tier rules accepted_versions are missing")
    for benchmark_id, versions in accepted.items():
        if not isinstance(benchmark_id, str) or not isinstance(versions, list) or not versions or any(not isinstance(v, str) or not v for v in versions):
            raise ModelScorecardError("tier rules accepted_versions are invalid")
    return raw


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evidence_set_sha256(records: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted((dict(item) for item in records), key=lambda item: str(item.get("evidence_id") or ""))
    return _canonical_sha256(ordered)


def source_registry_sha256(registry: ModelEvaluationSourceRegistry) -> str:
    return _canonical_sha256({
        "schema": registry.schema,
        "updated_at": registry.updated_at,
        "sources": registry.sources,
    })


def tier_rules_sha256(tier_rules: Mapping[str, Any]) -> str:
    return _canonical_sha256(dict(tier_rules))


def _date_only(value: str, field: str) -> date:
    try:
        if "T" in value or " " in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        if len(value) == 7 and value[4] == "-":
            return date.fromisoformat(value + "-01")
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ModelScorecardError(f"{field} must be an ISO-8601 date/time") from exc


def _freshness(record: Mapping[str, Any], registry: ModelEvaluationSourceRegistry, evaluated: date) -> tuple[str, int | None, int, str]:
    source = registry.source(str(record.get("source_id") or ""))
    limit = int(source.get("freshness_policy_days") or 0)
    observed = record.get("provenance", {}).get("observed_at") if isinstance(record.get("provenance"), Mapping) else None
    if limit <= 0:
        return "not_applicable", None, limit, "observed_at"
    if not isinstance(observed, str) or not observed:
        return "invalid", None, limit, "observed_at"
    observed_date = _date_only(observed, "provenance.observed_at")
    age = (evaluated - observed_date).days
    if age < 0:
        return "invalid", age, limit, "observed_at"
    if age > limit:
        return "stale", age, limit, "observed_at"
    return "current", age, limit, "observed_at"

def _numeric_percent(record: Mapping[str, Any]) -> float | None:
    result = record.get("result")
    if not isinstance(result, Mapping) or result.get("scale") != "percent":
        return None
    value = result.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0.0 <= number <= 100.0 else None


def _formal_primary(record: Mapping[str, Any]) -> bool:
    return (
        record.get("source_role") == "primary"
        and isinstance(record.get("model"), Mapping)
        and record["model"].get("model_match") == "exact"
        and record.get("subject_type") not in {"provider_claim", "preference"}
        and not (isinstance(record.get("relationships"), Mapping) and record["relationships"].get("duplicate_of"))
    )


def _tier_from_rules(
    measurements: list[dict[str, Any]], dimensions: Mapping[str, Mapping[str, Any]], rules: Mapping[str, Any]
) -> str:
    if rules.get("status") != "calibrated":
        return "UNCLASSIFIED"
    primary = [item for item in measurements if item["primary"]]
    if not primary:
        return "UNCLASSIFIED"
    primary_dims = {item["dimension"] for item in primary}
    primary_families = {item["benchmark_id"] for item in primary}
    has_agentic_or_repo = bool(primary_dims & {"repository_engineering", "terminal_agentic"})
    tiers = rules.get("tiers") or {}
    for tier in ("L3", "L2", "L1", "L0"):
        policy = tiers.get(tier)
        if not isinstance(policy, Mapping):
            continue
        if len(primary_dims) < int(policy.get("minimum_primary_dimensions", 999)):
            continue
        if len(primary_families) < int(policy.get("minimum_primary_families", 999)):
            continue
        if policy.get("requires_agentic_or_repository") and not has_agentic_or_repo:
            continue
        minimums = policy.get("benchmark_minimums") or {}
        passing_families: set[str] = set()
        for item in primary:
            threshold = minimums.get(item["benchmark_id"])
            if threshold is None:
                continue
            value = item.get("numeric_percent")
            if value is not None and value >= float(threshold):
                passing_families.add(item["benchmark_id"])
        required_families = min(int(policy.get("minimum_primary_families", 1)), len(primary_families))
        if len(passing_families) >= required_families:
            return tier
    return "UNCLASSIFIED"


def build_scorecard(
    canonical_family: str,
    evidence: Sequence[Mapping[str, Any]],
    registry: ModelEvaluationSourceRegistry,
    tier_rules: Mapping[str, Any],
    *,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    if not canonical_family or not isinstance(canonical_family, str):
        raise ModelScorecardError("canonical_family is required")
    evaluated_at = evaluated_at or datetime.now(timezone.utc).date().isoformat()
    evaluated_date = _date_only(evaluated_at, "evaluated_at")
    try:
        records = validate_evidence_collection(list(evidence), registry)
    except ModelEvaluationError as exc:
        raise ModelScorecardError(str(exc)) from exc
    benchmark_dimensions = tier_rules.get("benchmark_dimensions") or {}
    dimensions: dict[str, dict[str, Any]] = {
        name: {"status": "N/A", "evidence_ids": [], "measurements": []}
        for name in _DIMENSIONS
    }
    measurements: list[dict[str, Any]] = []
    for record in records:
        if record["model"]["canonical_family"] != canonical_family:
            continue
        benchmark_id = str(record["benchmark"]["id"])
        dimension = benchmark_dimensions.get(benchmark_id)
        if dimension not in dimensions:
            continue
        is_composite = bool(record.get("relationships", {}).get("composite_of"))
        accepted_versions = tier_rules.get("accepted_versions") or {}
        allowed_versions = accepted_versions.get(benchmark_id)
        calibration_compatible = bool(allowed_versions and record["benchmark"].get("version") in allowed_versions)
        freshness_status, age_days, freshness_limit_days, freshness_basis = _freshness(
            record, registry, evaluated_date
        )
        primary = (
            _formal_primary(record)
            and not is_composite
            and calibration_compatible
            and freshness_status == "current"
        )
        item = {
            "evidence_id": record["evidence_id"],
            "source_id": record["source_id"],
            "source_role": record["source_role"],
            "benchmark_id": benchmark_id,
            "benchmark_version": record["benchmark"].get("version"),
            "metric": record["result"]["metric"],
            "value": record["result"]["value"],
            "scale": record["result"]["scale"],
            "numeric_percent": _numeric_percent(record),
            "calibration_compatible": calibration_compatible,
            "freshness_status": freshness_status,
            "age_days": age_days,
            "freshness_limit_days": freshness_limit_days,
            "freshness_basis": freshness_basis,
            "primary": primary,
        }
        measurements.append({**item, "dimension": dimension})
        dimensions[dimension]["evidence_ids"].append(record["evidence_id"])
        dimensions[dimension]["measurements"].append(item)
        if primary:
            dimensions[dimension]["status"] = "measured"
        elif dimensions[dimension]["status"] != "measured":
            dimensions[dimension]["status"] = "supplemental"

    primary_dims = {
        item["dimension"] for item in measurements if item["primary"]
    }
    primary_count = len(primary_dims)
    coverage = "high" if primary_count >= 3 else "medium" if primary_count >= 2 else "low"
    confidence = "high" if primary_count >= 3 else "medium" if primary_count >= 2 else "low"
    tier = _tier_from_rules(measurements, dimensions, tier_rules)
    evidence_ids = sorted(record["evidence_id"] for record in records)
    return {
        "schema": SCORECARD_SCHEMA,
        "rules_version": TIER_RULES_SCHEMA,
        "rules_status": tier_rules.get("status"),
        "builder_version": BUILDER_VERSION,
        "canonical_family": canonical_family,
        "evidence_ids": evidence_ids,
        "evidence_set_sha256": evidence_set_sha256(records),
        "source_registry_sha256": source_registry_sha256(registry),
        "tier_rules_sha256": tier_rules_sha256(tier_rules),
        "evaluated_at": evaluated_at,
        "dimensions": dimensions,
        "coverage": coverage,
        "confidence": confidence,
        "tier": tier,
    }


def validate_scorecard(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelScorecardError("scorecard must be an object")
    required = {
        "schema", "rules_version", "rules_status", "builder_version",
        "canonical_family", "evidence_ids", "evidence_set_sha256",
        "source_registry_sha256", "tier_rules_sha256", "evaluated_at",
        "dimensions", "coverage", "confidence", "tier",
    }
    if set(value) != required:
        raise ModelScorecardError("scorecard contains unsupported or missing fields")
    if value.get("schema") != SCORECARD_SCHEMA or value.get("rules_version") != TIER_RULES_SCHEMA:
        raise ModelScorecardError("scorecard schema/rules version is unsupported")
    if value.get("builder_version") != BUILDER_VERSION:
        raise ModelScorecardError("scorecard builder_version is unsupported")
    canonical = value.get("canonical_family")
    if not isinstance(canonical, str) or not canonical.strip():
        raise ModelScorecardError("scorecard canonical_family is invalid")
    evidence_ids = value.get("evidence_ids")
    if not isinstance(evidence_ids, list) or any(not isinstance(item, str) or not item for item in evidence_ids) or evidence_ids != sorted(set(evidence_ids)):
        raise ModelScorecardError("scorecard evidence_ids are invalid")
    for field in ("evidence_set_sha256", "source_registry_sha256", "tier_rules_sha256"):
        digest = value.get(field)
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
            raise ModelScorecardError(f"scorecard {field} is invalid")
    _date_only(str(value.get("evaluated_at") or ""), "scorecard.evaluated_at")
    if value.get("rules_status") not in {"uncalibrated", "calibrated"}:
        raise ModelScorecardError("scorecard rules_status is unsupported")
    if value.get("tier") not in _TIERS - {"DYNAMIC"}:
        raise ModelScorecardError("scorecard tier is unsupported")
    if value.get("coverage") not in {"high", "medium", "low"} or value.get("confidence") not in {"high", "medium", "low"}:
        raise ModelScorecardError("scorecard coverage/confidence is unsupported")
    dims = value.get("dimensions")
    if not isinstance(dims, dict) or set(dims) != set(_DIMENSIONS):
        raise ModelScorecardError("scorecard dimensions are invalid")
    for name, dim in dims.items():
        if not isinstance(dim, dict) or set(dim) - {"status", "evidence_ids", "measurements"}:
            raise ModelScorecardError(f"scorecard dimension {name} is invalid")
        if dim.get("status") not in {"measured", "supplemental", "N/A"}:
            raise ModelScorecardError(f"scorecard dimension {name} status is invalid")
        ids = dim.get("evidence_ids")
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise ModelScorecardError(f"scorecard dimension {name} evidence_ids is invalid")
        measurements = dim.get("measurements", [])
        if not isinstance(measurements, list):
            raise ModelScorecardError(f"scorecard dimension {name} measurements is invalid")
    return value

def validate_scorecard_binding(
    scorecard: Mapping[str, Any],
    *,
    canonical_family: str,
    evidence: Sequence[Mapping[str, Any]],
    registry: ModelEvaluationSourceRegistry,
    tier_rules: Mapping[str, Any],
) -> None:
    """Verify a persisted scorecard is cryptographically bound to its inputs."""
    normalized = validate_scorecard(dict(scorecard))
    if normalized["canonical_family"] != canonical_family:
        raise ModelScorecardError("scorecard binding canonical_family mismatch")
    records = validate_evidence_collection(list(evidence), registry)
    if any(record["model"]["canonical_family"] != canonical_family for record in records):
        raise ModelScorecardError("scorecard binding evidence canonical_family mismatch")
    expected_ids = sorted(record["evidence_id"] for record in records)
    if normalized["evidence_ids"] != expected_ids:
        raise ModelScorecardError("scorecard binding evidence_ids mismatch")
    expected = {
        "evidence_set_sha256": evidence_set_sha256(records),
        "source_registry_sha256": source_registry_sha256(registry),
        "tier_rules_sha256": tier_rules_sha256(tier_rules),
    }
    for field, digest in expected.items():
        if normalized[field] != digest:
            raise ModelScorecardError(f"scorecard binding {field} mismatch")
