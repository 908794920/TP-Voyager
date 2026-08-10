"""Application boundary for explicit durable task launch.

This reusable launch use-case keeps MCP adapters and durable controllers from
calling each other.  Official CodeBuddy/Qoder launchers are injected by the
composition root; this module contains no MCP or backend transport imports.

The boundary keeps selection explicit, rejects retired legacy review/session
fields, introduces no automatic fallback/retry, and leaves durable Task truth
with the existing Runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from agent_runtime.domain.dispatch import CommandSpec


TaskLauncher = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class TaskLaunchRequest:
    """Transport-neutral explicit task-launch request.

    The fields mirror the existing public ``subagent_start`` contract so a
    controller can reuse the same application boundary without importing MCP.
    Backend-specific fields remain explicit and are validated before routing.
    """

    prompt: str
    runtime: str = ""
    cwd: str = ""
    timeout_seconds: int = 300
    model: str = ""
    reasoning_effort: str = ""
    route: str = ""
    identity: str = ""
    resume_task_id: str = ""
    resume_session_id: str = ""
    review_target: str = ""
    resume_review: bool = False
    idempotency_key: str = ""
    idle_timeout_seconds: int = 180
    max_task_duration_seconds: int | None = None
    parent_task_id: str = ""
    context_id: str = ""
    agent_profile: str = ""
    execution_mode: str = "background"
    allowed_paths: list[str] | None = None
    forbidden_paths: list[str] | None = None
    verification_commands: list[str] | None = None
    verification_command_specs: list[CommandSpec] | None = None
    expected_artifacts: list[str] | None = None
    max_changed_files: int = 0
    max_diff_lines: int = 0
    verification_timeout_seconds: int = 900
    require_patch: bool = False
    source_cwd: str = ""
    workspace_mode: str = ""
    workspace_base_revision: str = ""
    patch_policy: dict[str, Any] | None = None
    routing_metadata: dict[str, Any] | None = None


class TaskLaunchService:
    """Route one explicit launch through injected runtime launchers.

    The service is deliberately stateless.  It owns only public request
    validation/routing; persistence, workers, leases and backend sessions stay
    in the existing Runtime launch implementations until later V2 phases move
    those concerns behind narrower ports.
    """

    def __init__(self, launchers: Mapping[str, TaskLauncher]) -> None:
        self._launchers = {
            str(name).strip().lower(): launcher
            for name, launcher in launchers.items()
            if str(name).strip()
        }

    def start(self, request: TaskLaunchRequest) -> dict[str, Any]:
        selected = request.runtime.strip().lower()
        if not selected:
            return {"ok": False, "error": "runtime must be explicit: codebuddy or qoder"}
        launcher = self._launchers.get(selected)
        if launcher is None:
            return {
                "ok": False,
                "error": f"Unsupported sub-agent runtime: {selected}",
            }

        common = dict(
            prompt=request.prompt,
            cwd=request.cwd,
            timeout_seconds=request.timeout_seconds,
            model=request.model,
            reasoning_effort=request.reasoning_effort,
            idempotency_key=request.idempotency_key,
            idle_timeout_seconds=request.idle_timeout_seconds,
            max_task_duration_seconds=request.max_task_duration_seconds,
            parent_task_id=request.parent_task_id,
            context_id=request.context_id,
            agent_profile=request.agent_profile,
            execution_mode=request.execution_mode,
            allowed_paths=request.allowed_paths,
            forbidden_paths=request.forbidden_paths,
            verification_commands=request.verification_commands,
            expected_artifacts=request.expected_artifacts,
            max_changed_files=request.max_changed_files,
            verification_timeout_seconds=request.verification_timeout_seconds,
            require_patch=request.require_patch,
            routing_metadata=request.routing_metadata,
        )

        official_extra = dict(
            verification_command_specs=request.verification_command_specs,
            max_diff_lines=request.max_diff_lines,
            source_cwd=request.source_cwd,
            workspace_mode=request.workspace_mode,
            workspace_base_revision=request.workspace_base_revision,
            patch_policy=request.patch_policy,
        )

        if selected == "codebuddy":
            route = request.route.strip().lower() or "sdk_context_read_only"
            if route not in {"sdk_context_read_only", "sdk_patch", "sdk_verify"}:
                return {
                    "ok": False,
                    "error": "CodeBuddy route must be sdk_context_read_only, sdk_patch or sdk_verify",
                }
            if (
                request.identity
                or request.review_target
                or request.resume_review
                or request.resume_session_id
            ):
                return {
                    "ok": False,
                    "error": (
                        "CodeBuddy uses agent_profile and resume_task_id; legacy "
                        "review/session fields are not accepted"
                    ),
                }
            return launcher(
                **common,
                **official_extra,
                route=route,
                resume_task_id=request.resume_task_id,
            )

        if selected == "qoder":
            route = request.route.strip().lower() or "acp_read_only"
            if route not in {"acp_read_only", "acp_patch", "acp_verify"}:
                return {
                    "ok": False,
                    "error": "Qoder route must be acp_read_only, acp_patch or acp_verify",
                }
            if (
                request.identity
                or request.review_target
                or request.resume_review
                or request.resume_session_id
            ):
                return {
                    "ok": False,
                    "error": (
                        "Qoder uses agent_profile and resume_task_id; legacy "
                        "review/session fields are not accepted"
                    ),
                }
            return launcher(
                **common,
                **official_extra,
                route=route,
                resume_task_id=request.resume_task_id,
            )

        # Keep this fail-closed even if a future launcher is accidentally
        # registered without adding explicit request adaptation above.
        return {
            "ok": False,
            "error": f"Unsupported sub-agent runtime: {selected}",
        }
