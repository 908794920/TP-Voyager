"""Persistence for the v1.0.5 durable RunControl resource ledger."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from agent_runtime.domain.run_control import RunControlSpec
from agent_runtime.domain.enums import TERMINAL_STATUS_VALUES


class RunControlError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RunControlSnapshot:
    run_id: str
    max_dispatches: int
    max_runtime_seconds: float
    max_input_tokens: int | None
    max_output_tokens: int | None
    max_credits: float | None
    require_strict_usage_budget: bool
    dispatches_reserved: int
    dispatches_consumed: int
    runtime_reserved_seconds: float
    runtime_consumed_seconds: float
    input_tokens_consumed: int | None
    output_tokens_consumed: int | None
    credits_consumed: float | None
    usage_complete: bool
    status: str
    revision: int

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class RunControlRepository:
    """Budget baseline + atomically checked projections over durable Task truth.

    Task rows themselves are the durable reservation records.  Counters in
    ``run_controls`` are refreshed from those rows inside the same write
    transaction used to admit a new task, eliminating counter drift after
    crashes while still providing a compact ledger projection.
    """

    @staticmethod
    def _optional_narrower(new: int | float | None, old: int | float | None) -> int | float | None:
        if old is None:
            return new
        if new is None:
            return old
        if new > old:
            raise RunControlError("RUN_BUDGET_RELAXATION_REJECTED", "run budget ceilings may only stay equal or become narrower")
        return new

    def ensure_baseline(self, connection: sqlite3.Connection, spec: RunControlSpec, *, now: float) -> None:
        row = connection.execute("SELECT * FROM run_controls WHERE run_id = ?", (spec.run_id,)).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO run_controls (
                    run_id, max_dispatches, max_runtime_seconds, max_input_tokens,
                    max_output_tokens, max_credits, require_strict_usage_budget,
                    dispatches_reserved, dispatches_consumed,
                    runtime_reserved_seconds, runtime_consumed_seconds,
                    input_tokens_consumed, output_tokens_consumed, credits_consumed,
                    usage_complete, status, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, NULL, NULL, NULL, 1, 'open', 1, ?, ?)
                """,
                (
                    spec.run_id, spec.max_dispatches, spec.max_runtime_seconds,
                    spec.max_input_tokens, spec.max_output_tokens, spec.max_credits,
                    int(spec.require_strict_usage_budget), now, now,
                ),
            )
            return
        if str(row["status"]) == "closed":
            raise RunControlError("RUN_CLOSED", "run_control is closed")
        ledger_now = max(float(now), float(row["created_at"]))
        max_dispatches = int(self._optional_narrower(spec.max_dispatches, int(row["max_dispatches"])))
        max_runtime = float(self._optional_narrower(spec.max_runtime_seconds, float(row["max_runtime_seconds"])))
        max_input = self._optional_narrower(spec.max_input_tokens, row["max_input_tokens"])
        max_output = self._optional_narrower(spec.max_output_tokens, row["max_output_tokens"])
        max_credits = self._optional_narrower(spec.max_credits, row["max_credits"])
        strict = bool(row["require_strict_usage_budget"]) or spec.require_strict_usage_budget
        connection.execute(
            """
            UPDATE run_controls SET max_dispatches=?, max_runtime_seconds=?,
                max_input_tokens=?, max_output_tokens=?, max_credits=?,
                require_strict_usage_budget=?, revision=revision+1, updated_at=?
            WHERE run_id=?
            """,
            (max_dispatches, max_runtime, max_input, max_output, max_credits, int(strict), ledger_now, spec.run_id),
        )

    @staticmethod
    def _usage_from_result_json(raw: object) -> tuple[int | None, int | None, float | None, bool]:
        if not isinstance(raw, str) or not raw:
            return None, None, None, False
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None, None, None, False
        if not isinstance(payload, dict):
            return None, None, None, False
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None, None, None, False
        normalized = usage.get("usage") if isinstance(usage.get("usage"), dict) else usage
        def numeric(name: str):
            val = normalized.get(name) if isinstance(normalized, dict) else None
            return val if isinstance(val, (int, float)) and not isinstance(val, bool) and val >= 0 else None
        inp = numeric("input_tokens")
        out = numeric("output_tokens")
        credits = numeric("credits_used")
        return (
            int(inp) if inp is not None else None,
            int(out) if out is not None else None,
            float(credits) if credits is not None else None,
            bool(inp is not None or out is not None or credits is not None),
        )

    def refresh(self, connection: sqlite3.Connection, run_id: str, *, now: float) -> RunControlSnapshot:
        control = connection.execute("SELECT * FROM run_controls WHERE run_id=?", (run_id,)).fetchone()
        if control is None:
            raise RunControlError("RUN_NOT_FOUND", "run_control was not found")
        ledger_now = max(float(now), float(control["created_at"]))
        rows = connection.execute(
            """
            SELECT t.task_id, t.status, t.started_at, t.finished_at,
                   json_extract(s.metadata_json, '$.max_task_duration_seconds') AS max_duration
            FROM tasks t
            LEFT JOIN sessions s ON s.task_id=t.task_id
            WHERE t.run_id=?
            """,
            (run_id,),
        ).fetchall()
        dispatches_reserved = len(rows)
        dispatches_consumed = len(rows)
        runtime_reserved = 0.0
        runtime_consumed = 0.0
        input_total = 0
        output_total = 0
        credits_total = 0.0
        any_input = any_output = any_credits = False
        usage_complete = True
        for row in rows:
            maximum = row["max_duration"]
            started = row["started_at"]
            finished = row["finished_at"]
            status_value = str(row["status"] or "")
            elapsed = 0.0
            if isinstance(started, (int, float)):
                end = float(finished) if isinstance(finished, (int, float)) else ledger_now
                elapsed = max(0.0, end - float(started))
                runtime_consumed += elapsed
            # A Task row is the durable reservation fact.  Terminal tasks release
            # unused runtime budget; active tasks reserve only their remaining
            # maximum duration so consumed + reserved never double counts time.
            if status_value not in TERMINAL_STATUS_VALUES and isinstance(maximum, (int, float)) and maximum > 0:
                runtime_reserved += max(0.0, float(maximum) - elapsed)
        # Usage Evidence, not Result projection, is the durable resource truth.
        # This preserves usage observed before timeout/cancel/failure as well as
        # successful terminal usage.
        usage_rows = connection.execute(
            """
            SELECT e.task_id, e.detail_json
            FROM evidences e
            JOIN tasks t ON t.task_id=e.task_id
            WHERE t.run_id=? AND e.evidence_type='usage'
            ORDER BY e.created_at, e.evidence_id
            """,
            (run_id,),
        ).fetchall()
        tasks_with_usage: set[str] = set()
        for usage_row in usage_rows:
            inp, out, credits, has_usage = self._usage_from_result_json(usage_row["detail_json"])
            if has_usage:
                tasks_with_usage.add(str(usage_row["task_id"]))
            if inp is not None:
                input_total += inp; any_input = True
            if out is not None:
                output_total += out; any_output = True
            if credits is not None:
                credits_total += credits; any_credits = True
        usage_complete = bool(rows) and len(tasks_with_usage) == len(rows)
        status = "open"
        if (
            dispatches_reserved >= int(control["max_dispatches"])
            or runtime_consumed + runtime_reserved >= float(control["max_runtime_seconds"])
        ):
            status = "exhausted"
        connection.execute(
            """
            UPDATE run_controls SET dispatches_reserved=?, dispatches_consumed=?,
                runtime_reserved_seconds=?, runtime_consumed_seconds=?,
                input_tokens_consumed=?, output_tokens_consumed=?, credits_consumed=?,
                usage_complete=?, status=CASE WHEN status='closed' THEN 'closed' ELSE ? END,
                updated_at=? WHERE run_id=?
            """,
            (
                dispatches_reserved, dispatches_consumed, runtime_reserved, runtime_consumed,
                input_total if any_input else None, output_total if any_output else None,
                credits_total if any_credits else None, int(usage_complete), status, ledger_now, run_id,
            ),
        )
        control = connection.execute("SELECT * FROM run_controls WHERE run_id=?", (run_id,)).fetchone()
        return RunControlSnapshot(
            run_id=run_id,
            max_dispatches=int(control["max_dispatches"]),
            max_runtime_seconds=float(control["max_runtime_seconds"]),
            max_input_tokens=control["max_input_tokens"], max_output_tokens=control["max_output_tokens"],
            max_credits=control["max_credits"],
            require_strict_usage_budget=bool(control["require_strict_usage_budget"]),
            dispatches_reserved=int(control["dispatches_reserved"]),
            dispatches_consumed=int(control["dispatches_consumed"]),
            runtime_reserved_seconds=float(control["runtime_reserved_seconds"]),
            runtime_consumed_seconds=float(control["runtime_consumed_seconds"]),
            input_tokens_consumed=control["input_tokens_consumed"],
            output_tokens_consumed=control["output_tokens_consumed"],
            credits_consumed=control["credits_consumed"], usage_complete=bool(control["usage_complete"]),
            status=str(control["status"]), revision=int(control["revision"]),
        )

    def admit_current_task(self, connection: sqlite3.Connection, spec: RunControlSpec, *, requested_runtime_seconds: float, now: float) -> RunControlSnapshot:
        self.ensure_baseline(connection, spec, now=now)
        snapshot = self.refresh(connection, spec.run_id, now=now)
        # The current Task row already exists in this transaction, while its
        # Session (which carries max_task_duration_seconds) is inserted after
        # admission.  Account for that one pending reservation explicitly.
        projected_runtime_commitment = (
            snapshot.runtime_consumed_seconds
            + snapshot.runtime_reserved_seconds
            + max(0.0, float(requested_runtime_seconds))
        )
        if snapshot.dispatches_reserved > snapshot.max_dispatches:
            raise RunControlError("RUN_DISPATCH_BUDGET_EXCEEDED", "run dispatch budget is exhausted")
        if projected_runtime_commitment > snapshot.max_runtime_seconds:
            raise RunControlError("RUN_TIME_BUDGET_EXCEEDED", "run runtime budget is exhausted")
        # The Session carrying max_task_duration_seconds is created immediately
        # after admission in the same transaction.  Persist the pending active
        # reservation projection now; a later refresh derives it from Tasks.
        pending_reserved = snapshot.runtime_reserved_seconds + max(0.0, float(requested_runtime_seconds))
        created_row = connection.execute("SELECT created_at FROM run_controls WHERE run_id=?", (spec.run_id,)).fetchone()
        ledger_now = max(float(now), float(created_row["created_at"])) if created_row is not None else float(now)
        connection.execute(
            "UPDATE run_controls SET runtime_reserved_seconds=?, updated_at=? WHERE run_id=?",
            (pending_reserved, ledger_now, spec.run_id),
        )
        if snapshot.max_input_tokens is not None and snapshot.input_tokens_consumed is not None and snapshot.input_tokens_consumed >= snapshot.max_input_tokens:
            raise RunControlError("RUN_TOKEN_BUDGET_EXCEEDED", "run input-token budget is exhausted")
        if snapshot.max_output_tokens is not None and snapshot.output_tokens_consumed is not None and snapshot.output_tokens_consumed >= snapshot.max_output_tokens:
            raise RunControlError("RUN_TOKEN_BUDGET_EXCEEDED", "run output-token budget is exhausted")
        if snapshot.max_credits is not None and snapshot.credits_consumed is not None and snapshot.credits_consumed >= snapshot.max_credits:
            raise RunControlError("RUN_CREDIT_BUDGET_EXCEEDED", "run credit budget is exhausted")
        if snapshot.require_strict_usage_budget and (snapshot.max_input_tokens is not None or snapshot.max_output_tokens is not None or snapshot.max_credits is not None):
            # TP-Voyager has no provider-side per-task hard token/credit cap in
            # the accepted CodeBuddy/Qoder contracts.  Post-task Usage can stop
            # future dispatches but cannot guarantee a task will not overshoot.
            raise RunControlError("BUDGET_NOT_ENFORCEABLE", "strict token/credit ceilings are not enforceable by the current Crew contract")
        return snapshot

    def get(self, connection: sqlite3.Connection, run_id: str, *, now: float) -> RunControlSnapshot | None:
        row = connection.execute("SELECT 1 FROM run_controls WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return self.refresh(connection, run_id, now=now)
