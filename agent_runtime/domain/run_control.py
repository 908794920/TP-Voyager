"""Durable resource-ledger contract for one Captain-owned run.

RunControl is deliberately not a workflow model: it knows budgets only.  It
never stores stages, roles, ordering, dependencies, next actions or acceptance
decisions.  Durable Task rows remain the reservation/consumption truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunControlSpec:
    run_id: str
    max_dispatches: int
    max_runtime_seconds: float
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_credits: float | None = None
    require_strict_usage_budget: bool = False

    @classmethod
    def from_dict(cls, value: object) -> "RunControlSpec":
        if not isinstance(value, dict):
            raise ValueError("run_control must be an object")
        run_id = str(value.get("run_id") or "").strip()
        if (
            not run_id
            or len(run_id) > 160
            or "\x00" in run_id
            or any(ord(ch) < 32 for ch in run_id)
        ):
            raise ValueError("run_control.run_id must be printable and at most 160 characters")
        try:
            max_dispatches = int(value.get("max_dispatches"))
            max_runtime_seconds = float(value.get("max_runtime_seconds"))
        except (TypeError, ValueError) as exc:
            raise ValueError("run_control dispatch/runtime ceilings are required") from exc
        if max_dispatches <= 0 or max_dispatches > 10000:
            raise ValueError("run_control.max_dispatches must be between 1 and 10000")
        if max_runtime_seconds <= 0 or max_runtime_seconds > 31 * 24 * 3600:
            raise ValueError("run_control.max_runtime_seconds is outside the bounded limit")

        def optional_int(name: str) -> int | None:
            raw = value.get(name)
            if raw is None:
                return None
            try:
                parsed = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"run_control.{name} is invalid") from exc
            if parsed <= 0 or parsed > 10**15:
                raise ValueError(f"run_control.{name} is outside the bounded limit")
            return parsed

        def optional_float(name: str) -> float | None:
            raw = value.get(name)
            if raw is None:
                return None
            try:
                parsed = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"run_control.{name} is invalid") from exc
            if parsed <= 0 or parsed > 10**12:
                raise ValueError(f"run_control.{name} is outside the bounded limit")
            return parsed

        strict = value.get("require_strict_usage_budget", False)
        if not isinstance(strict, bool):
            raise ValueError("run_control.require_strict_usage_budget must be boolean")
        return cls(
            run_id=run_id,
            max_dispatches=max_dispatches,
            max_runtime_seconds=max_runtime_seconds,
            max_input_tokens=optional_int("max_input_tokens"),
            max_output_tokens=optional_int("max_output_tokens"),
            max_credits=optional_float("max_credits"),
            require_strict_usage_budget=strict,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "max_dispatches": self.max_dispatches,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_credits": self.max_credits,
            "require_strict_usage_budget": self.require_strict_usage_budget,
        }
