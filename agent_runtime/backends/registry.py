"""Thread-safe registry for runtime backend implementations."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from agent_runtime.backends.base import SubAgentBackend
from agent_runtime.backends.errors import BackendError


BackendFactory = Callable[[], SubAgentBackend]


@dataclass(frozen=True)
class RegisteredBackend:
    name: str
    factory: BackendFactory


class BackendRegistry:
    """Resolve backends by durable runtime name without owning task state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._factories: dict[str, BackendFactory] = {}

    def register(self, name: str, factory: BackendFactory, *, replace: bool = False) -> None:
        key = name.strip().lower()
        if not key:
            raise ValueError("backend name is required")
        with self._lock:
            if key in self._factories and not replace:
                raise ValueError(f"backend already registered: {key}")
            self._factories[key] = factory

    def resolve(self, name: str) -> SubAgentBackend:
        key = name.strip().lower()
        with self._lock:
            factory = self._factories.get(key)
        if factory is None:
            raise BackendError(f"Unsupported sub-agent runtime: {name}")
        return factory()

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories)
