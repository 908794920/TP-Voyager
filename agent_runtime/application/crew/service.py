"""TP-Voyager Crew Registry.

The registry is deliberately a projection over official backend declarations
and existing Durable Task history.  It creates no new persistence tables and
never dispatches a worker.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping

from agent_runtime.domain.crew import CrewDescriptor, CrewHealthSnapshot, ModelDescriptor
from agent_runtime.domain.task import Task


Probe = Callable[[], dict[str, Any]]
ModelCatalog = Callable[[], list[ModelDescriptor]]


@dataclass(frozen=True)
class CrewProvider:
    descriptor: CrewDescriptor
    probe: Probe | None = None
    models: ModelCatalog | None = None


_TASK_CAPABILITIES: dict[str, frozenset[str]] = {
    # ``analyze_context`` is the normalized Captain outcome.  A Crew may
    # satisfy it either with host-bounded native read/search tools (Qoder) or
    # with a Runtime-rendered immutable context snapshot (CodeBuddy).
    "research": frozenset({"analyze_context"}),
    "code_review": frozenset({"analyze_context"}),
    "small_patch": frozenset({"analyze_context", "edit_files", "run_commands"}),
    "test_failure_triage": frozenset({"analyze_context"}),
    # A command-only verification route is not part of T4.  Keep the public
    # task kind visible but incompatible until that bounded route is designed.
    "verify_only": frozenset({"verify_commands"}),
}


class CrewRegistryService:
    """Normalized Crew capability/health/recommendation view for the Captain."""

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
                item["models"] = [model.to_dict() for model in self.models(name)]
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
        return CrewHealthSnapshot(
            backend=name,
            installed=installed,
            dispatch_ready=provider.descriptor.dispatch_ready,
            availability=availability,
            version=version,
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
            ranked.append(
                {
                    "backend": name,
                    "compatible": compatible,
                    "score": round(score, 3),
                    "missing_capabilities": missing,
                    "sample_count": health.sample_count,
                    "success_rate": health.success_rate,
                    "availability": health.availability,
                    "dispatch_ready": provider.descriptor.dispatch_ready,
                }
            )
        ranked.sort(key=lambda item: (-item["score"], item["backend"]))
        return {
            "schema": "tp-voyager.crew_recommendation/v1",
            "task_kind": kind,
            "required_capabilities": sorted(required),
            "recommendations": ranked,
            "selection_performed": False,
            "dispatch_performed": False,
        }

    def _history(self, backend: str) -> dict[str, Any]:
        if self._task_service is None:
            return self._empty_history()
        try:
            tasks = [
                task for task in self._task_service.list_tasks()
                if str(task.task_type).strip().lower() == backend
            ]
        except Exception:
            return self._empty_history()
        considered = [
            task for task in tasks
            if task.status in {"completed", "failed", "lost", "orphaned"}
        ]
        considered.sort(key=lambda task: task.finished_at or task.updated_at or task.created_at, reverse=True)
        successes = sum(task.status == "completed" for task in considered)
        success_rate = (successes / len(considered)) if considered else None
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
        return {
            "sample_count": len(considered),
            "success_rate": success_rate,
            "average_duration_seconds": (sum(durations) / len(durations)) if durations else None,
            "failure_streak": failure_streak,
            "last_success_at": max(success_times) if success_times else None,
            "last_failure_at": max(failure_times) if failure_times else None,
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
        }
