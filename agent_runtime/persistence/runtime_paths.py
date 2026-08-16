"""Canonical TP-Voyager runtime paths.

v1.0.7 is a clean break: TP-Voyager no longer auto-selects historical
``.agent-runtime`` or WorkBuddy locations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.configuration import canonical_voyager_home


@dataclass(frozen=True)
class RuntimePathResolution:
    database: Path
    source: str
    canonical_database: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "database": str(self.database),
            "path_source": self.source,
            "canonical_database": str(self.canonical_database),
        }


def canonical_runtime_home() -> Path:
    return canonical_voyager_home()


def canonical_runtime_database_path() -> Path:
    return canonical_runtime_home() / "runtime" / "tp_voyager.db"


def resolve_runtime_database() -> RuntimePathResolution:
    canonical = canonical_runtime_database_path().resolve()
    explicit = str(os.environ.get("TP_VOYAGER_DB") or "").strip()
    if explicit:
        selected = Path(explicit).expanduser().resolve()
        return RuntimePathResolution(selected, "TP_VOYAGER_DB", canonical)
    if canonical.exists():
        return RuntimePathResolution(canonical, "canonical_existing", canonical)
    if str(os.environ.get("TP_VOYAGER_HOME") or "").strip():
        return RuntimePathResolution(canonical, "TP_VOYAGER_HOME", canonical)
    return RuntimePathResolution(canonical, "canonical_default", canonical)


def runtime_database_path() -> Path:
    return resolve_runtime_database().database
