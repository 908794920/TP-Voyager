"""Fail-closed Captain dispatch boundary for TP-Voyager.

T2 establishes the stable Captain-facing contract before any worker is allowed
through it.  A Crew backend must be explicitly marked controlled-dispatch
ready *and* have an injected dispatcher.  The service never falls back to a
legacy backend, never auto-selects another Crew, and never invokes WorkBuddy.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from agent_runtime.application.crew import CrewRegistryService
from agent_runtime.domain.dispatch import CaptainDispatchRequest


CrewDispatcher = Callable[[CaptainDispatchRequest], dict[str, Any]]

_ALLOWED_TASK_KINDS = frozenset(
    {"research", "code_review", "small_patch", "test_failure_triage", "verify_only"}
)
_ALLOWED_ACCESS_MODES = frozenset({"read_only", "patch"})


class CaptainDispatchService:
    def __init__(
        self,
        registry: CrewRegistryService,
        dispatchers: Mapping[str, CrewDispatcher] | None = None,
    ) -> None:
        self._registry = registry
        self._dispatchers = {
            str(name).strip().lower(): dispatcher
            for name, dispatcher in (dispatchers or {}).items()
            if str(name).strip()
        }

    def dispatch(self, request: CaptainDispatchRequest) -> dict[str, Any]:
        objective = str(request.objective or "").strip()
        crew = str(request.crew or "").strip().lower()
        kind = str(request.task_kind or "").strip().lower()
        mode = str(request.access_mode or "read_only").strip().lower()

        if not objective:
            return self._reject("INVALID_REQUEST", "objective is required", crew=crew, task_kind=kind)
        if not crew:
            return self._reject("CREW_REQUIRED", "Captain must explicitly choose a Crew", task_kind=kind)
        if crew == "workbuddy":
            return self._reject(
                "CREW_NOT_SUPPORTED",
                "WorkBuddy is not a TP-Voyager target Crew",
                crew=crew,
                task_kind=kind,
            )
        if kind not in _ALLOWED_TASK_KINDS:
            return self._reject("TASK_KIND_NOT_SUPPORTED", "unsupported task_kind", crew=crew, task_kind=kind)
        if mode not in _ALLOWED_ACCESS_MODES:
            return self._reject(
                "ACCESS_MODE_NOT_AVAILABLE",
                "access_mode must be read_only or patch",
                crew=crew,
                task_kind=kind,
            )
        if int(request.timeout_seconds) <= 0:
            return self._reject("INVALID_REQUEST", "timeout_seconds must be positive", crew=crew, task_kind=kind)

        selected_model = str(request.model or "").strip()
        if request.model_policy is not None:
            if not selected_model:
                return self._reject(
                    "MODEL_REQUIRED",
                    "model_policy requires the Captain to explicitly choose a model",
                    crew=crew,
                    task_kind=kind,
                )
            if selected_model not in request.model_policy.allowed_models:
                return self._reject(
                    "MODEL_NOT_ALLOWED",
                    "selected model is outside the Passenger/Captain allowed model pool",
                    crew=crew,
                    task_kind=kind,
                )

        if mode != "read_only" and (request.read_scope is not None or request.resolved_read_files):
            return self._reject(
                "READ_SCOPE_NOT_APPLICABLE",
                "read_scope is only accepted for read_only access_mode",
                crew=crew,
                task_kind=kind,
            )

        if request.worker_profile_ref is not None:
            if not str(request.worker_profile_content or "").strip():
                return self._reject(
                    "WORKER_PROFILE_UNRESOLVED",
                    "worker_profile_ref must resolve and verify before dispatch",
                    crew=crew,
                    task_kind=kind,
                )
            objective = (
                "# TP-Voyager verified Worker Profile\n\n"
                f"Profile: {request.worker_profile_ref.profile_id}\n"
                f"SHA256: {request.worker_profile_ref.sha256}\n\n"
                f"{request.worker_profile_content.strip()}\n\n"
                "# Assigned bounded task\n\n"
                f"{objective}"
            )
            request = replace(request, objective=objective)
        if mode == "patch":
            if kind != "small_patch":
                return self._reject(
                    "ACCESS_MODE_TASK_MISMATCH",
                    "patch access_mode is only available for small_patch tasks",
                    crew=crew,
                    task_kind=kind,
                )
            if request.patch_policy is None:
                return self._reject(
                    "PATCH_POLICY_REQUIRED",
                    "patch access_mode requires an explicit bounded patch_policy",
                    crew=crew,
                    task_kind=kind,
                )
            if not request.patch_policy.verification_command_ids:
                return self._reject(
                    "VERIFICATION_COMMAND_REQUIRED",
                    "small_patch requires at least one explicit verification command",
                    crew=crew,
                    task_kind=kind,
                )
        elif request.patch_policy is not None:
            return self._reject(
                "PATCH_POLICY_NOT_APPLICABLE",
                "patch_policy is only accepted for patch access_mode",
                crew=crew,
                task_kind=kind,
            )

        try:
            descriptor = self._registry.descriptor(crew)
        except ValueError:
            return self._reject("UNKNOWN_CREW", "unknown Crew backend", crew=crew, task_kind=kind)

        if not descriptor.dispatch_ready:
            return self._reject(
                "CREW_NOT_CONTROLLED_READY",
                "selected Crew has no accepted Captain-controlled dispatch route yet",
                crew=crew,
                task_kind=kind,
            )

        required = self._registry.required_capabilities(kind, request.required_capabilities)
        missing = sorted(set(required) - set(descriptor.controlled_capabilities))
        if missing:
            return {
                **self._reject(
                    "CAPABILITY_MISMATCH",
                    "selected Crew controlled route does not provide all required capabilities",
                    crew=crew,
                    task_kind=kind,
                ),
                "missing_capabilities": missing,
            }

        dispatcher = self._dispatchers.get(crew)
        if dispatcher is None:
            # Descriptor readiness and actual composition must agree.  Failing
            # closed prevents configuration drift from reaching legacy routes.
            return self._reject(
                "CREW_DISPATCHER_UNAVAILABLE",
                "controlled dispatcher is not configured",
                crew=crew,
                task_kind=kind,
            )

        result = dict(dispatcher(request) or {})
        if not result.get("ok"):
            return {
                "ok": False,
                "schema": "tp-voyager.dispatch/v1",
                "reason_code": str(result.get("reason_code") or "DISPATCH_FAILED"),
                "crew": crew,
                "task_kind": kind,
                "dispatch_performed": bool(result.get("dispatch_performed", False)),
                "detail": str(result.get("detail") or "controlled Crew dispatcher refused the task"),
            }
        return {
            **result,
            "ok": True,
            "schema": "tp-voyager.dispatch/v1",
            "crew": crew,
            "task_kind": kind,
            "selection_performed": False,
            "dispatch_performed": True,
        }

    @staticmethod
    def _reject(reason_code: str, detail: str, *, crew: str = "", task_kind: str = "") -> dict[str, Any]:
        return {
            "ok": False,
            "schema": "tp-voyager.dispatch/v1",
            "reason_code": reason_code,
            "detail": detail,
            "crew": crew or None,
            "task_kind": task_kind or None,
            "selection_performed": False,
            "dispatch_performed": False,
        }
