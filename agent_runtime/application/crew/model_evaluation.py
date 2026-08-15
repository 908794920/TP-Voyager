"""Model Evaluation Standard v1 schemas and validation.

This module validates operator/research evidence.  It does not fetch the web,
score models, select models, or change dispatch authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


SOURCE_SCHEMA = "tp-voyager.model_evaluation_sources/v1"
EVIDENCE_SCHEMA = "tp-voyager.model_evidence/v1"
_SOURCE_FILE = "model_evaluation_sources.baseline.json"

_SOURCE_TOP = frozenset({"schema", "updated_at", "sources"})
_SOURCE_KEYS = frozenset({
    "status", "role", "source_type", "dimensions", "requires", "composite_of",
    "freshness_policy_days", "url", "methodology_url",
})
_EVIDENCE_KEYS = frozenset({
    "evidence_schema", "evidence_id", "source_id", "source_role", "subject_type",
    "model", "benchmark", "execution", "result", "provenance", "relationships",
})
_MODEL_KEYS = frozenset({"tested_model", "canonical_family", "model_match"})
_BENCHMARK_KEYS = frozenset({"id", "version", "task_count"})
_EXECUTION_KEYS = frozenset({
    "agent", "agent_version", "harness", "harness_version", "reasoning_effort",
    "attempts_per_task",
})
_RESULT_KEYS = frozenset({"metric", "value", "scale"})
_PROVENANCE_KEYS = frozenset({
    "observed_at", "published_at", "url", "methodology_url", "primary_approved_by",
    "primary_approved_at", "approval_basis_url",
})
_RELATIONSHIP_KEYS = frozenset({"composite_of", "duplicate_of"})

_SOURCE_STATUS = frozenset({"active", "supplemental", "legacy", "archived", "experimental"})
_SOURCE_ROLE = frozenset({"primary", "supplemental", "provider", "preference", "historical", "experimental"})
_SUBJECT_TYPES = frozenset({"model_only", "model_agent", "preference", "provider_claim", "operator_observed"})
_MODEL_MATCH = frozenset({"exact", "near_exact", "family", "predecessor", "dynamic_tier", "missing"})
_DIMENSIONS = frozenset({
    "repository_engineering", "terminal_agentic", "codebase_understanding",
    "general_coding", "multimodal_coding",
})
_REQUIREMENTS = frozenset({
    "benchmark_version", "exact_model_identity", "agent", "harness", "reasoning_effort",
    "attempts_per_task", "provenance_url", "observed_at", "primary_approval",
})


class ModelEvaluationError(ValueError):
    """Evaluation source/evidence data is malformed or non-comparable."""


def _strict_json(path: Path) -> dict[str, Any]:
    def hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        out: dict[str, object] = {}
        for key, value in pairs:
            if key in out:
                raise ModelEvaluationError(f"duplicate JSON key: {key}")
            out[key] = value
        return out

    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=hook)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelEvaluationError(f"invalid evaluation JSON: {path.name}") from exc
    if not isinstance(raw, dict):
        raise ModelEvaluationError("evaluation document must be an object")
    return raw


def _text(value: object, field: str, *, required: bool = True, limit: int = 800) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ModelEvaluationError(f"{field} must be a string")
    value = value.strip()
    if not value or len(value) > limit or "\x00" in value:
        raise ModelEvaluationError(f"{field} is invalid")
    return value


def _url(value: object, field: str, *, required: bool = True) -> str | None:
    text = _text(value, field, required=required, limit=1200)
    if text is None:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelEvaluationError(f"{field} must be http(s)")
    return text


def _object(value: object, field: str, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - keys:
        raise ModelEvaluationError(f"{field} contains unsupported fields")
    return dict(value)


def _string_list(value: object, field: str, *, allowed: frozenset[str] | None = None) -> list[str]:
    if not isinstance(value, list) or len(value) > 64:
        raise ModelEvaluationError(f"{field} must be a bounded list")
    out: list[str] = []
    for item in value:
        text = _text(item, field, limit=120)
        assert text is not None
        if allowed is not None and text not in allowed:
            raise ModelEvaluationError(f"{field} contains unsupported value: {text}")
        if text in out:
            raise ModelEvaluationError(f"{field} contains duplicate value: {text}")
        out.append(text)
    return out


@dataclass(frozen=True)
class ModelEvaluationSourceRegistry:
    schema: str
    updated_at: str
    sources: Mapping[str, Mapping[str, Any]]

    @classmethod
    def bundled_path(cls) -> Path:
        return Path(__file__).with_name(_SOURCE_FILE)

    @classmethod
    def load_bundled(cls) -> "ModelEvaluationSourceRegistry":
        return cls.load(cls.bundled_path())

    @classmethod
    def load(cls, path: str | Path) -> "ModelEvaluationSourceRegistry":
        raw = _strict_json(Path(path))
        if set(raw) - _SOURCE_TOP or raw.get("schema") != SOURCE_SCHEMA:
            raise ModelEvaluationError("model evaluation source registry schema is invalid")
        updated_at = _text(raw.get("updated_at"), "updated_at", limit=64)
        raw_sources = raw.get("sources")
        if not isinstance(raw_sources, dict) or not raw_sources or len(raw_sources) > 128:
            raise ModelEvaluationError("sources must be a non-empty bounded object")
        sources: dict[str, dict[str, Any]] = {}
        for source_id, source_value in raw_sources.items():
            sid = _text(source_id, "source_id", limit=80)
            assert sid is not None
            obj = _object(source_value, f"sources.{sid}", _SOURCE_KEYS)
            status = _text(obj.get("status"), f"sources.{sid}.status", limit=32)
            role = _text(obj.get("role"), f"sources.{sid}.role", limit=32)
            source_type = _text(obj.get("source_type"), f"sources.{sid}.source_type", limit=32)
            if status not in _SOURCE_STATUS or role not in _SOURCE_ROLE:
                raise ModelEvaluationError(f"source {sid} has unsupported status/role")
            dimensions = _string_list(obj.get("dimensions"), f"sources.{sid}.dimensions", allowed=_DIMENSIONS)
            requires = _string_list(obj.get("requires"), f"sources.{sid}.requires", allowed=_REQUIREMENTS)
            composite_of = _string_list(obj.get("composite_of"), f"sources.{sid}.composite_of")
            freshness = obj.get("freshness_policy_days")
            if not isinstance(freshness, int) or isinstance(freshness, bool) or freshness < 0 or freshness > 3650:
                raise ModelEvaluationError(f"source {sid} freshness_policy_days is invalid")
            sources[sid] = {
                "status": status,
                "role": role,
                "source_type": source_type,
                "dimensions": dimensions,
                "requires": requires,
                "composite_of": composite_of,
                "freshness_policy_days": freshness,
                "url": _url(obj.get("url"), f"sources.{sid}.url", required=False),
                "methodology_url": _url(obj.get("methodology_url"), f"sources.{sid}.methodology_url", required=False),
            }
        return cls(schema=SOURCE_SCHEMA, updated_at=updated_at or "", sources=sources)

    def source(self, source_id: str) -> Mapping[str, Any]:
        source = self.sources.get(source_id)
        if source is None:
            raise ModelEvaluationError(f"unknown model evaluation source: {source_id}")
        return source


def validate_standard_evidence(
    record: Mapping[str, Any], registry: ModelEvaluationSourceRegistry,
) -> dict[str, Any]:
    if not isinstance(record, Mapping) or set(record) - _EVIDENCE_KEYS:
        raise ModelEvaluationError("standard evidence contains unsupported fields")
    if record.get("evidence_schema") != EVIDENCE_SCHEMA:
        raise ModelEvaluationError("standard evidence schema is unsupported")

    evidence_id = _text(record.get("evidence_id"), "evidence_id", limit=180)
    source_id = _text(record.get("source_id"), "source_id", limit=80)
    source_role = _text(record.get("source_role"), "source_role", limit=32)
    subject_type = _text(record.get("subject_type"), "subject_type", limit=32)
    assert evidence_id and source_id and source_role and subject_type
    if subject_type not in _SUBJECT_TYPES:
        raise ModelEvaluationError("subject_type is unsupported")
    source = registry.source(source_id)
    if source_role != source["role"]:
        raise ModelEvaluationError("source_role does not match Source Registry")
    if source["status"] == "archived" and source_role == "primary":
        raise ModelEvaluationError("archived source cannot be Primary")

    model = _object(record.get("model"), "model", _MODEL_KEYS)
    tested_model = _text(model.get("tested_model"), "model.tested_model", limit=240)
    canonical = _text(model.get("canonical_family"), "model.canonical_family", limit=180)
    model_match = _text(model.get("model_match"), "model.model_match", limit=32)
    if model_match not in _MODEL_MATCH:
        raise ModelEvaluationError("model.model_match is unsupported")

    benchmark = _object(record.get("benchmark"), "benchmark", _BENCHMARK_KEYS)
    benchmark_id = _text(benchmark.get("id"), "benchmark.id", limit=120)
    benchmark_version = _text(benchmark.get("version"), "benchmark.version", required=False, limit=120)
    task_count = benchmark.get("task_count")
    if task_count is not None and (not isinstance(task_count, int) or isinstance(task_count, bool) or task_count < 0):
        raise ModelEvaluationError("benchmark.task_count is invalid")

    execution = _object(record.get("execution"), "execution", _EXECUTION_KEYS)
    normalized_execution: dict[str, Any] = {}
    for key in ("agent", "agent_version", "harness", "harness_version", "reasoning_effort"):
        normalized_execution[key] = _text(execution.get(key), f"execution.{key}", required=False, limit=200)
    attempts = execution.get("attempts_per_task")
    if attempts is not None and (not isinstance(attempts, int) or isinstance(attempts, bool) or attempts <= 0 or attempts > 1000):
        raise ModelEvaluationError("execution.attempts_per_task is invalid")
    normalized_execution["attempts_per_task"] = attempts

    result = _object(record.get("result"), "result", _RESULT_KEYS)
    metric = _text(result.get("metric"), "result.metric", limit=120)
    scale = _text(result.get("scale"), "result.scale", limit=80)
    value = result.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ModelEvaluationError("result.value is invalid")
    if isinstance(value, str) and (not value.strip() or len(value) > 240):
        raise ModelEvaluationError("result.value is invalid")

    provenance = _object(record.get("provenance"), "provenance", _PROVENANCE_KEYS)
    normalized_provenance = {
        "observed_at": _text(provenance.get("observed_at"), "provenance.observed_at", required=False, limit=64),
        "published_at": _text(provenance.get("published_at"), "provenance.published_at", required=False, limit=64),
        "url": _url(provenance.get("url"), "provenance.url", required=False),
        "methodology_url": _url(provenance.get("methodology_url"), "provenance.methodology_url", required=False),
        "primary_approved_by": _text(provenance.get("primary_approved_by"), "provenance.primary_approved_by", required=False, limit=160),
        "primary_approved_at": _text(provenance.get("primary_approved_at"), "provenance.primary_approved_at", required=False, limit=64),
        "approval_basis_url": _url(provenance.get("approval_basis_url"), "provenance.approval_basis_url", required=False),
    }

    relationships = _object(record.get("relationships"), "relationships", _RELATIONSHIP_KEYS)
    composite_of = _string_list(relationships.get("composite_of", []), "relationships.composite_of")
    duplicate_of = _text(relationships.get("duplicate_of"), "relationships.duplicate_of", required=False, limit=180)

    requirements = set(source["requires"])
    if "benchmark_version" in requirements and not benchmark_version:
        raise ModelEvaluationError("Primary/source-required benchmark version is missing")
    if "exact_model_identity" in requirements and model_match != "exact":
        raise ModelEvaluationError("source requires exact model identity")
    if "agent" in requirements and not normalized_execution["agent"]:
        raise ModelEvaluationError("source-required agent is missing")
    if "harness" in requirements and not normalized_execution["harness"]:
        raise ModelEvaluationError("source-required harness is missing")
    if "reasoning_effort" in requirements and not normalized_execution["reasoning_effort"]:
        raise ModelEvaluationError("source-required reasoning effort is missing")
    if "attempts_per_task" in requirements and not attempts:
        raise ModelEvaluationError("source-required attempts_per_task is missing")
    if "provenance_url" in requirements and not normalized_provenance["url"]:
        raise ModelEvaluationError("source-required provenance URL is missing")
    if "observed_at" in requirements and not normalized_provenance["observed_at"]:
        raise ModelEvaluationError("source-required observed_at is missing")
    if "primary_approval" in requirements:
        for key in ("primary_approved_by", "primary_approved_at", "approval_basis_url"):
            if not normalized_provenance[key]:
                raise ModelEvaluationError(f"Primary evidence approval field is missing: {key}")
    if source_role == "primary" and (model_match != "exact" or source["status"] != "active"):
        raise ModelEvaluationError("Primary evidence must use an active source and exact model identity")

    return {
        "evidence_schema": EVIDENCE_SCHEMA,
        "evidence_id": evidence_id,
        "source_id": source_id,
        "source_role": source_role,
        "subject_type": subject_type,
        "model": {"tested_model": tested_model, "canonical_family": canonical, "model_match": model_match},
        "benchmark": {"id": benchmark_id, "version": benchmark_version, "task_count": task_count},
        "execution": normalized_execution,
        "result": {"metric": metric, "value": value, "scale": scale},
        "provenance": normalized_provenance,
        "relationships": {"composite_of": composite_of, "duplicate_of": duplicate_of},
    }


def validate_evidence_collection(
    records: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    registry: ModelEvaluationSourceRegistry,
) -> list[dict[str, Any]]:
    if len(records) > 1024:
        raise ModelEvaluationError("too many evidence records")
    out: list[dict[str, Any]] = []
    ids: set[str] = set()
    for record in records:
        normalized = validate_standard_evidence(record, registry)
        evidence_id = normalized["evidence_id"]
        if evidence_id in ids:
            raise ModelEvaluationError(f"duplicate evidence id: {evidence_id}")
        ids.add(evidence_id)
        out.append(normalized)
    return out
