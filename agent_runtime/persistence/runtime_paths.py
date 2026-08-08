"""Canonical Agent Runtime paths with explicit V1 compatibility resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePathResolution:
    database: Path
    source: str
    canonical_database: Path
    legacy_database: Path
    legacy_compat_active: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "database": str(self.database),
            "path_source": self.source,
            "canonical_database": str(self.canonical_database),
            "legacy_database": str(self.legacy_database),
            "legacy_compat_active": self.legacy_compat_active,
        }


def canonical_runtime_home() -> Path:
    configured = os.environ.get("AGENT_RUNTIME_HOME")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".agent-runtime").resolve()
    )


def canonical_runtime_database_path() -> Path:
    return canonical_runtime_home() / "runtime" / "agent_runtime.db"


def legacy_runtime_home() -> Path:
    configured = os.environ.get("WORKBUDDY_CONFIG_DIR") or os.environ.get(
        "CODEBUDDY_CONFIG_DIR"
    )
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".workbuddy").resolve()
    )


def legacy_runtime_database_path() -> Path:
    return legacy_runtime_home() / "runtime" / "workbuddy_runtime.db"


def resolve_runtime_database() -> RuntimePathResolution:
    canonical = canonical_runtime_database_path().resolve()
    legacy = legacy_runtime_database_path().resolve()

    explicit_new = os.environ.get("AGENT_RUNTIME_DB")
    if explicit_new:
        selected = Path(explicit_new).expanduser().resolve()
        return RuntimePathResolution(
            selected, "AGENT_RUNTIME_DB", canonical, legacy, False
        )

    explicit_legacy = os.environ.get("WORKBUDDY_RUNTIME_DB")
    if explicit_legacy:
        selected = Path(explicit_legacy).expanduser().resolve()
        return RuntimePathResolution(
            selected, "WORKBUDDY_RUNTIME_DB", canonical, legacy, True
        )

    # An explicitly configured canonical home is itself an operator choice;
    # do not override it with a historical WorkBuddy location.
    if os.environ.get("AGENT_RUNTIME_HOME"):
        return RuntimePathResolution(
            canonical, "AGENT_RUNTIME_HOME", canonical, legacy, False
        )

    # Once a successful V2 migration created the canonical database, it wins
    # even while the old rollback copy remains on disk.
    if canonical.exists():
        return RuntimePathResolution(
            canonical, "canonical_existing", canonical, legacy, False
        )
    if legacy.exists():
        return RuntimePathResolution(
            legacy, "legacy_existing", canonical, legacy, True
        )
    return RuntimePathResolution(
        canonical, "canonical_default", canonical, legacy, False
    )


def runtime_database_path() -> Path:
    return resolve_runtime_database().database
