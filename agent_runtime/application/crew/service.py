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
from agent_runtime.application.crew.routing_profiles import ModelRoutingProfiles

Probe = Callable[[], dict[str, Any]]
ModelCatalog = Callable[[], list[ModelDescriptor]]
ModelPolicyLoader = Callable[[], Any]
RoutingProfilesLoader = Callable[[], ModelRoutingProfiles]


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

    def __init__(
        self,
        providers: Mapping[str, CrewProvider],
        *,
        task_service: Any | None = None,
        model_policy_loader: ModelPolicyLoader | None = None,
        routing_profiles_loader: RoutingProfilesLoader | None = None,
    ) -> None:
        self._providers = {
            str(name).strip().lower(): provider
            for name, provider in providers.items()
            if str(name).strip()
        }
        self._task_service = task_service
        self._model_policy_loader = model_policy_loader
        self._routing_profiles_loader = routing_profiles_loader

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
        """Project provider + operator + durable facts without selecting a model."""
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

        policy, authorization = self._load_model_policy()
        profiles, profiles_meta = self._load_routing_profiles()
        explicit_allowed = getattr(policy, "allowed_models", None) if policy is not None else None
        policy_route_ids = {
            str(item) for item in (explicit_allowed or ())
            if str(item).startswith(f"{name}:")
        }
        profile_route_ids = set(profiles.route_ids(name)) if profiles is not None else set()
        historical_ids = set(self._historical_models(name))

        provider_by_id: dict[str, ModelDescriptor] = {}
        ordered_ids: list[str] = []
        for model in models:
            if model.model_id in provider_by_id:
                continue
            provider_by_id[model.model_id] = model
            ordered_ids.append(model.model_id)
        for route_id in sorted(policy_route_ids | profile_route_ids):
            model_id = route_id.split(":", 1)[1]
            if model_id not in ordered_ids:
                ordered_ids.append(model_id)
        for model_id in sorted(historical_ids):
            if model_id not in ordered_ids:
                ordered_ids.append(model_id)

        projected: list[dict[str, Any]] = []
        for model_id in ordered_ids:
            observed = provider_by_id.get(model_id)
            if observed is None:
                model = ModelDescriptor(
                    backend=name,
                    model_id=model_id,
                    available=None,
                    disabled_reason=None,
                    source=(
                        "runtime_history" if model_id in historical_ids
                        else "operator_projection"
                    ),
                    metadata={
                        "catalog_status": (
                            "historical_only" if model_id in historical_ids
                            else "not_observed_in_provider_catalog"
                        ),
                        "availability_status": "current_catalog_not_observed",
                        "billing": {"status": "unknown"},
                    },
                )
            else:
                model = observed
            route_id = f"{name}:{model_id}"
            profile = profiles.get(route_id) if profiles is not None else None
            allowlist_status = self._allowlist_status(route_id, policy, authorization)
            routable, routability_status = self._routability(
                model.available, allowlist_status,
                dispatch_ready=provider.descriptor.dispatch_ready,
            )
            billing = model.metadata.get("billing")
            billing = billing if isinstance(billing, dict) else {}
            public_metadata = dict(model.metadata)
            public_billing = dict(billing)
            # Catalog pricing metadata is never a task-cost formula.  Enforce
            # the public safety bit even if an upstream adapter accidentally
            # projects a permissive value.
            public_billing["calculation_allowed"] = False
            public_metadata["billing"] = public_billing
            reference_multiplier = self._reference_multiplier(billing)
            supported_efforts = self._supported_efforts(model.metadata)
            suggested_effort = (
                str(profile.get("suggested_effort") or "").strip()
                if isinstance(profile, dict) else ""
            ) or None
            suggested_supported = (
                suggested_effort in supported_efforts
                if suggested_effort is not None and supported_efforts
                else None
            )
            projected.append({
                **model.to_dict(),
                "metadata": public_metadata,
                "route_id": route_id,
                "allowlist_status": allowlist_status,
                "policy_sha256": authorization.get("sha256"),
                "routable": routable,
                "routability_status": routability_status,
                "reference_multiplier": reference_multiplier,
                "calculation_allowed": False,
                "context_window_tokens": self._context_window_tokens(model.metadata),
                "capability_profile": profile,
                "reasoning": {
                    "supported_efforts": supported_efforts,
                    "suggested_effort": suggested_effort,
                    "suggested_effort_supported": suggested_supported,
                },
                "history": self.model_history(name, model_id),
                "usage": self.model_usage_summary(name, model_id),
                "sources": {
                    "availability": model.source,
                    "billing": str(billing.get("source") or model.source),
                    "authorization": "operator_dispatch_policy" if policy is not None else "unavailable",
                    "capability_profile": (
                        "operator_model_routing_profiles" if profile is not None
                        else ("unavailable" if profiles_meta.get("status") == "invalid" else "not_configured")
                    ),
                    "history": "runtime_task_history",
                    "usage": "runtime_evidence",
                },
            })

        return {
            "schema": "tp-voyager.model_catalog/v2",
            "backend": name,
            "catalog": {
                "status": status,
                "source": catalog_source,
                "provider_model_count": len(models),
                "projected_model_count": len(projected),
                "historical_only_count": sum(
                    1 for item in projected
                    if item.get("metadata", {}).get("catalog_status") == "historical_only"
                ),
                "authorization": authorization,
                "routing_profiles": profiles_meta,
                "selection_performed": False,
            },
            "models": projected,
            "selection_performed": False,
            "dispatch_performed": False,
            "observed_at": time.time(),
        }

    def _load_model_policy(self) -> tuple[Any | None, dict[str, Any]]:
        if self._model_policy_loader is None:
            return None, {
                "status": "unavailable",
                "source": "operator_dispatch_policy",
                "sha256": None,
            }
        try:
            policy = self._model_policy_loader()
        except Exception:
            return None, {
                "status": "invalid",
                "source": "operator_dispatch_policy",
                "sha256": None,
            }
        return policy, {
            "status": "loaded",
            "source": "operator_dispatch_policy",
            "sha256": str(getattr(policy, "sha256", "") or "") or None,
            "allowlist_configured": getattr(policy, "allowed_models", None) is not None,
        }

    def _load_routing_profiles(self) -> tuple[ModelRoutingProfiles | None, dict[str, Any]]:
        if self._routing_profiles_loader is None:
            profiles = ModelRoutingProfiles()
            return profiles, profiles.metadata()
        try:
            profiles = self._routing_profiles_loader()
        except Exception:
            return None, {
                "status": "invalid",
                "source": "operator_model_routing_profiles",
                "sha256": None,
                "profile_count": 0,
                "advisory_only": True,
            }
        return profiles, profiles.metadata()

    @staticmethod
    def _allowlist_status(route_id: str, policy: Any | None, authorization: Mapping[str, Any]) -> str:
        if authorization.get("status") == "invalid":
            return "policy_invalid"
        if policy is None:
            return "policy_unavailable"
        allowed = getattr(policy, "allowed_models", None)
        if allowed is None:
            return "unrestricted"
        return "allowed" if route_id in allowed else "denied"

    @staticmethod
    def _routability(
        available: bool | None, allowlist_status: str, *, dispatch_ready: bool = True
    ) -> tuple[bool | None, str]:
        if not dispatch_ready:
            return False, "crew_not_dispatch_ready"
        if allowlist_status == "policy_invalid":
            return False, "policy_invalid"
        if allowlist_status == "denied":
            return False, "denied_by_policy"
        if allowlist_status == "policy_unavailable":
            return None, "policy_unavailable"
        if available is False:
            return False, "provider_disabled"
        if available is True:
            return True, "confirmed"
        return None, "availability_unconfirmed"

    @staticmethod
    def _reference_multiplier(billing: Mapping[str, Any]) -> float | None:
        for key in ("multiplier", "price_factor"):
            value = billing.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                return float(value)
        return None

    @staticmethod
    def _context_window_tokens(metadata: Mapping[str, Any]) -> int | None:
        direct = metadata.get("context_window_tokens")
        if isinstance(direct, int) and not isinstance(direct, bool) and direct > 0:
            return direct
        config = metadata.get("context_config")
        if not isinstance(config, dict):
            return None
        values: list[int] = []
        for item in config.values():
            if not isinstance(item, dict):
                continue
            count = item.get("token_count")
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                values.append(count)
        return max(values) if values else None

    @staticmethod
    def _supported_efforts(metadata: Mapping[str, Any]) -> list[str]:
        value = metadata.get("supported_efforts")
        if isinstance(value, (list, tuple)):
            output: list[str] = []
            for item in value:
                token = str(item or "").strip()
                if token and token not in output:
                    output.append(token)
            return output[:16]
        thinking = metadata.get("thinking_config")
        if isinstance(thinking, dict):
            for key in ("supported_efforts", "reasoning_efforts", "efforts"):
                value = thinking.get(key)
                if isinstance(value, list):
                    return [str(item).strip() for item in value[:16] if str(item).strip()]
            enabled = thinking.get("enabled")
            if isinstance(enabled, dict):
                efforts = enabled.get("efforts")
                if isinstance(efforts, dict):
                    return [
                        str(key).strip() for key in list(efforts)[:16]
                        if str(key).strip()
                    ]
        return []

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
