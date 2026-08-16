"""Captain-facing TP-Voyager dispatch contracts.

The Captain chooses a Crew and supplies a bounded execution policy.  Vendor
CLI flags never cross this boundary.  Patch policy is deliberately small and
contains only host-enforceable limits; it is not a second task state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
import re
from typing import Any

from agent_runtime.domain.run_control import RunControlSpec


_MANDATORY_FORBIDDEN = (".git", ".codebuddy", ".qoder")


def _safe_relpath(value: object, *, field_name: str) -> str:
    raw = str(value or "").strip().replace("\\", "/").strip("/")
    pure = PurePosixPath(raw)
    if (
        not raw
        or str(value or "").strip().replace("\\", "/").startswith("/")
        or (len(raw) >= 2 and raw[1] == ":")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"{field_name} contains an unsafe relative path")
    if len(raw) > 512:
        raise ValueError(f"{field_name} path is too long")
    return raw


def _relative_path_parts(value: object) -> tuple[str, ...] | None:
    """Return safe relative path components without dot-prefix rewriting.

    This helper deliberately preserves component names such as ``.src`` and
    ``.env``.  Security checks must compare path components, never use string
    trimming such as ``lstrip("./")`` which aliases distinct names.
    """
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        return None
    pure = PurePosixPath(raw)
    parts = tuple(pure.parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return parts


def relative_path_matches_prefix(path: object, prefix: object) -> bool:
    """Component-prefix match for already-relative policy paths.

    ``src/a.py`` matches ``src``; ``.src/a.py`` and ``..src/a.py`` do not.
    Invalid/absolute paths fail closed.
    """
    path_parts = _relative_path_parts(path)
    prefix_parts = _relative_path_parts(prefix)
    if path_parts is None or prefix_parts is None:
        return False
    return (
        len(path_parts) >= len(prefix_parts)
        and path_parts[: len(prefix_parts)] == prefix_parts
    )


def relative_path_matches_any(path: object, prefixes: tuple[str, ...]) -> bool:
    """Return True when ``path`` is inside any component-prefix root."""
    return any(relative_path_matches_prefix(path, prefix) for prefix in prefixes)


@dataclass(frozen=True)
class CommandSpec:
    """One Captain-authorized command, represented as argv (never shell text)."""

    command_id: str
    argv: tuple[str, ...]
    cwd: str = "."

    @classmethod
    def from_dict(cls, value: object) -> "CommandSpec":
        data = value if isinstance(value, dict) else {}
        command_id = str(data.get("id") or data.get("command_id") or "").strip()
        raw_argv = data.get("argv")
        if not command_id or len(command_id) > 80:
            raise ValueError("command spec requires a bounded id")
        if not isinstance(raw_argv, list) or not raw_argv or len(raw_argv) > 64:
            raise ValueError(f"command {command_id!r} requires a bounded argv list")
        argv = tuple(str(item) for item in raw_argv)
        if any((not item) or len(item) > 2_000 or "\x00" in item for item in argv):
            raise ValueError(f"command {command_id!r} contains an invalid argv item")
        cwd_raw = str(data.get("cwd") or ".").strip().replace("\\", "/")
        if cwd_raw in {"", "."}:
            cwd = "."
        else:
            cwd = _safe_relpath(cwd_raw, field_name=f"command {command_id} cwd")
        return cls(command_id=command_id, argv=argv, cwd=cwd)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.command_id, "argv": list(self.argv), "cwd": self.cwd}


@dataclass(frozen=True)
class PatchPolicy:
    """Host-enforced patch boundary supplied by the Captain.

    The policy is intentionally per task.  TP-Voyager does not infer hidden
    commands or widen paths on the worker's behalf.
    """

    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...] = _MANDATORY_FORBIDDEN
    commands: tuple[CommandSpec, ...] = ()
    verification_command_ids: tuple[str, ...] = ()
    max_changed_files: int = 8
    max_diff_lines: int = 600
    verification_timeout_seconds: int = 900

    @classmethod
    def from_dict(cls, value: object) -> "PatchPolicy":
        if not isinstance(value, dict):
            raise ValueError("patch_policy must be an object")
        raw_allowed = value.get("allowed_paths")
        if not isinstance(raw_allowed, list) or not raw_allowed:
            raise ValueError("patch_policy.allowed_paths must be a non-empty list")
        allowed: list[str] = []
        for item in raw_allowed:
            path = _safe_relpath(item, field_name="allowed_paths")
            if path not in allowed:
                allowed.append(path)

        forbidden = list(_MANDATORY_FORBIDDEN)
        raw_forbidden = value.get("forbidden_paths")
        if raw_forbidden is not None:
            if not isinstance(raw_forbidden, list):
                raise ValueError("patch_policy.forbidden_paths must be a list")
            for item in raw_forbidden:
                path = _safe_relpath(item, field_name="forbidden_paths")
                if path not in forbidden:
                    forbidden.append(path)

        raw_commands = value.get("commands") or []
        if not isinstance(raw_commands, list) or len(raw_commands) > 16:
            raise ValueError("patch_policy.commands must contain at most 16 command specs")
        commands = tuple(CommandSpec.from_dict(item) for item in raw_commands)
        ids = [item.command_id for item in commands]
        if len(ids) != len(set(ids)):
            raise ValueError("patch_policy command ids must be unique")

        raw_verify = value.get("verification_command_ids") or []
        if not isinstance(raw_verify, list) or len(raw_verify) > 16:
            raise ValueError("verification_command_ids must be a bounded list")
        verify = tuple(str(item).strip() for item in raw_verify if str(item).strip())
        unknown = sorted(set(verify) - set(ids))
        if unknown:
            raise ValueError(
                "verification_command_ids reference undeclared commands: " + ", ".join(unknown)
            )

        max_changed_files = int(value.get("max_changed_files") or 8)
        max_diff_lines = int(value.get("max_diff_lines") or 600)
        verification_timeout_seconds = int(value.get("verification_timeout_seconds") or 900)
        if max_changed_files <= 0 or max_changed_files > 100:
            raise ValueError("max_changed_files must be between 1 and 100")
        if max_diff_lines <= 0 or max_diff_lines > 20_000:
            raise ValueError("max_diff_lines must be between 1 and 20000")
        if verification_timeout_seconds <= 0 or verification_timeout_seconds > 7_200:
            raise ValueError("verification_timeout_seconds must be between 1 and 7200")

        return cls(
            allowed_paths=tuple(allowed),
            forbidden_paths=tuple(forbidden),
            commands=commands,
            verification_command_ids=verify,
            max_changed_files=max_changed_files,
            max_diff_lines=max_diff_lines,
            verification_timeout_seconds=verification_timeout_seconds,
        )

    def verification_commands(self) -> tuple[CommandSpec, ...]:
        wanted = set(self.verification_command_ids)
        return tuple(item for item in self.commands if item.command_id in wanted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "commands": [item.to_dict() for item in self.commands],
            "verification_command_ids": list(self.verification_command_ids),
            "max_changed_files": self.max_changed_files,
            "max_diff_lines": self.max_diff_lines,
            "verification_timeout_seconds": self.verification_timeout_seconds,
        }


@dataclass(frozen=True)
class VerificationPolicy:
    """Exact commands allowed inside a disposable verification workspace."""

    commands: tuple[CommandSpec, ...]
    timeout_seconds: int = 900

    @classmethod
    def from_dict(cls, value: object) -> "VerificationPolicy":
        if not isinstance(value, dict):
            raise ValueError("verification_policy must be an object")
        raw = value.get("commands")
        if not isinstance(raw, list) or not raw or len(raw) > 16:
            raise ValueError("verification_policy.commands must be a non-empty bounded list")
        commands = tuple(CommandSpec.from_dict(item) for item in raw)
        ids = [item.command_id for item in commands]
        if len(ids) != len(set(ids)):
            raise ValueError("verification_policy command ids must be unique")
        try:
            timeout = int(value.get("timeout_seconds") or 900)
        except (TypeError, ValueError) as exc:
            raise ValueError("verification_policy.timeout_seconds is invalid") from exc
        if timeout <= 0 or timeout > 7200:
            raise ValueError("verification_policy.timeout_seconds must be between 1 and 7200")
        return cls(commands=commands, timeout_seconds=timeout)

    def to_dict(self) -> dict[str, Any]:
        return {"commands": [item.to_dict() for item in self.commands], "timeout_seconds": self.timeout_seconds}


@dataclass(frozen=True)
class ModelPolicy:
    """Passenger/Captain supplied allow-list; never an automatic selector."""

    allowed_models: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: object) -> "ModelPolicy":
        if not isinstance(value, dict):
            raise ValueError("model_policy must be an object")
        raw = value.get("allowed_models")
        if not isinstance(raw, list) or not raw:
            raise ValueError("model_policy.allowed_models must be a non-empty list")
        models: list[str] = []
        for item in raw:
            model = str(item or "").strip()
            if not model or len(model) > 160 or "\x00" in model:
                raise ValueError("model_policy contains an invalid model id")
            if model not in models:
                models.append(model)
        if len(models) > 64:
            raise ValueError("model_policy allows at most 64 models")
        return cls(tuple(models))

    def to_dict(self) -> dict[str, Any]:
        return {"allowed_models": list(self.allowed_models)}


@dataclass(frozen=True)
class ReadScope:
    """Vendor-neutral Captain intent for a bounded read-only filesystem scope."""

    files: tuple[str, ...] = ()
    directories: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()
    max_files: int = 256
    max_bytes: int = 8 * 1024 * 1024

    @classmethod
    def from_dict(cls, value: object) -> "ReadScope":
        if not isinstance(value, dict):
            raise ValueError("read_scope must be an object")

        def paths(name: str) -> tuple[str, ...]:
            raw = value.get(name) or []
            if not isinstance(raw, list):
                raise ValueError(f"read_scope.{name} must be a list")
            out: list[str] = []
            for item in raw:
                path = _safe_relpath(item, field_name=f"read_scope.{name}")
                if path not in out:
                    out.append(path)
            if len(out) > 256:
                raise ValueError(f"read_scope.{name} exceeds 256 entries")
            return tuple(out)

        raw_globs = value.get("globs") or []
        if not isinstance(raw_globs, list):
            raise ValueError("read_scope.globs must be a list")
        globs: list[str] = []
        for item in raw_globs:
            pattern = str(item or "").strip().replace("\\", "/")
            pure = PurePosixPath(pattern)
            if (
                not pattern
                or pattern.startswith("/")
                or (len(pattern) >= 2 and pattern[1] == ":")
                or any(part in {"", ".", ".."} for part in pure.parts)
                or len(pattern) > 512
            ):
                raise ValueError("read_scope.globs contains an unsafe pattern")
            if pattern not in globs:
                globs.append(pattern)
        if len(globs) > 128:
            raise ValueError("read_scope.globs exceeds 128 entries")
        max_files = int(value.get("max_files") or 256)
        max_bytes = int(value.get("max_bytes") or (8 * 1024 * 1024))
        if max_files <= 0 or max_files > 256:
            raise ValueError("read_scope.max_files must be between 1 and 256")
        if max_bytes <= 0 or max_bytes > 8 * 1024 * 1024:
            raise ValueError("read_scope.max_bytes must be between 1 and 8388608")
        result = cls(
            files=paths("files"), directories=paths("directories"), globs=tuple(globs),
            max_files=max_files, max_bytes=max_bytes,
        )
        if not (result.files or result.directories or result.globs):
            raise ValueError("read_scope must contain files, directories, or globs")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": list(self.files),
            "directories": list(self.directories),
            "globs": list(self.globs),
            "max_files": self.max_files,
            "max_bytes": self.max_bytes,
        }


@dataclass(frozen=True)
class RepositoryResearchSpec:
    """Captain-supplied contract for one bounded external GitHub research task."""

    url: str
    target_directory: str
    max_size_bytes: int
    report_path: str = "reports/repository-research.md"

    @classmethod
    def from_dict(cls, value: object) -> "RepositoryResearchSpec":
        if not isinstance(value, dict):
            raise ValueError("repository_research must be an object")
        url = str(value.get("url") or "").strip()
        target = str(value.get("target_directory") or "").strip()
        report = _safe_relpath(
            value.get("report_path") or "reports/repository-research.md",
            field_name="repository_research.report_path",
        )
        if not url or len(url) > 2048 or "\x00" in url:
            raise ValueError("repository_research.url is invalid")
        if not target or len(target) > 2048 or "\x00" in target:
            raise ValueError("repository_research.target_directory is invalid")
        max_size = int(value.get("max_size_bytes") or 0)
        if max_size <= 0 or max_size > 250 * 1024 * 1024:
            raise ValueError("repository_research.max_size_bytes must be between 1 and 262144000")
        if not (report == "reports" or report.startswith("reports/")):
            raise ValueError("repository_research.report_path must stay under reports/")
        if not report.lower().endswith((".md", ".txt")):
            raise ValueError("repository_research.report_path must be a Markdown or text report")
        return cls(url=url, target_directory=target, max_size_bytes=max_size, report_path=report)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "target_directory": self.target_directory,
            "max_size_bytes": self.max_size_bytes,
            "report_path": self.report_path,
        }


_PROFILE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class WorkerProfileRef:
    """Immutable reference to trusted Worker role text plus optional model constraint."""

    name: str
    version: str
    sha256: str
    allowed_models: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: object) -> "WorkerProfileRef":
        if not isinstance(value, dict):
            raise ValueError("worker_profile_ref must be an object")
        name = str(value.get("name") or "").strip()
        version = str(value.get("version") or "").strip()
        sha256 = str(value.get("sha256") or "").strip().lower()
        if not _PROFILE_TOKEN_RE.fullmatch(name):
            raise ValueError("worker_profile_ref.name is invalid")
        if not _PROFILE_TOKEN_RE.fullmatch(version):
            raise ValueError("worker_profile_ref.version is invalid")
        if not _SHA256_RE.fullmatch(sha256):
            raise ValueError("worker_profile_ref.sha256 must be a 64-character hex digest")
        raw_models = value.get("allowed_models") or []
        if not isinstance(raw_models, list) or len(raw_models) > 64:
            raise ValueError("worker_profile_ref.allowed_models must be a bounded list")
        allowed: list[str] = []
        for item in raw_models:
            model = str(item or "").strip()
            if not model or len(model) > 160 or "\x00" in model:
                raise ValueError("worker_profile_ref.allowed_models contains an invalid model id")
            if model not in allowed:
                allowed.append(model)
        return cls(name=name, version=version, sha256=sha256, allowed_models=tuple(allowed))

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "version": self.version, "sha256": self.sha256}
        if self.allowed_models:
            data["allowed_models"] = list(self.allowed_models)
        return data

    @property
    def profile_id(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class WorkerSkillRef(WorkerProfileRef):
    """Hash-pinned, operator-owned Worker Skill reference."""

    allowed_crews: tuple[str, ...] = ()
    allowed_task_kinds: tuple[str, ...] = ()
    allowed_access_modes: tuple[str, ...] = ()
    max_bytes: int = 64 * 1024
    artifact_consumer: bool = False

    @classmethod
    def from_dict(cls, value: object) -> "WorkerSkillRef":
        base = WorkerProfileRef.from_dict(value)
        assert isinstance(value, dict)

        def bounded_tokens(field: str, allowed: frozenset[str]) -> tuple[str, ...]:
            raw = value.get(field)
            if not isinstance(raw, list) or not raw or len(raw) > len(allowed):
                raise ValueError(f"worker_skill_ref.{field} must be a non-empty bounded list")
            normalized = tuple(dict.fromkeys(str(item or "").strip().lower() for item in raw))
            if len(normalized) != len(raw) or any(item not in allowed for item in normalized):
                raise ValueError(f"worker_skill_ref.{field} contains an invalid value")
            return normalized

        crews = bounded_tokens("allowed_crews", frozenset({"codebuddy", "qoder"}))
        task_kinds = bounded_tokens(
            "allowed_task_kinds",
            frozenset({"research", "repository_research", "code_review", "small_patch", "test_failure_triage", "verify_only"}),
        )
        access_modes = bounded_tokens("allowed_access_modes", frozenset({"read_only", "patch", "verification"}))
        try:
            max_bytes = int(value.get("max_bytes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("worker_skill_ref.max_bytes is invalid") from exc
        if max_bytes <= 0 or max_bytes > 64 * 1024:
            raise ValueError("worker_skill_ref.max_bytes must be between 1 and 65536")
        artifact_consumer = value.get("artifact_consumer")
        if not isinstance(artifact_consumer, bool):
            raise ValueError("worker_skill_ref.artifact_consumer must be boolean")
        return cls(
            base.name, base.version, base.sha256, base.allowed_models,
            crews, task_kinds, access_modes, max_bytes, artifact_consumer,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "allowed_crews": list(self.allowed_crews),
            "allowed_task_kinds": list(self.allowed_task_kinds),
            "allowed_access_modes": list(self.allowed_access_modes),
            "max_bytes": self.max_bytes,
            "artifact_consumer": self.artifact_consumer,
        }


_INPUT_ARTIFACT_KINDS = frozenset({"research_summary", "technical_report", "structured_evidence", "bounded_text"})


@dataclass(frozen=True)
class InputArtifactRef:
    artifact_id: str
    source_task_id: str
    kind: str
    sha256: str
    byte_size: int

    @classmethod
    def from_dict(cls, value: object) -> "InputArtifactRef":
        if not isinstance(value, dict):
            raise ValueError("input_artifact_ref must be an object")
        artifact_id = str(value.get("artifact_id") or "").strip()
        source_task_id = str(value.get("source_task_id") or "").strip()
        kind = str(value.get("kind") or "").strip()
        sha256 = str(value.get("sha256") or "").strip().lower()
        try:
            byte_size = int(value.get("byte_size"))
        except (TypeError, ValueError) as exc:
            raise ValueError("input_artifact_ref.byte_size is invalid") from exc
        if not artifact_id or len(artifact_id) > 128 or not source_task_id or len(source_task_id) > 128:
            raise ValueError("input_artifact_ref IDs are invalid")
        if kind not in _INPUT_ARTIFACT_KINDS:
            raise ValueError("ARTIFACT_TYPE_NOT_ALLOWED_AS_INPUT")
        if not _SHA256_RE.fullmatch(sha256) or byte_size < 0 or byte_size > 512 * 1024:
            raise ValueError("input_artifact_ref hash or size is invalid")
        return cls(artifact_id, source_task_id, kind, sha256, byte_size)

    def to_dict(self) -> dict[str, Any]:
        return {"artifact_id": self.artifact_id, "source_task_id": self.source_task_id,
                "kind": self.kind, "sha256": self.sha256, "byte_size": self.byte_size}


def canonical_input_artifact_refs(value: object) -> tuple[InputArtifactRef, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("INPUT_ARTIFACT_LIMIT_EXCEEDED")
    refs = [InputArtifactRef.from_dict(item) for item in value]
    unique: dict[tuple[str, str, str], InputArtifactRef] = {}
    for item in refs:
        key = (item.source_task_id, item.artifact_id, item.sha256)
        if key in unique and unique[key] != item:
            raise ValueError("INPUT_ARTIFACT_REF_CONFLICT")
        unique[key] = item
    if len(unique) > 4:
        raise ValueError("INPUT_ARTIFACT_LIMIT_EXCEEDED")
    if sum(item.byte_size for item in unique.values()) > 2 * 1024 * 1024:
        raise ValueError("INPUT_ARTIFACT_TOO_LARGE")
    return tuple(unique[key] for key in sorted(unique))


@dataclass(frozen=True)
class TrustedInstructionRef:
    """Hash-pinned Captain reference to operator-owned trusted instruction text."""

    root_alias: str
    path: str
    sha256: str
    max_bytes: int = 64 * 1024

    @classmethod
    def from_dict(cls, value: object) -> "TrustedInstructionRef":
        if not isinstance(value, dict):
            raise ValueError("trusted_instruction_ref must be an object")
        root_alias = str(value.get("root_alias") or "").strip()
        if not _PROFILE_TOKEN_RE.fullmatch(root_alias):
            raise ValueError("trusted_instruction_ref.root_alias is invalid")
        path = _safe_relpath(value.get("path"), field_name="trusted_instruction_ref.path")
        sha256 = str(value.get("sha256") or "").strip().lower()
        if not _SHA256_RE.fullmatch(sha256):
            raise ValueError("trusted_instruction_ref.sha256 must be a 64-character hex digest")
        try:
            max_bytes = int(value.get("max_bytes") or (64 * 1024))
        except (TypeError, ValueError) as exc:
            raise ValueError("trusted_instruction_ref.max_bytes is invalid") from exc
        if max_bytes <= 0 or max_bytes > 256 * 1024:
            raise ValueError("trusted_instruction_ref.max_bytes must be between 1 and 262144")
        return cls(root_alias, path, sha256, max_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {"root_alias": self.root_alias, "path": self.path, "sha256": self.sha256, "max_bytes": self.max_bytes}


@dataclass(frozen=True)
class ApplyReceipt:
    """Captain-Host assertion about one accepted Patch Artifact application.

    Runtime never trusts this receipt blindly.  Verification reconstructs the
    subject from base_revision + Patch Artifact and compares result_tree_hash.
    """

    repository_identity: str
    base_commit: str
    base_tree_hash: str
    patch_artifact_id: str
    patch_sha256: str
    result_tree_hash: str
    changed_files: tuple[str, ...]
    applied_by: str
    applied_at: str
    git_status_digest: str
    conflicts: tuple[str, ...]
    receipt_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> "ApplyReceipt":
        if not isinstance(value, dict):
            raise ValueError("apply_receipt must be an object")
        if str(value.get("schema") or "").strip() != "tp-voyager.apply_receipt/v1":
            raise ValueError("apply_receipt.schema must be tp-voyager.apply_receipt/v1")
        def text(name: str, limit: int = 512) -> str:
            raw = str(value.get(name) or "").strip()
            if not raw or len(raw) > limit or "\x00" in raw:
                raise ValueError(f"apply_receipt.{name} is invalid")
            return raw
        def sha(name: str) -> str:
            raw = text(name, 64).lower()
            if not _SHA256_RE.fullmatch(raw):
                raise ValueError(f"apply_receipt.{name} must be sha256")
            return raw
        def string_list(name: str, max_items: int = 256) -> tuple[str, ...]:
            raw = value.get(name)
            if not isinstance(raw, list) or len(raw) > max_items:
                raise ValueError(f"apply_receipt.{name} must be a bounded list")
            out: list[str] = []
            for item in raw:
                entry = str(item or "").strip().replace("\\", "/")
                if not entry or len(entry) > 512 or "\x00" in entry:
                    raise ValueError(f"apply_receipt.{name} contains invalid entry")
                if entry not in out:
                    out.append(entry)
            return tuple(out)
        applied_by = text("applied_by", 80)
        if applied_by != "captain_host":
            raise ValueError("apply_receipt.applied_by must be captain_host")
        changed_files = tuple(_safe_relpath(item, field_name="apply_receipt.changed_files") for item in string_list("changed_files"))
        return cls(
            repository_identity=text("repository_identity", 1024),
            base_commit=text("base_commit", 160),
            base_tree_hash=text("base_tree_hash", 160),
            patch_artifact_id=text("patch_artifact_id", 128),
            patch_sha256=sha("patch_sha256"),
            result_tree_hash=text("result_tree_hash", 160),
            changed_files=changed_files,
            applied_by=applied_by,
            applied_at=text("applied_at", 80),
            git_status_digest=sha("git_status_digest"),
            conflicts=string_list("conflicts", 64),
            receipt_sha256=sha("receipt_sha256"),
        )

    def canonical_body(self) -> dict[str, Any]:
        return {
            "schema": "tp-voyager.apply_receipt/v1",
            "repository_identity": self.repository_identity,
            "base_commit": self.base_commit,
            "base_tree_hash": self.base_tree_hash,
            "patch_artifact_id": self.patch_artifact_id,
            "patch_sha256": self.patch_sha256,
            "result_tree_hash": self.result_tree_hash,
            "changed_files": list(self.changed_files),
            "applied_by": self.applied_by,
            "applied_at": self.applied_at,
            "git_status_digest": self.git_status_digest,
            "conflicts": list(self.conflicts),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_body(), "receipt_sha256": self.receipt_sha256}


@dataclass(frozen=True)
class RepositorySnapshotRef:
    """Reference to one Runtime-owned repository_research snapshot."""

    source_task_id: str
    commit: str
    scope_manifest_id: str
    scope_root_hash: str

    @classmethod
    def from_dict(cls, value: object) -> "RepositorySnapshotRef":
        if not isinstance(value, dict):
            raise ValueError("repository_snapshot_ref must be an object")
        source_task_id = str(value.get("source_task_id") or "").strip()
        commit = str(value.get("commit") or "").strip()
        manifest = str(value.get("scope_manifest_id") or "").strip()
        root_hash = str(value.get("scope_root_hash") or "").strip().lower()
        if not source_task_id or len(source_task_id) > 128:
            raise ValueError("repository_snapshot_ref.source_task_id is invalid")
        if not commit or len(commit) > 160:
            raise ValueError("repository_snapshot_ref.commit is invalid")
        if not manifest or len(manifest) > 80:
            raise ValueError("repository_snapshot_ref.scope_manifest_id is invalid")
        if not _SHA256_RE.fullmatch(root_hash):
            raise ValueError("repository_snapshot_ref.scope_root_hash must be sha256")
        return cls(source_task_id, commit, manifest, root_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_task_id": self.source_task_id,
            "commit": self.commit,
            "scope_manifest_id": self.scope_manifest_id,
            "scope_root_hash": self.scope_root_hash,
        }


@dataclass(frozen=True)
class ScopeSegmentSpec:
    """Captain-selected deterministic segment of a larger Scope Manifest."""

    index: int = 0

    @classmethod
    def from_dict(cls, value: object) -> "ScopeSegmentSpec":
        if value is None:
            return cls(0)
        if not isinstance(value, dict):
            raise ValueError("scope_segment must be an object")
        try:
            index = int(value.get("index") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("scope_segment.index is invalid") from exc
        if index < 0 or index > 10000:
            raise ValueError("scope_segment.index is outside the bounded limit")
        return cls(index)

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index}


@dataclass(frozen=True)
class CaptainDispatchRequest:
    objective: str
    crew: str
    task_kind: str
    cwd: str = ""
    model: str = ""
    access_mode: str = "read_only"
    idempotency_key: str = ""
    context_id: str = ""
    timeout_seconds: int = 300
    required_capabilities: tuple[str, ...] = ()
    patch_policy: PatchPolicy | None = None
    model_policy: ModelPolicy | None = None
    read_scope: ReadScope | None = None
    resolved_read_files: tuple[str, ...] = ()
    worker_profile_ref: WorkerProfileRef | None = None
    worker_skill_refs: tuple[WorkerSkillRef, ...] = ()
    worker_skill_content: tuple[str, ...] = ()
    input_artifact_refs: tuple[InputArtifactRef, ...] = ()
    input_artifact_content: tuple[str, ...] = ()
    trusted_instruction_refs: tuple[TrustedInstructionRef, ...] = ()
    trusted_instruction_content: tuple[str, ...] = ()
    run_control: RunControlSpec | None = None
    step_key: str = ""
    apply_receipt: ApplyReceipt | None = None
    verification_policy: VerificationPolicy | None = None
    verification_subject: dict[str, Any] = field(default_factory=dict)
    workspace_source_cwd: str = ""
    workspace_mode: str = ""
    workspace_base_revision: str = ""
    captain_request_contract: dict[str, Any] = field(default_factory=dict)
    effective_model_policy: dict[str, Any] = field(default_factory=dict)
    repository_research: dict[str, Any] | None = None
    repository_snapshot_ref: RepositorySnapshotRef | None = None
    scope_segment: ScopeSegmentSpec = field(default_factory=ScopeSegmentSpec)
    worker_profile_content: str = ""
    correlation_id: str = ""

    def routing_metadata(self) -> dict[str, Any]:
        """Return bounded routing facts safe to persist with the durable Session."""
        data: dict[str, Any] = {}
        if self.model_policy is not None:
            data["model_policy"] = self.model_policy.to_dict()
        if self.read_scope is not None:
            scope = self.read_scope.to_dict()
            scope["resolved_files"] = list(self.resolved_read_files)
            data["read_scope"] = scope
        if self.worker_profile_ref is not None:
            data["worker_profile_ref"] = self.worker_profile_ref.to_dict()
        if self.worker_skill_refs:
            data["worker_skill_refs"] = [item.to_dict() for item in self.worker_skill_refs]
        if self.input_artifact_refs:
            data["input_artifact_refs"] = [item.to_dict() for item in self.input_artifact_refs]
        if self.trusted_instruction_refs:
            data["trusted_instruction_refs"] = [item.to_dict() for item in self.trusted_instruction_refs]
        if self.run_control is not None:
            data["run_control"] = self.run_control.to_dict()
        if self.step_key:
            data["step_key"] = self.step_key
        if self.apply_receipt is not None:
            data["apply_receipt"] = self.apply_receipt.to_dict()
        if self.verification_policy is not None:
            data["verification_policy"] = self.verification_policy.to_dict()
        if self.verification_subject:
            data["verification_subject"] = dict(self.verification_subject)
        if self.captain_request_contract:
            data["captain_request_contract"] = dict(self.captain_request_contract)
        if self.effective_model_policy:
            data["effective_model_policy"] = dict(self.effective_model_policy)
        if self.repository_research is not None:
            data["repository_research"] = dict(self.repository_research)
        if self.repository_snapshot_ref is not None:
            data["repository_snapshot_ref"] = self.repository_snapshot_ref.to_dict()
        if self.repository_research is not None or self.repository_snapshot_ref is not None or self.scope_segment.index:
            data["scope_segment"] = self.scope_segment.to_dict()
        if self.correlation_id:
            data["correlation_id"] = self.correlation_id
        return data
