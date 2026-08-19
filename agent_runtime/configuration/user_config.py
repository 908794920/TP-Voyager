"""Strict user-owned TP-Voyager configuration.

The configuration is intentionally small: machine-specific Crew locations,
Crew-local concurrency limits, dispatch authorization, trusted external roots,
and reusable worker resources. Credentials and task-specific controls are
never persisted here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any


_SCHEMA = "tp-voyager.config/v2"
_DEFAULT_ALLOWED_MODELS = (
    "qoder:lite",
    "qoder:qmodel_38max",
    "codebuddy:hy3",
    "codebuddy:deepseek-v4-flash",
)
_TASK_KINDS = frozenset(
    {
        "research",
        "repository_research",
        "code_review",
        "small_patch",
        "test_failure_triage",
        "verify_only",
    }
)
_ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_BACKEND_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


class VoyagerUserConfigError(ValueError):
    """Raised when user-owned configuration is malformed or unsafe."""


def canonical_voyager_home() -> Path:
    configured = str(os.environ.get("TP_VOYAGER_HOME") or "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".tp-voyager").resolve()
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise VoyagerUserConfigError(f"config contains duplicate key: {key}")
        output[key] = value
    return output


def _require_object(value: object, field: str, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise VoyagerUserConfigError(f"{field} must be an object")
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        detail: list[str] = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("unsupported=" + ",".join(extra))
        raise VoyagerUserConfigError(f"{field} schema is invalid ({'; '.join(detail)})")
    return value


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise VoyagerUserConfigError(f"{field} must be boolean")
    return bool(value)


def _concurrency_limit(value: object, field: str) -> int:
    if type(value) is not int or not 1 <= value <= 64:
        raise VoyagerUserConfigError(f"{field} must be an integer from 1 to 64")
    return int(value)


def _is_absolute_path(text: str) -> bool:
    expanded = str(Path(text).expanduser())
    return Path(expanded).is_absolute() or PureWindowsPath(expanded).is_absolute()


def _path(value: object, field: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise VoyagerUserConfigError(f"{field} must be a string")
    text = value.strip()
    if not text:
        if allow_empty:
            return ""
        raise VoyagerUserConfigError(f"{field} must not be empty")
    if "\x00" in text or len(text) > 4096:
        raise VoyagerUserConfigError(f"{field} contains an invalid path")
    if not _is_absolute_path(text):
        raise VoyagerUserConfigError(f"{field} must be an absolute path")
    return text


def _model_ids(value: object, field: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 256:
        raise VoyagerUserConfigError(f"{field} must be a bounded list")
    if not value and not allow_empty:
        raise VoyagerUserConfigError(f"{field} must contain backend-qualified model IDs")
    output: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise VoyagerUserConfigError(f"{field} must contain backend-qualified model IDs")
        text = item.strip()
        backend, separator, model_id = text.partition(":")
        if (
            not separator
            or not _BACKEND_RE.fullmatch(backend)
            or not model_id
            or len(model_id) > 128
            or any(ch.isspace() for ch in model_id)
            or text in output
        ):
            raise VoyagerUserConfigError(
                f"{field} must contain unique backend-qualified model IDs"
            )
        output.append(text)
    return tuple(output)


def _trusted_root_map(value: object, field: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or len(value) > 64:
        raise VoyagerUserConfigError(f"{field} must be a bounded object")
    output: list[tuple[str, str]] = []
    for alias, raw_path in value.items():
        if not isinstance(alias, str) or not _ALIAS_RE.fullmatch(alias):
            raise VoyagerUserConfigError(f"{field} contains an invalid alias")
        output.append((alias, _path(raw_path, f"{field}.{alias}", allow_empty=False)))
    return tuple(sorted(output))


def _first_executable(names: tuple[str, ...]) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return str(Path(found).expanduser().resolve())
    return ""


@dataclass(frozen=True)
class QoderCrewConfig:
    enabled: bool = True
    cli_path: str = ""
    max_concurrent_tasks: int = 2


@dataclass(frozen=True)
class CodeBuddyCrewConfig:
    enabled: bool = True
    cli_path: str = ""
    internet_environment: str = "internal"
    max_concurrent_tasks: int = 2


@dataclass(frozen=True)
class CrewConfig:
    qoder: QoderCrewConfig
    codebuddy: CodeBuddyCrewConfig


@dataclass(frozen=True)
class DispatchConfig:
    allowed_models: tuple[str, ...]
    preferred_models: tuple[str, ...]
    task_kind_allowed_models: tuple[tuple[str, tuple[str, ...]], ...]

    def task_kind_map(self) -> dict[str, tuple[str, ...]]:
        return dict(self.task_kind_allowed_models)


@dataclass(frozen=True)
class TrustedRootsConfig:
    model_evidence: tuple[tuple[str, str], ...]
    instructions: tuple[tuple[str, str], ...]

    def model_evidence_map(self) -> dict[str, str]:
        return dict(self.model_evidence)

    def instructions_map(self) -> dict[str, str]:
        return dict(self.instructions)


@dataclass(frozen=True)
class ResourcesConfig:
    worker_profiles_root: str = ""
    worker_skills_root: str = ""


@dataclass(frozen=True)
class VoyagerUserConfig:
    schema: str
    home: Path
    crew: CrewConfig
    dispatch: DispatchConfig
    trusted_roots: TrustedRootsConfig
    resources: ResourcesConfig

    @classmethod
    def defaults(cls, home: str | Path | None = None) -> "VoyagerUserConfig":
        resolved_home = (
            Path(home).expanduser().resolve() if home is not None else canonical_voyager_home()
        )
        return cls(
            schema=_SCHEMA,
            home=resolved_home,
            crew=CrewConfig(QoderCrewConfig(), CodeBuddyCrewConfig()),
            dispatch=DispatchConfig(_DEFAULT_ALLOWED_MODELS, (), ()),
            trusted_roots=TrustedRootsConfig((), ()),
            resources=ResourcesConfig(),
        )

    @property
    def path(self) -> Path:
        return self.home / "config.json"

    @classmethod
    def load(cls, home: str | Path | None = None) -> "VoyagerUserConfig":
        base = Path(home).expanduser().resolve() if home is not None else canonical_voyager_home()
        path = base / "config.json"
        if not path.is_file():
            return cls.defaults(base)
        try:
            data = path.read_bytes()
            raw = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
        except VoyagerUserConfigError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VoyagerUserConfigError("config.json is unreadable or invalid JSON") from exc
        return cls._from_raw(raw, base)

    @classmethod
    def _from_raw(cls, raw: object, home: Path) -> "VoyagerUserConfig":
        top = _require_object(
            raw,
            "config",
            {"schema", "crew", "dispatch", "trusted_roots", "resources"},
        )
        if top["schema"] != _SCHEMA:
            raise VoyagerUserConfigError("config.schema is unsupported")

        crew_raw = _require_object(top["crew"], "crew", {"qoder", "codebuddy"})
        qoder_raw = _require_object(
            crew_raw["qoder"],
            "crew.qoder",
            {"enabled", "cli_path", "max_concurrent_tasks"},
        )
        codebuddy_raw = _require_object(
            crew_raw["codebuddy"],
            "crew.codebuddy",
            {"enabled", "cli_path", "internet_environment", "max_concurrent_tasks"},
        )
        environment = codebuddy_raw["internet_environment"]
        if not isinstance(environment, str) or environment.strip().lower() not in {
            "internal",
            "ioa",
            "public",
        }:
            raise VoyagerUserConfigError(
                "crew.codebuddy.internet_environment must be internal, ioa, or public"
            )

        dispatch_raw = _require_object(
            top["dispatch"],
            "dispatch",
            {"allowed_models", "preferred_models", "task_kind_allowed_models"},
        )
        allowed = _model_ids(dispatch_raw["allowed_models"], "dispatch.allowed_models", allow_empty=False)
        preferred = _model_ids(dispatch_raw["preferred_models"], "dispatch.preferred_models", allow_empty=True)
        allowed_set = set(allowed)
        if any(item not in allowed_set for item in preferred):
            raise VoyagerUserConfigError("dispatch.preferred_models must be a subset of allowed_models")
        raw_task_kinds = dispatch_raw["task_kind_allowed_models"]
        if not isinstance(raw_task_kinds, dict) or len(raw_task_kinds) > len(_TASK_KINDS):
            raise VoyagerUserConfigError("dispatch.task_kind_allowed_models must be an object")
        task_kind_allowed: list[tuple[str, tuple[str, ...]]] = []
        for task_kind, models in raw_task_kinds.items():
            if not isinstance(task_kind, str) or task_kind not in _TASK_KINDS:
                raise VoyagerUserConfigError("dispatch.task_kind_allowed_models contains an invalid task kind")
            parsed = _model_ids(
                models,
                f"dispatch.task_kind_allowed_models.{task_kind}",
                allow_empty=False,
            )
            if any(item not in allowed_set for item in parsed):
                raise VoyagerUserConfigError(
                    f"dispatch.task_kind_allowed_models.{task_kind} must be a subset of allowed_models"
                )
            task_kind_allowed.append((task_kind, parsed))

        roots_raw = _require_object(
            top["trusted_roots"], "trusted_roots", {"model_evidence", "instructions"}
        )
        resources_raw = _require_object(
            top["resources"], "resources", {"worker_profiles_root", "worker_skills_root"}
        )

        return cls(
            schema=_SCHEMA,
            home=home,
            crew=CrewConfig(
                QoderCrewConfig(
                    enabled=_bool(qoder_raw["enabled"], "crew.qoder.enabled"),
                    cli_path=_path(qoder_raw["cli_path"], "crew.qoder.cli_path"),
                    max_concurrent_tasks=_concurrency_limit(
                        qoder_raw["max_concurrent_tasks"],
                        "crew.qoder.max_concurrent_tasks",
                    ),
                ),
                CodeBuddyCrewConfig(
                    enabled=_bool(codebuddy_raw["enabled"], "crew.codebuddy.enabled"),
                    cli_path=_path(codebuddy_raw["cli_path"], "crew.codebuddy.cli_path"),
                    internet_environment=environment.strip().lower(),
                    max_concurrent_tasks=_concurrency_limit(
                        codebuddy_raw["max_concurrent_tasks"],
                        "crew.codebuddy.max_concurrent_tasks",
                    ),
                ),
            ),
            dispatch=DispatchConfig(
                allowed_models=allowed,
                preferred_models=preferred,
                task_kind_allowed_models=tuple(sorted(task_kind_allowed)),
            ),
            trusted_roots=TrustedRootsConfig(
                model_evidence=_trusted_root_map(
                    roots_raw["model_evidence"], "trusted_roots.model_evidence"
                ),
                instructions=_trusted_root_map(
                    roots_raw["instructions"], "trusted_roots.instructions"
                ),
            ),
            resources=ResourcesConfig(
                worker_profiles_root=_path(
                    resources_raw["worker_profiles_root"], "resources.worker_profiles_root"
                ),
                worker_skills_root=_path(
                    resources_raw["worker_skills_root"], "resources.worker_skills_root"
                ),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "crew": {
                "qoder": {
                    "enabled": self.crew.qoder.enabled,
                    "cli_path": self.crew.qoder.cli_path,
                    "max_concurrent_tasks": self.crew.qoder.max_concurrent_tasks,
                },
                "codebuddy": {
                    "enabled": self.crew.codebuddy.enabled,
                    "cli_path": self.crew.codebuddy.cli_path,
                    "internet_environment": self.crew.codebuddy.internet_environment,
                    "max_concurrent_tasks": self.crew.codebuddy.max_concurrent_tasks,
                },
            },
            "dispatch": {
                "allowed_models": list(self.dispatch.allowed_models),
                "preferred_models": list(self.dispatch.preferred_models),
                "task_kind_allowed_models": {
                    kind: list(models) for kind, models in self.dispatch.task_kind_allowed_models
                },
            },
            "trusted_roots": {
                "model_evidence": dict(self.trusted_roots.model_evidence),
                "instructions": dict(self.trusted_roots.instructions),
            },
            "resources": {
                "worker_profiles_root": self.resources.worker_profiles_root,
                "worker_skills_root": self.resources.worker_skills_root,
            },
        }

    @classmethod
    def initialize(cls, home: str | Path | None = None) -> dict[str, Any]:
        base = Path(home).expanduser().resolve() if home is not None else canonical_voyager_home()
        path = base / "config.json"
        runtime_dir = base / "runtime"
        for directory in (
            base,
            runtime_dir,
            runtime_dir / "artifacts",
            runtime_dir / "workspaces",
            runtime_dir / "logs",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if path.exists():
            cls.load(base)  # Validate existing config without overwriting it.
            return {"status": "already_exists", "path": str(path)}

        defaults = cls.defaults(base)
        discovered = cls(
            schema=defaults.schema,
            home=base,
            crew=CrewConfig(
                QoderCrewConfig(
                    enabled=True,
                    cli_path=_first_executable(("qodercli", "qodercli.cmd", "qodercli.exe")),
                    max_concurrent_tasks=defaults.crew.qoder.max_concurrent_tasks,
                ),
                CodeBuddyCrewConfig(
                    enabled=True,
                    cli_path=_first_executable(
                        ("codebuddy", "codebuddy.cmd", "codebuddy.exe", "cbc", "cbc.cmd", "cbc.exe")
                    ),
                    internet_environment="internal",
                    max_concurrent_tasks=defaults.crew.codebuddy.max_concurrent_tasks,
                ),
            ),
            dispatch=defaults.dispatch,
            trusted_roots=defaults.trusted_roots,
            resources=defaults.resources,
        )
        encoded = (json.dumps(discovered.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=str(base))
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "status": "installed",
            "path": str(path),
            "qoder_cli_discovered": bool(discovered.crew.qoder.cli_path),
            "codebuddy_cli_discovered": bool(discovered.crew.codebuddy.cli_path),
        }
