"""TP-Voyager Crew and model-awareness registry.

The registry is deliberately a projection over official backend declarations,
provider-observed model catalogs, Usage Evidence, and existing Durable Task
history.  It creates no second persistence system and never selects or
dispatches a worker/model on the Captain's behalf.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable, Mapping

from agent_runtime.domain.crew import CrewDescriptor, CrewHealthSnapshot, ModelDescriptor

Probe = Callable[[], dict[str, Any]]
ModelCatalog = Callable[[], list[ModelDescriptor]]


@dataclass(frozen=True)
class CrewProvider:
    descriptor: CrewDescriptor
    probe: Probe | None = None
    models: ModelCatalog | None = None


_TASK_CAPABILITIES: dict[str, frozenset[str]] = {
    "research": frozenset({"analyze_context"}),
    "repository_research": frozenset({"analyze_context"}),
    "code_review": frozenset({"analyze_context"}),
    "small_patch": frozenset({"analyze_context", "edit_files", "run_commands"}),
    "test_failure_triage": frozenset({"analyze_context"}),
    "verify_only": frozenset({"verify_commands"}),
}

_TERMINAL_HEALTH = frozenset({"completed", "failed", "lost", "orphaned"})


class CrewRegistryService:
    """Normalized Crew capability/health/model-awareness view for Captain."""

    def __init__(self, providers: Mapping[str, CrewProvider], *, task_service: Any | None = None) -> None:
        self._providers = {
            str(name).strip().lower(): provider
            for name, provider in providers.items()
            if str(name).strip()
        }
        self._task_service = task_service

    def catalog(self, *, probe: bool = False, include_models: bool = False) -> dict[str, Any]:
        crews: list[dict[str, Any]] = []
        for name in sorted(self._providers):
            provider = self._providers[name]
            item = provider.descriptor.to_dict()
            item["health"] = self.health(name, probe=probe).to_dict()
            if include_models:
                snapshot = self.model_catalog(name)
                item["model_catalog"] = snapshot["catalog"]
                item["models"] = snapshot["models"]
            crews.append(item)
        return {
            "schema": "tp-voyager.crew_catalog/v1",
            "selection_performed": False,
            "dispatch_performed": False,
            "crew": crews,
            "updated_at": time.time(),
        }

    def descriptor(self, backend: str) -> CrewDescriptor:
        name = backend.strip().lower()
        provider = self._providers.get(name)
        if provider is None:
            raise ValueError(f"Unknown crew backend: {backend}")
        return provider.descriptor

    @staticmethod
    def required_capabilities(
        task_kind: str, extra: tuple[str, ...] | list[str] | None = None
    ) -> tuple[str, ...]:
        kind = str(task_kind or "").strip().lower()
        if kind not in _TASK_CAPABILITIES:
            raise ValueError(f"Unsupported task_kind: {task_kind}")
        required = set(_TASK_CAPABILITIES[kind])
        required.update(str(item).strip() for item in (extra or ()) if str(item).strip())
        return tuple(sorted(required))

    def health(self, backend: str, *, probe: bool = True) -> CrewHealthSnapshot:
        name = backend.strip().lower()
        provider = self._providers.get(name)
        if provider is None:
            raise ValueError(f"Unknown crew backend: {backend}")
        history = self._history(name)
        installed: bool | None = None
        version: str | None = None
        probe_error: str | None = None
        detail: dict[str, Any] = {}
        if probe and provider.probe is not None:
            try:
                observed = dict(provider.probe() or {})
                installed = bool(observed.pop("installed", observed.pop("ok", True)))
                version_value = observed.pop("version", None)
                version = str(version_value) if version_value else None
                detail = observed
            except Exception as exc:  # noqa: BLE001 - public probe is fail-closed
                installed = False
                probe_error = type(exc).__name__
        availability = "unknown"
        if installed is True:
            availability = "available" if provider.descriptor.dispatch_ready else "installed_not_dispatch_ready"
        elif installed is False:
            availability = "unavailable"
        elif provider.descriptor.dispatch_ready:
            availability = "not_probed"
        else:
            availability = "not_dispatch_ready"
        auth_status = "not_probed"
        if bool(detail.get("auth_probe_performed")):
            auth_status = str(detail.get("auth_status") or "probed_unknown")
        return CrewHealthSnapshot(
            backend=name,
            installed=installed,
            dispatch_ready=provider.descriptor.dispatch_ready,
            availability=availability,
            version=version,
            auth_status=auth_status,
            model_catalog_status=provider.descriptor.model_discovery or "unknown",
            probe_error=probe_error,
            detail=detail,
            **history,
        )

    def models(self, backend: str) -> list[ModelDescriptor]:
        name = backend.strip().lower()
        provider = self._providers.get(name)
        if provider is None:
            raise ValueError(f"Unknown crew backend: {backend}")
        if provider.models is None:
            return []
        try:
            return list(provider.models())
        except Exception:
            return []

    def model_catalog(self, backend: str) -> dict[str, Any]:
        """Project provider catalog + durable model facts without new state."""
        name = backend.strip().lower()
        provider = self._providers.get(name)
        if provider is None:
            raise ValueError(f"Unknown crew backend: {backend}")
        models = self.models(name)
        statuses = {
            str(model.metadata.get("catalog_status") or "unknown")
            for model in models
        }
        if provider.models is None:
            status = "unsupported"
        elif not models:
            status = "unknown"
        elif any(value.startswith("incomplete") for value in statuses):
            status = "incomplete"
        else:
            status = "complete"
        observed_sources = {str(model.source or "unknown") for model in models}
        catalog_source = (
            next(iter(observed_sources))
            if len(observed_sources) == 1
            else provider.descriptor.model_discovery
        )
        projected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for model in models:
            seen.add(model.model_id)
            projected.append({
                **model.to_dict(),
                "history": self.model_history(name, model.model_id),
                "usage": self.model_usage_summary(name, model.model_id),
            })
        # Preserve models actually used in durable history even if the current
        # CLI catalog is unknown/changed.  Historical presence never implies
        # current availability.
        for model_id in self._historical_models(name):
            if model_id in seen:
                continue
            projected.append({
                **ModelDescriptor(
                    backend=name,
                    model_id=model_id,
                    available=None,
                    disabled_reason=None,
                    source="runtime_history",
                    metadata={
                        "catalog_status": "historical_only",
                        "availability_status": "current_catalog_not_observed",
                        "billing": {"status": "unknown"},
                    },
                ).to_dict(),
                "history": self.model_history(name, model_id),
                "usage": self.model_usage_summary(name, model_id),
            })
        return {
            "schema": "tp-voyager.model_catalog/v1",
            "backend": name,
            "catalog": {
                "status": status,
                "source": catalog_source,
                "model_count": len(models),
                "historical_only_count": max(0, len(projected) - len(models)),
                "selection_performed": False,
            },
            "models": projected,
            "observed_at": time.time(),
        }

    def model_history(self, backend: str, model: str) -> dict[str, Any]:
        name = backend.strip().lower()
        model_id = str(model or "").strip()
        tasks = self._model_tasks(name, model_id)
        considered = [task for task in tasks if task.status in _TERMINAL_HEALTH]
        considered.sort(key=lambda task: task.finished_at or task.updated_at or task.created_at, reverse=True)
        successes = sum(task.status == "completed" for task in considered)
        durations = [
            float(task.finished_at - task.started_at)
            for task in considered
            if task.started_at is not None and task.finished_at is not None and task.finished_at >= task.started_at
        ]
        failure_streak = 0
        for task in considered:
            if task.status == "completed":
                break
            failure_streak += 1
        last = considered[0] if considered else None
        return {
            "sample_count": len(considered),
            "success_count": successes,
            "success_rate": (successes / len(considered)) if considered else None,
            "average_duration_seconds": (sum(durations) / len(durations)) if durations else None,
            "failure_streak": failure_streak,
            "last_terminal_status": last.status if last else None,
            "last_observed_at": (last.finished_at or last.updated_at) if last else None,
            "health_interpretation": "facts_only",
        }

    def model_usage_summary(self, backend: str, model: str) -> dict[str, Any]:
        """Aggregate only persisted provider-reported ``tp-voyager.usage/v1`` facts."""
        name = backend.strip().lower()
        model_id = str(model or "").strip()
        if self._task_service is None:
            return self._empty_usage_summary(name, model_id)
        payloads: list[dict[str, Any]] = []
        for task in self._model_tasks(name, model_id):
            try:
                payload = self._task_service.latest_usage_evidence(task.task_id)
            except Exception:
                continue
            if not isinstance(payload, dict) or payload.get("schema") != "tp-voyager.usage/v1":
                continue
            if str(payload.get("provider") or "").strip().lower() != name:
                continue
            if str(payload.get("model") or "").strip() != model_id:
                continue
            payloads.append(payload)

        def values(key: str) -> list[float]:
            out: list[float] = []
            for payload in payloads:
                usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                value = usage.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out.append(float(value))
            return out

        input_tokens = values("input_tokens")
        output_tokens = values("output_tokens")
        credits = values("credits_used")
        costs = values("reported_cost")
        currencies = {
            str((payload.get("usage") or {}).get("currency") or "").strip().upper()
            for payload in payloads
            if isinstance(payload.get("usage"), dict)
            and str((payload.get("usage") or {}).get("currency") or "").strip()
        }
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        return {
            "schema": "tp-voyager.model_usage_summary/v1",
            "backend": name,
            "model": model_id,
            "usage_sample_count": len(payloads),
            "average_input_tokens": (sum(input_tokens) / len(input_tokens)) if input_tokens else None,
            "average_output_tokens": (sum(output_tokens) / len(output_tokens)) if output_tokens else None,
            "average_credits_used": (sum(credits) / len(credits)) if credits else None,
            "total_reported_cost": (sum(costs) if costs and currency else None),
            "currency": currency,
            "pricing_estimated": False,
            "source": "usage_evidence",
        }

    def recommend(
        self,
        task_kind: str,
        *,
        required_capabilities: list[str] | None = None,
        probe: bool = False,
    ) -> dict[str, Any]:
        kind = task_kind.strip().lower()
        if kind not in _TASK_CAPABILITIES:
            raise ValueError(f"Unsupported task_kind: {task_kind}")
        required = set(_TASK_CAPABILITIES[kind])
        required.update(str(item).strip() for item in (required_capabilities or []) if str(item).strip())
        ranked: list[dict[str, Any]] = []
        for name in sorted(self._providers):
            provider = self._providers[name]
            controlled = set(provider.descriptor.controlled_capabilities)
            missing = sorted(required - controlled)
            health = self.health(name, probe=probe)
            compatible = not missing and provider.descriptor.dispatch_ready
            if probe and health.installed is False:
                compatible = False
            score = 0.0
            if compatible:
                score = 100.0
                if health.success_rate is not None:
                    score += health.success_rate * 20.0
                score -= min(health.failure_streak, 5) * 5.0
            ranked.append({
                "backend": name,
                "compatible": compatible,
                "score": round(score, 3),
                "missing_capabilities": missing,
                "sample_count": health.sample_count,
                "success_rate": health.success_rate,
                "availability": health.availability,
                "dispatch_ready": provider.descriptor.dispatch_ready,
            })
        ranked.sort(key=lambda item: (-item["score"], item["backend"]))
        return {
            "schema": "tp-voyager.crew_recommendation/v1",
            "task_kind": kind,
            "required_capabilities": sorted(required),
            "recommendations": ranked,
            "selection_performed": False,
            "dispatch_performed": False,
        }

    def _model_tasks(self, backend: str, model: str) -> list[Any]:
        if self._task_service is None or not model:
            return []
        try:
            tasks = [task for task in self._task_service.list_tasks() if str(task.task_type).strip().lower() == backend]
        except Exception:
            return []
        result: list[Any] = []
        for task in tasks:
            try:
                session = self._task_service.get_session(task.task_id)
                metadata = json.loads(session.metadata_json or "{}") if session is not None else {}
            except Exception:
                metadata = {}
            observed_model = str(metadata.get("model") or "").strip() if isinstance(metadata, dict) else ""
            if observed_model == model:
                result.append(task)
        return result

    def _historical_models(self, backend: str) -> list[str]:
        if self._task_service is None:
            return []
        try:
            tasks = [task for task in self._task_service.list_tasks() if str(task.task_type).strip().lower() == backend]
        except Exception:
            return []
        models: list[str] = []
        for task in tasks:
            try:
                session = self._task_service.get_session(task.task_id)
                metadata = json.loads(session.metadata_json or "{}") if session is not None else {}
            except Exception:
                metadata = {}
            model = str(metadata.get("model") or "").strip() if isinstance(metadata, dict) else ""
            if model and model not in models:
                models.append(model)
        return models[:256]

    def _history(self, backend: str) -> dict[str, Any]:
        if self._task_service is None:
            return self._empty_history()
        try:
            tasks = [task for task in self._task_service.list_tasks() if str(task.task_type).strip().lower() == backend]
        except Exception:
            return self._empty_history()
        considered = [task for task in tasks if task.status in _TERMINAL_HEALTH]
        considered.sort(key=lambda task: task.finished_at or task.updated_at or task.created_at, reverse=True)
        successes = sum(task.status == "completed" for task in considered)
        durations = [
            float(task.finished_at - task.started_at)
            for task in considered
            if task.started_at is not None and task.finished_at is not None and task.finished_at >= task.started_at
        ]
        failure_streak = 0
        for task in considered:
            if task.status == "completed":
                break
            failure_streak += 1
        success_times = [task.finished_at or task.updated_at for task in considered if task.status == "completed"]
        failure_times = [task.finished_at or task.updated_at for task in considered if task.status != "completed"]
        last_successful_model = None
        last_successful_model_at = None
        for task in considered:
            if task.status != "completed":
                continue
            try:
                session = self._task_service.get_session(task.task_id)
                metadata = json.loads(session.metadata_json or "{}") if session is not None else {}
            except Exception:
                metadata = {}
            model = str(metadata.get("model") or "").strip() if isinstance(metadata, dict) else ""
            if model:
                last_successful_model = model
                last_successful_model_at = task.finished_at or task.updated_at
                break
        return {
            "sample_count": len(considered),
            "success_rate": (successes / len(considered)) if considered else None,
            "average_duration_seconds": (sum(durations) / len(durations)) if durations else None,
            "failure_streak": failure_streak,
            "last_success_at": max(success_times) if success_times else None,
            "last_failure_at": max(failure_times) if failure_times else None,
            "last_successful_model": last_successful_model,
            "last_successful_model_at": last_successful_model_at,
            "last_successful_model_source": "runtime_observation" if last_successful_model else None,
        }

    @staticmethod
    def _empty_usage_summary(backend: str, model: str) -> dict[str, Any]:
        return {
            "schema": "tp-voyager.model_usage_summary/v1",
            "backend": backend,
            "model": model,
            "usage_sample_count": 0,
            "average_input_tokens": None,
            "average_output_tokens": None,
            "average_credits_used": None,
            "total_reported_cost": None,
            "currency": None,
            "pricing_estimated": False,
            "source": "usage_evidence",
        }

    @staticmethod
    def _empty_history() -> dict[str, Any]:
        return {
            "sample_count": 0,
            "success_rate": None,
            "average_duration_seconds": None,
            "failure_streak": 0,
            "last_success_at": None,
            "last_failure_at": None,
            "last_successful_model": None,
            "last_successful_model_at": None,
            "last_successful_model_source": None,
        }
