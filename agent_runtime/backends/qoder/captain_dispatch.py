"""TP-Voyager Captain adapter for controlled Qoder ACP routes."""

from __future__ import annotations

import subprocess
from typing import Any

from agent_runtime.application.dispatch.workspace import PatchWorkspace, PatchWorkspaceError, PatchWorkspaceService
from agent_runtime.application.task_launch_service import TaskLaunchRequest, TaskLaunchService
from agent_runtime.domain.dispatch import CaptainDispatchRequest, PatchPolicy


class QoderReadOnlyDispatcher:
    """Translate vendor-neutral Captain intent into accepted Qoder routes."""

    def __init__(
        self,
        launch_service: TaskLaunchService,
        *,
        patch_workspaces: PatchWorkspaceService | None = None,
    ) -> None:
        self._launch_service = launch_service
        self._patch_workspaces = patch_workspaces

    def __call__(self, request: CaptainDispatchRequest) -> dict[str, Any]:
        if request.access_mode == "patch":
            return self._dispatch_patch(request)
        return self._dispatch_read_only(request)

    def _dispatch_read_only(self, request: CaptainDispatchRequest) -> dict[str, Any]:
        if request.access_mode != "read_only":
            return self._reject("ACCESS_MODE_NOT_AVAILABLE", "Qoder Captain route does not support this access mode")
        timeout = int(request.timeout_seconds)
        if timeout < 2:
            return self._reject("INVALID_REQUEST", "timeout_seconds must be at least 2")
        idle = max(1, min(180, timeout // 2))
        result = self._launch_service.start(
            TaskLaunchRequest(
                prompt=request.objective,
                runtime="qoder",
                route="acp_read_only",
                cwd=request.cwd,
                timeout_seconds=timeout,
                model=request.model,
                idempotency_key=request.idempotency_key,
                idle_timeout_seconds=idle,
                max_task_duration_seconds=timeout,
                execution_mode="background",
            )
        )
        if not result.get("ok"):
            return self._reject("DISPATCH_FAILED", str(result.get("error") or "Qoder controlled dispatch failed"))
        return {**result, "dispatch_performed": True, "access_mode": "read_only"}

    def _dispatch_patch(self, request: CaptainDispatchRequest) -> dict[str, Any]:
        policy = request.patch_policy
        if policy is None:
            return self._reject("PATCH_POLICY_REQUIRED", "Qoder patch route requires patch_policy")
        if self._patch_workspaces is None:
            return self._reject("PATCH_WORKSPACE_UNAVAILABLE", "isolated patch workspace service is not configured")
        if not str(request.cwd or "").strip():
            return self._reject("INVALID_REQUEST", "cwd is required for patch mode")
        timeout = int(request.timeout_seconds)
        if timeout < 2:
            return self._reject("INVALID_REQUEST", "timeout_seconds must be at least 2")

        try:
            workspace = self._patch_workspaces.prepare(request.cwd, idempotency_key=request.idempotency_key)
        except PatchWorkspaceError as exc:
            return self._reject("PATCH_WORKSPACE_REJECTED", str(exc))

        idle = max(1, min(180, timeout // 2))
        result = self._launch_service.start(
            TaskLaunchRequest(
                prompt=self._build_patch_prompt(request.objective, policy),
                runtime="qoder",
                route="acp_patch",
                cwd=workspace.worktree_root,
                timeout_seconds=timeout,
                model=request.model,
                idempotency_key=request.idempotency_key,
                idle_timeout_seconds=idle,
                max_task_duration_seconds=timeout,
                execution_mode="background",
                allowed_paths=list(policy.allowed_paths),
                forbidden_paths=list(policy.forbidden_paths),
                verification_command_specs=list(policy.verification_commands()),
                max_changed_files=policy.max_changed_files,
                max_diff_lines=policy.max_diff_lines,
                verification_timeout_seconds=policy.verification_timeout_seconds,
                require_patch=True,
                source_cwd=workspace.source_root,
                workspace_mode="patch_worktree",
                workspace_base_revision=workspace.base_revision,
                patch_policy=policy.to_dict(),
            )
        )
        if not result.get("ok"):
            if not workspace.reused:
                self._patch_workspaces.cleanup(workspace)
            return self._reject("DISPATCH_FAILED", str(result.get("error") or "Qoder patch dispatch failed"))
        if bool(result.get("replayed")) and not workspace.reused:
            self._patch_workspaces.cleanup(workspace)
        return {
            **result,
            "dispatch_performed": True,
            "access_mode": "patch",
            "workspace_isolated": True,
            "base_revision": workspace.base_revision,
            "allowed_paths": list(policy.allowed_paths),
            "command_ids": [item.command_id for item in policy.commands],
        }

    @staticmethod
    def _command_text(argv: tuple[str, ...]) -> str:
        return subprocess.list2cmdline(list(argv))

    @classmethod
    def _build_patch_prompt(cls, objective: str, policy: PatchPolicy) -> str:
        allowed = "\n".join(f"- {item}" for item in policy.allowed_paths)
        commands = "\n".join(
            f"- {item.command_id}: {cls._command_text(item.argv)}"
            for item in policy.commands
        ) or "- none"
        return (
            "# TP-Voyager controlled patch task\n\n"
            "You are a Crew worker operating inside an isolated Git worktree. "
            "Read and write only the allowed paths. Run only the exact authorized commands. "
            "Do not request broader permissions or create subagents. If the task cannot fit the policy, report the blocker.\n\n"
            f"## Objective\n{str(objective).strip()}\n\n"
            f"## Allowed paths\n{allowed}\n\n"
            f"## Authorized commands\n{commands}\n"
        )

    @staticmethod
    def _reject(reason_code: str, detail: str) -> dict[str, Any]:
        return {"ok": False, "reason_code": reason_code, "detail": detail, "dispatch_performed": False}
