"""Read-only execution/work-product assessment for terminal tasks.

V1.3 deliberately separates two facts that used to be conflated:

* ``execution`` — whether the backend produced a terminal response; and
* ``work_product`` — whether deterministic Runtime verification accepted the
  files/tests already present in the workspace.

The assessment is advisory and content-free.  It never mutates Task status,
never turns a timeout into success, and never returns answer text, file paths,
commands, Backend session identifiers, or raw error messages.
"""

from __future__ import annotations

import re
from typing import Any

from agent_runtime.domain.structured_result import (
    StructuredResultParseError,
    parse_structured_result,
)


ASSESSMENT_SCHEMA = "workbuddy.assessment/v1"
_SAFE_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


def _safe_code(value: str | None) -> str | None:
    if value is None:
        return None
    canonical = str(value).strip()
    if not canonical:
        return None
    return canonical if _SAFE_CODE_RE.fullmatch(canonical) else "other"


def _safe_count(value: object) -> int:
    """Accept only an explicit, bounded non-negative integer count."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if 0 <= value <= 1_000_000 else 0


def assess_task_result(
    *,
    task_id: str,
    execution_status: str,
    terminal_reason: str | None,
    timeout_reason: str | None,
    result_available: bool,
    result_json: str | None,
) -> dict[str, Any]:
    """Return a safe advisory assessment for one durable Task.

    ``recommended_action`` is intentionally conservative:

    * ``accept`` is possible only for a completed task with PASSED verification;
    * a terminal-loss partial Result with PASSED verification becomes
      ``review_for_acceptance`` — never automatic success;
    * FAILED verification recommends rejection;
    * every other case remains an explicit inspection/retry decision.
    """

    safe_execution_status = _safe_code(execution_status) or "unknown"
    safe_terminal_reason = _safe_code(terminal_reason)
    safe_timeout_reason = _safe_code(timeout_reason)
    base: dict[str, Any] = {
        "schema": ASSESSMENT_SCHEMA,
        "task_id": task_id,
        "advisory": True,
        "mutates_task": False,
        "execution": {
            "status": safe_execution_status,
            "terminal_reason": safe_terminal_reason,
            "timeout_reason": safe_timeout_reason,
        },
        "work_product": {
            "available": False,
            "status": "unavailable",
            "partial": False,
            "backend_terminal_observed": safe_execution_status == "completed",
            "verification_status": "UNAVAILABLE",
            "changed_file_count": 0,
            "artifact_count": 0,
            "test_count": 0,
            "evidence_basis": "none",
            "substantive_evidence": False,
        },
        "recommended_action": "inspect_or_retry",
        "operator_decision_required": True,
        "reason_codes": [],
    }
    if not result_available or not result_json:
        base["reason_codes"] = ["result_unavailable"]
        return base

    try:
        parsed = parse_structured_result(result_json)
    except StructuredResultParseError:
        base["work_product"].update(
            {"available": True, "status": "unreadable"}
        )
        base["reason_codes"] = ["result_unreadable"]
        return base

    output = parsed.output if isinstance(parsed.output, dict) else {}
    verification = (
        parsed.verification if isinstance(parsed.verification, dict) else {}
    )
    verification_status = str(
        verification.get("status") or "UNAVAILABLE"
    ).upper()
    partial = bool(output.get("partial"))
    terminal_observed_value = output.get("backend_terminal_observed")
    backend_terminal_observed = (
        bool(terminal_observed_value)
        if isinstance(terminal_observed_value, bool)
        else safe_execution_status == "completed" and not partial
    )
    reported_changed_file_count = len(parsed.changed_files)
    reported_artifact_count = len(parsed.artifacts)
    reported_test_count = len(parsed.tests)

    # A partial Result without a Backend terminal response may contain
    # normalized Backend claims.  Such claims remain useful for inspection,
    # but cannot justify an acceptance recommendation.  For terminal-loss
    # assessment, count only facts observed by Runtime capture/verification.
    trusted_evidence_required = partial or not backend_terminal_observed
    if trusted_evidence_required:
        changed_file_count = _safe_count(
            output.get("runtime_observed_changed_file_count")
        )
        artifact_count = _safe_count(
            output.get("runtime_captured_artifact_count")
        )
        test_count = _safe_count(output.get("runtime_verified_test_count"))
        evidence_basis = "runtime_observed"
    else:
        changed_file_count = reported_changed_file_count
        artifact_count = reported_artifact_count
        test_count = reported_test_count
        evidence_basis = "structured_result"
    substantive_evidence = any((changed_file_count, artifact_count, test_count))
    work_product = base["work_product"]
    work_product.update(
        {
            "available": True,
            "partial": partial,
            "backend_terminal_observed": backend_terminal_observed,
            "verification_status": verification_status,
            "changed_file_count": changed_file_count,
            "artifact_count": artifact_count,
            "test_count": test_count,
            "evidence_basis": evidence_basis,
            "substantive_evidence": substantive_evidence,
        }
    )

    reason_codes: list[str] = []
    if partial:
        reason_codes.append("partial_result")
    if safe_timeout_reason:
        reason_codes.append(f"timeout:{safe_timeout_reason}")
    if not backend_terminal_observed:
        reason_codes.append("backend_terminal_not_observed")

    if verification_status == "PASSED":
        work_product["status"] = "verified"
        reason_codes.append("verification_passed")
        if safe_execution_status == "completed" and not partial:
            base["recommended_action"] = "accept"
            base["operator_decision_required"] = False
        elif (
            safe_timeout_reason
            in {"idle_timeout", "stream_closed_without_terminal"}
            and partial
            and substantive_evidence
        ):
            # The Runtime verified the workspace, but the Backend did not
            # produce a terminal response.  Human/Master-Agent review remains
            # mandatory; this endpoint cannot mutate Task truth.
            base["recommended_action"] = "review_for_acceptance"
        else:
            base["recommended_action"] = "inspect"
            if not substantive_evidence:
                reason_codes.append("verified_without_work_product_evidence")
    elif verification_status == "FAILED":
        work_product["status"] = "rejected"
        base["recommended_action"] = "reject"
        base["operator_decision_required"] = False
        reason_codes.append("verification_failed")
    elif verification_status == "NEEDS_REVIEW":
        work_product["status"] = "needs_review"
        base["recommended_action"] = "review"
        reason_codes.append("verification_needs_review")
    elif verification_status == "NOT_REQUESTED":
        work_product["status"] = "unverified"
        base["recommended_action"] = "inspect_or_retry"
        reason_codes.append("verification_not_requested")
    elif verification_status == "NOT_RUN_EXECUTION_FAILED":
        work_product["status"] = "unverified"
        base["recommended_action"] = "inspect_or_retry"
        reason_codes.append("verification_not_run")
    else:
        work_product["status"] = "unverified"
        base["recommended_action"] = "inspect_or_retry"
        reason_codes.append("verification_unavailable")

    # Preserve order while removing duplicates.
    base["reason_codes"] = list(dict.fromkeys(reason_codes))
    return base
