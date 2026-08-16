"""Deterministic V1 verifiers: scope, commands, artifacts, and patch facts."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from agent_runtime.verification.artifacts.capture import (
    ArtifactCaptureBatch,
    WorkspaceBaseline,
)
from agent_runtime.domain.enums import (
    EvidenceOrigin,
    EvidenceType,
    TrustState,
)
from agent_runtime.domain.evidence import Evidence
from agent_runtime.domain.dispatch import CommandSpec, relative_path_matches_prefix
from agent_runtime.domain.ids import new_evidence_id


@dataclass(frozen=True)
class VerificationPlan:
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = (".git",)
    # Legacy command strings are kept for V2 history compatibility.  New
    # TP-Voyager Captain dispatch uses argv-based command_specs only.
    commands: tuple[str, ...] = ()
    command_specs: tuple[CommandSpec, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    max_changed_files: int = 0
    max_diff_lines: int = 0
    command_timeout_seconds: float = 900.0
    require_patch: bool = False

    @property
    def requested(self) -> bool:
        return bool(
            self.allowed_paths
            or self.forbidden_paths != (".git",)
            or self.commands
            or self.command_specs
            or self.expected_artifacts
            or self.max_changed_files
            or self.max_diff_lines
            or self.require_patch
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "commands": list(self.commands),
            "command_specs": [item.to_dict() for item in self.command_specs],
            "expected_artifacts": list(self.expected_artifacts),
            "max_changed_files": self.max_changed_files,
            "max_diff_lines": self.max_diff_lines,
            "command_timeout_seconds": self.command_timeout_seconds,
            "require_patch": self.require_patch,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VerificationPlan":
        data = value if isinstance(value, dict) else {}
        def strings(key: str) -> tuple[str, ...]:
            raw = data.get(key)
            return tuple(str(item) for item in raw if isinstance(item, str)) if isinstance(raw, list) else ()
        forbidden = strings("forbidden_paths")
        raw_specs = data.get("command_specs")
        command_specs: list[CommandSpec] = []
        if isinstance(raw_specs, list):
            for item in raw_specs:
                try:
                    command_specs.append(CommandSpec.from_dict(item))
                except ValueError:
                    # Persisted malformed policy fails closed by not executing
                    # the malformed command.  Verification will expose missing
                    # expected command evidence to the caller.
                    continue
        return cls(
            allowed_paths=strings("allowed_paths"),
            forbidden_paths=forbidden or (".git",),
            commands=strings("commands"),
            command_specs=tuple(command_specs),
            expected_artifacts=strings("expected_artifacts"),
            max_changed_files=max(0, int(data.get("max_changed_files") or 0)),
            max_diff_lines=max(0, int(data.get("max_diff_lines") or 0)),
            command_timeout_seconds=max(1.0, float(data.get("command_timeout_seconds") or 900.0)),
            require_patch=bool(data.get("require_patch") or False),
        )


@dataclass
class VerificationReport:
    status: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        passed = sum(1 for item in self.checks if item.get("status") == "passed")
        failed = sum(1 for item in self.checks if item.get("status") == "failed")
        review = sum(1 for item in self.checks if item.get("status") == "needs_review")
        return {
            "status": self.status,
            "checks": list(self.checks),
            "summary": {
                "passed": passed,
                "failed": failed,
                "needs_review": review,
                "total": len(self.checks),
            },
        }


def _matches_prefix(path: str, prefix: str) -> bool:
    return relative_path_matches_prefix(path, prefix)


def _safe_expected_path(cwd: Path, relpath: str) -> Path | None:
    raw = relpath.replace("\\", "/")
    pure = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or (len(raw) >= 2 and raw[1] == ":")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        return None
    candidate = (cwd / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(cwd)
    except ValueError:
        return None
    return candidate


class VerificationService:
    def verify(
        self,
        *,
        task_id: str,
        attempt_id: str,
        cwd: str | Path,
        plan: VerificationPlan,
        capture: ArtifactCaptureBatch,
        baseline: WorkspaceBaseline,
    ) -> VerificationReport:
        if not plan.requested:
            return VerificationReport(status="NOT_REQUESTED")
        workspace = Path(cwd).resolve()
        report = VerificationReport(status="PASSED")

        self._verify_scope(report, task_id, attempt_id, plan, capture, baseline)
        self._verify_artifacts(report, task_id, attempt_id, workspace, plan, capture)
        self._verify_patch(report, task_id, attempt_id, plan, capture, baseline)

        # The patch/evidence snapshot is captured before deterministic
        # verification.  T4 verification commands are therefore required to
        # be non-mutating with respect to Git-visible workspace state.  Detect
        # accidental formatter/snapshot/source mutations so they cannot hide
        # outside the captured patch.  Ignored build outputs are naturally
        # excluded by `git status`.
        status_before_commands = self._git_workspace_sha256(workspace)
        for command in plan.commands:
            self._verify_command(
                report, task_id, attempt_id, workspace, command,
                plan.command_timeout_seconds,
            )
        for spec in plan.command_specs:
            self._verify_command_spec(
                report, task_id, attempt_id, workspace, spec,
                plan.command_timeout_seconds,
            )
        status_after_commands = self._git_workspace_sha256(workspace)
        if (plan.commands or plan.command_specs) and status_before_commands is not None and status_after_commands is not None:
            mutated = status_before_commands != status_after_commands
            self._append_check(
                report,
                task_id=task_id,
                attempt_id=attempt_id,
                name="verification_workspace_stability",
                status="failed" if mutated else "passed",
                summary=(
                    "Verification commands changed Git-visible workspace state"
                    if mutated
                    else "Verification commands left Git-visible workspace state unchanged"
                ),
                detail={
                    "status_before_sha256": status_before_commands,
                    "status_after_sha256": status_after_commands,
                },
            )

        statuses = {item.get("status") for item in report.checks}
        if "failed" in statuses:
            report.status = "FAILED"
        elif "needs_review" in statuses:
            report.status = "NEEDS_REVIEW"
        else:
            report.status = "PASSED"
        return report

    def _append_check(
        self,
        report: VerificationReport,
        *,
        task_id: str,
        attempt_id: str,
        name: str,
        status: str,
        summary: str,
        evidence_type: str = EvidenceType.REVIEW.value,
        detail: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> None:
        report.checks.append({"name": name, "status": status, "summary": summary})
        trust = {
            "passed": TrustState.VERIFIED_PASSED.value,
            "failed": TrustState.VERIFIED_FAILED.value,
            "needs_review": TrustState.NEEDS_REVIEW.value,
            "skipped": TrustState.SKIPPED.value,
        }[status]
        now = time.time()
        report.evidence.append(
            Evidence(
                evidence_id=new_evidence_id(),
                task_id=task_id,
                attempt_id=attempt_id,
                evidence_type=evidence_type,
                trust_state=trust,
                origin=EvidenceOrigin.VERIFIER.value,
                summary=summary[:2_000],
                detail_json=json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
                captured_at=now,
                created_at=now,
                artifact_id=artifact_id if evidence_type == EvidenceType.ARTIFACT.value else None,
            )
        )

    def _verify_scope(
        self,
        report: VerificationReport,
        task_id: str,
        attempt_id: str,
        plan: VerificationPlan,
        capture: ArtifactCaptureBatch,
        baseline: WorkspaceBaseline,
    ) -> None:
        changed = capture.changed_files
        forbidden = [
            path for path in changed
            if any(_matches_prefix(path, prefix) for prefix in plan.forbidden_paths)
        ]
        outside = [
            path for path in changed
            if plan.allowed_paths
            and not any(_matches_prefix(path, prefix) for prefix in plan.allowed_paths)
        ]
        too_many = bool(plan.max_changed_files and len(changed) > plan.max_changed_files)
        too_large = bool(plan.max_diff_lines and capture.patch_line_count > plan.max_diff_lines)
        status = "failed" if forbidden or outside or too_many or too_large else "passed"
        detail = {
            "changed_count": len(changed),
            "forbidden_count": len(forbidden),
            "outside_allowed_count": len(outside),
            "max_changed_files": plan.max_changed_files or None,
            "patch_line_count": capture.patch_line_count,
            "max_diff_lines": plan.max_diff_lines or None,
        }
        self._append_check(
            report,
            task_id=task_id,
            attempt_id=attempt_id,
            name="scope",
            status=status,
            summary=(
                "Workspace changes are inside the requested scope"
                if status == "passed"
                else "Workspace changes violate the requested scope"
            ),
            detail=detail,
        )
        if baseline.dirty:
            report.risks.append("workspace_was_dirty_before_dispatch")
            self._append_check(
                report,
                task_id=task_id,
                attempt_id=attempt_id,
                name="baseline_cleanliness",
                status="needs_review",
                summary="Workspace was already dirty before dispatch; attribution is not deterministic",
                detail={"baseline_changed_count": len(baseline.changed_files)},
            )

    def _verify_artifacts(
        self,
        report: VerificationReport,
        task_id: str,
        attempt_id: str,
        cwd: Path,
        plan: VerificationPlan,
        capture: ArtifactCaptureBatch,
    ) -> None:
        for relpath in plan.expected_artifacts:
            path = _safe_expected_path(cwd, relpath)
            matching = next(
                (
                    item for item in capture.artifacts
                    if item.workspace_relpath == relpath
                    and item.capture_state == "captured"
                ),
                None,
            )
            ok = path is not None and path.is_file() and matching is not None
            self._append_check(
                report,
                task_id=task_id,
                attempt_id=attempt_id,
                name=f"artifact:{relpath}"[:200],
                status="passed" if ok else "failed",
                summary=(
                    f"Expected artifact captured: {relpath}"
                    if ok
                    else f"Expected artifact is missing or was not captured: {relpath}"
                ),
                evidence_type=(
                    EvidenceType.ARTIFACT.value if matching is not None
                    else EvidenceType.FILE.value
                ),
                artifact_id=matching.artifact_id if matching is not None else None,
                detail={"exists": bool(path and path.is_file())},
            )

    def _verify_patch(
        self,
        report: VerificationReport,
        task_id: str,
        attempt_id: str,
        plan: VerificationPlan,
        capture: ArtifactCaptureBatch,
        baseline: WorkspaceBaseline,
    ) -> None:
        if not plan.require_patch:
            return
        status = "passed" if capture.patch_available else "failed"
        if capture.head_changed:
            status = "needs_review"
            report.risks.append("workspace_head_changed_during_execution")
        self._append_check(
            report,
            task_id=task_id,
            attempt_id=attempt_id,
            name="patch",
            status=status,
            summary=(
                "Git patch was captured"
                if status == "passed"
                else "Git patch could not be deterministically verified"
            ),
            detail={
                "patch_available": capture.patch_available,
                "baseline_head_present": baseline.head is not None,
                "head_changed": capture.head_changed,
            },
        )

    def _verify_command(
        self,
        report: VerificationReport,
        task_id: str,
        attempt_id: str,
        cwd: Path,
        command: str,
        timeout_seconds: float,
    ) -> None:
        started = time.monotonic()
        timed_out = False
        stdout_digest = hashlib.sha256()
        stderr_digest = hashlib.sha256()
        stdout_bytes = 0
        stderr_bytes = 0
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    command,
                    cwd=str(cwd),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=True,
                    start_new_session=(os.name != "nt"),
                )
                try:
                    process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._kill_process_tree(process)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                exit_code = process.returncode
                stdout_bytes = self._hash_file(stdout_file, stdout_digest)
                stderr_bytes = self._hash_file(stderr_file, stderr_digest)
        except OSError as exc:
            exit_code = None
            encoded = type(exc).__name__.encode("ascii", "replace")
            stderr_digest.update(encoded)
            stderr_bytes = len(encoded)
        duration = time.monotonic() - started
        passed = exit_code == 0 and not timed_out
        try:
            tokens = shlex.split(command, posix=(os.name != "nt"))
        except ValueError:
            tokens = []
        name = (Path(tokens[0]).name if tokens else "verification command")[:160]
        output_record = {
            "name": name,
            "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "exit_code": exit_code,
            "duration_seconds": round(duration, 3),
            "timed_out": timed_out,
            "stdout_sha256": stdout_digest.hexdigest(),
            "stderr_sha256": stderr_digest.hexdigest(),
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "status": "passed" if passed else "failed",
        }
        report.tests.append(output_record)
        evidence_type = (
            EvidenceType.TEST.value
            if any(token in command.lower() for token in ("test", "pytest", "unittest"))
            else EvidenceType.COMMAND.value
        )
        self._append_check(
            report,
            task_id=task_id,
            attempt_id=attempt_id,
            name=f"command:{name}"[:200],
            status="passed" if passed else "failed",
            summary=(
                f"Verification command passed: {name}"
                if passed
                else f"Verification command failed: {name}"
            ),
            evidence_type=evidence_type,
            detail=output_record,
        )

    def _verify_command_spec(
        self,
        report: VerificationReport,
        task_id: str,
        attempt_id: str,
        cwd: Path,
        spec: CommandSpec,
        timeout_seconds: float,
    ) -> None:
        started = time.monotonic()
        timed_out = False
        stdout_digest = hashlib.sha256()
        stderr_digest = hashlib.sha256()
        stdout_bytes = 0
        stderr_bytes = 0
        command_cwd = cwd if spec.cwd == "." else (cwd / spec.cwd).resolve()
        try:
            command_cwd.relative_to(cwd)
        except ValueError:
            command_cwd = cwd / "__tp_voyager_invalid_command_cwd__"
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    list(spec.argv),
                    cwd=str(command_cwd),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    start_new_session=(os.name != "nt"),
                )
                try:
                    process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._kill_process_tree(process)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                exit_code = process.returncode
                stdout_bytes = self._hash_file(stdout_file, stdout_digest)
                stderr_bytes = self._hash_file(stderr_file, stderr_digest)
        except OSError as exc:
            exit_code = None
            encoded = type(exc).__name__.encode("ascii", "replace")
            stderr_digest.update(encoded)
            stderr_bytes = len(encoded)
        duration = time.monotonic() - started
        passed = exit_code == 0 and not timed_out
        argv_json = json.dumps(list(spec.argv), ensure_ascii=False, separators=(",", ":"))
        output_record = {
            "name": spec.command_id,
            "command_id": spec.command_id,
            "argv_sha256": hashlib.sha256(argv_json.encode("utf-8")).hexdigest(),
            "exit_code": exit_code,
            "duration_seconds": round(duration, 3),
            "timed_out": timed_out,
            "stdout_sha256": stdout_digest.hexdigest(),
            "stderr_sha256": stderr_digest.hexdigest(),
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "status": "passed" if passed else "failed",
        }
        report.tests.append(output_record)
        evidence_type = (
            EvidenceType.TEST.value
            if any("test" in token.lower() for token in spec.argv)
            else EvidenceType.COMMAND.value
        )
        self._append_check(
            report,
            task_id=task_id,
            attempt_id=attempt_id,
            name=f"command:{spec.command_id}"[:200],
            status="passed" if passed else "failed",
            summary=(
                f"Verification command passed: {spec.command_id}"
                if passed
                else f"Verification command failed: {spec.command_id}"
            ),
            evidence_type=evidence_type,
            detail=output_record,
        )

    @staticmethod
    def _git_workspace_sha256(cwd: Path) -> str | None:
        """Hash Git-visible changed state, including changed file contents.

        `git status` alone is insufficient because a verification command can
        mutate an already-modified file without changing its XY status.
        """
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        digest = hashlib.sha256()
        digest.update(result.stdout)
        text = result.stdout.decode("utf-8", "replace")
        for line in text.splitlines():
            if len(line) < 4:
                continue
            raw_path = line[3:].strip()
            if " -> " in raw_path:
                raw_path = raw_path.split(" -> ", 1)[1]
            raw_path = raw_path.strip('"').replace("\\", "/")
            candidate = (cwd / raw_path).resolve()
            try:
                candidate.relative_to(cwd)
            except ValueError:
                continue
            if not candidate.is_file():
                continue
            digest.update(raw_path.encode("utf-8", "replace"))
            try:
                with candidate.open("rb") as stream:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
            except OSError:
                digest.update(b"<unreadable>")
        return digest.hexdigest()

    @staticmethod
    def _hash_file(stream: Any, digest: Any) -> int:
        stream.flush()
        stream.seek(0)
        total = 0
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        return total

    @staticmethod
    def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass
