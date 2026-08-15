"""Read-only, fail-closed global dispatch-model policy.

The policy is loaded from ``~/.tp-voyager/config.json``. It only narrows a Captain's explicit
selection; it never picks, replaces, or falls back to a model.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.configuration import VoyagerUserConfig, VoyagerUserConfigError


class DispatchModelPolicyError(ValueError):
    pass



@dataclass(frozen=True)
class GlobalDispatchModelPolicy:
    require_explicit_model: bool = True
    allowed_models: frozenset[str] | None = None
    preferred_models: tuple[str, ...] = ()
    sha256: str = "builtin-safe-baseline"
    task_kind_allowed_models: tuple[tuple[str, frozenset[str]], ...] = ()

    @classmethod
    def load(cls, runtime_home: str | Path) -> "GlobalDispatchModelPolicy":
        try:
            config = VoyagerUserConfig.load(runtime_home)
        except VoyagerUserConfigError as exc:
            raise DispatchModelPolicyError("TP-Voyager user config is invalid") from exc
        dispatch = config.dispatch
        canonical = {
            "allowed_models": list(dispatch.allowed_models),
            "preferred_models": list(dispatch.preferred_models),
            "task_kind_allowed_models": {
                kind: list(models) for kind, models in dispatch.task_kind_allowed_models
            },
        }
        data = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return cls(
            True,
            frozenset(dispatch.allowed_models),
            tuple(dispatch.preferred_models),
            hashlib.sha256(data).hexdigest(),
            tuple(
                (kind, frozenset(models))
                for kind, models in dispatch.task_kind_allowed_models
            ),
        )

    def effective_models(self, *constraints: object, backend: str = "", task_kind: str = "") -> frozenset[str] | None:
        sets = [set(self.allowed_models)] if self.allowed_models is not None else []
        task_constraints = dict(self.task_kind_allowed_models)
        if task_kind in task_constraints:
            sets.append(set(task_constraints[task_kind]))
        for constraint in constraints:
            values = getattr(constraint, "allowed_models", constraint)
            if values:
                normalized = {
                    str(item) if ":" in str(item) else f"{backend}:{item}"
                    for item in values
                }
                if any(not item.startswith(f"{backend}:") for item in normalized):
                    raise DispatchModelPolicyError("model constraint references another backend")
                sets.append(normalized)
        if not sets:
            return None
        result = frozenset.intersection(*(frozenset(item) for item in sets))
        if not result:
            raise DispatchModelPolicyError("effective model allowlist is empty")
        return result

    def validate(self, backend: str, model: str, *constraints: object, task_kind: str = "") -> tuple[str, ...]:
        model = str(model or "").strip()
        # v1.0.4 has no policy mode that may infer a model. Keep this guard
        # unconditional even for directly constructed test/operator objects.
        if not model:
            raise DispatchModelPolicyError("explicit model is required")
        effective = self.effective_models(*constraints, backend=backend, task_kind=task_kind)
        if effective is not None and f"{backend}:{model}" not in effective:
            raise DispatchModelPolicyError("selected model is outside the effective allowlist")
        prefix = f"{backend}:"
        return tuple(
            item for item in self.preferred_models
            if item.startswith(prefix) and (effective is None or item in effective)
        )
