"""Read-only, fail-closed global dispatch-model policy.

The policy is operator-owned JSON. It only narrows a Captain's explicit
selection; it never picks, replaces, or falls back to a model.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class DispatchModelPolicyError(ValueError):
    pass


_POLICY_KEYS = frozenset({"require_explicit_model", "allowed_models", "task_kind_allowed_models", "task_preferences"})
_TASK_KINDS = frozenset({"research", "repository_research", "code_review", "small_patch", "test_failure_triage", "verify_only"})


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise DispatchModelPolicyError(f"dispatch model policy contains duplicate key: {key}")
        output[key] = value
    return output


def _models(value: object, name: str, *, allow_absent: bool = False) -> frozenset[str] | None:
    if value is None and allow_absent:
        return None
    if not isinstance(value, list) or not value:
        raise DispatchModelPolicyError(f"{name} must be a non-empty list")
    output = frozenset(str(item).strip() for item in value)
    invalid = False
    for item in output:
        backend, separator, model_id = item.partition(":")
        if not separator or not backend or not model_id or len(item) > 160 or backend != backend.lower():
            invalid = True
            break
    if len(output) != len(value) or invalid:
        raise DispatchModelPolicyError(f"{name} must contain unique backend-qualified model IDs")
    return output


@dataclass(frozen=True)
class GlobalDispatchModelPolicy:
    require_explicit_model: bool = True
    allowed_models: frozenset[str] | None = None
    preferred_models: tuple[str, ...] = ()
    sha256: str = "builtin-safe-baseline"
    task_kind_allowed_models: tuple[tuple[str, frozenset[str]], ...] = ()

    @classmethod
    def load(cls, runtime_home: str | Path) -> "GlobalDispatchModelPolicy":
        path = Path(runtime_home) / "dispatch_model_policy.json"
        if not path.exists():
            return cls()
        try:
            data = path.read_bytes()
            raw = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DispatchModelPolicyError("dispatch model policy is invalid") from exc
        if not isinstance(raw, dict) or raw.get("require_explicit_model") is not True:
            raise DispatchModelPolicyError("dispatch model policy schema is invalid")
        if set(raw) - _POLICY_KEYS:
            raise DispatchModelPolicyError("dispatch model policy contains unsupported keys")
        allowed = _models(raw.get("allowed_models"), "allowed_models", allow_absent=True)
        prefs = raw.get("task_preferences", {})
        if not isinstance(prefs, dict) or set(prefs) - {"preferred"}:
            raise DispatchModelPolicyError("task_preferences schema is invalid")
        preferred_raw = prefs.get("preferred", [])
        preferred = _models(preferred_raw, "task_preferences.preferred") if preferred_raw else frozenset()
        raw_task_kinds = raw.get("task_kind_allowed_models", {})
        if not isinstance(raw_task_kinds, dict):
            raise DispatchModelPolicyError("task_kind_allowed_models schema is invalid")
        task_kind_allowed: list[tuple[str, frozenset[str]]] = []
        for task_kind, values in raw_task_kinds.items():
            normalized_kind = str(task_kind or "").strip().lower()
            if normalized_kind not in _TASK_KINDS or normalized_kind != task_kind:
                raise DispatchModelPolicyError("task_kind_allowed_models contains an invalid task kind")
            parsed = _models(values, f"task_kind_allowed_models.{task_kind}")
            assert parsed is not None
            task_kind_allowed.append((normalized_kind, parsed))
        return cls(
            bool(raw["require_explicit_model"]), allowed, tuple(sorted(preferred)),
            hashlib.sha256(data).hexdigest(), tuple(sorted(task_kind_allowed)),
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
