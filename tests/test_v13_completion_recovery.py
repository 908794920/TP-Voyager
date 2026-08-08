from __future__ import annotations

import json
import unittest

from agent_runtime.domain.structured_result import (
    RESULT_SCHEMA,
    StructuredResult,
)
from agent_runtime.application.outcome_service import (
    ASSESSMENT_SCHEMA,
    assess_task_result,
)


class CompletionRecoveryAssessmentTests(unittest.TestCase):
    def _result(
        self,
        *,
        verification_status: str,
        partial: bool = False,
        terminal_observed: bool | None = None,
    ) -> str:
        output: dict[str, object] = {
            "partial": partial,
            "private_workspace": "C:/private/project",
        }
        if partial:
            output.update(
                {
                    "runtime_observed_changed_file_count": 1,
                    "runtime_captured_artifact_count": 1,
                    "runtime_verified_test_count": 1,
                }
            )
        if terminal_observed is not None:
            output["backend_terminal_observed"] = terminal_observed
        return json.dumps(
            StructuredResult(
                schema=RESULT_SCHEMA,
                attempt_id="att-1",
                answer="private answer",
                backend="qoder",
                stop_reason="end_turn",
                output=output,
                observability={"private_session": "session-secret"},
                changed_files=["src/private.py"],
                tests=[{"command": "private test", "exit_code": 0}],
                artifacts=[{"name": "private.py"}],
                verification={"status": verification_status},
            ).to_dict(),
            ensure_ascii=False,
        )

    def _assess(
        self,
        *,
        execution_status: str = "failed",
        terminal_reason: str | None = "GatewayTimeoutError",
        timeout_reason: str | None = "idle_timeout",
        result_available: bool = True,
        result_json: str | None = None,
    ) -> dict[str, object]:
        return assess_task_result(
            task_id="wb-1",
            execution_status=execution_status,
            terminal_reason=terminal_reason,
            timeout_reason=timeout_reason,
            result_available=result_available,
            result_json=result_json,
        )

    def test_completed_verified_result_can_be_accepted(self) -> None:
        assessment = self._assess(
            execution_status="completed",
            terminal_reason="end_turn",
            timeout_reason=None,
            result_json=self._result(
                verification_status="PASSED",
                partial=False,
                terminal_observed=True,
            ),
        )
        self.assertEqual(assessment["schema"], ASSESSMENT_SCHEMA)
        self.assertEqual(assessment["recommended_action"], "accept")
        self.assertFalse(assessment["operator_decision_required"])
        self.assertEqual(assessment["work_product"]["status"], "verified")

    def test_idle_timeout_verified_partial_requires_explicit_review(self) -> None:
        assessment = self._assess(
            result_json=self._result(
                verification_status="PASSED",
                partial=True,
                terminal_observed=False,
            )
        )
        self.assertEqual(
            assessment["recommended_action"], "review_for_acceptance"
        )
        self.assertTrue(assessment["operator_decision_required"])
        self.assertEqual(assessment["execution"]["status"], "failed")
        self.assertEqual(assessment["work_product"]["status"], "verified")

    def test_stream_close_verified_partial_uses_same_conservative_boundary(self) -> None:
        assessment = self._assess(
            timeout_reason="stream_closed_without_terminal",
            result_json=self._result(
                verification_status="PASSED",
                partial=True,
                terminal_observed=False,
            ),
        )
        self.assertEqual(
            assessment["recommended_action"], "review_for_acceptance"
        )
        self.assertIn(
            "timeout:stream_closed_without_terminal",
            assessment["reason_codes"],
        )

    def test_passed_verification_without_material_does_not_suggest_acceptance(self) -> None:
        result = StructuredResult(
            schema=RESULT_SCHEMA,
            attempt_id="att-empty",
            answer="",
            backend="qoder",
            stop_reason="idle_timeout",
            output={"partial": True, "backend_terminal_observed": False},
            observability={},
            verification={"status": "PASSED"},
        )
        assessment = self._assess(
            result_json=json.dumps(result.to_dict(), ensure_ascii=False)
        )
        self.assertEqual(assessment["recommended_action"], "inspect")
        self.assertFalse(assessment["work_product"]["substantive_evidence"])
        self.assertIn(
            "verified_without_work_product_evidence",
            assessment["reason_codes"],
        )

    def test_backend_claims_do_not_count_as_terminal_loss_evidence(self) -> None:
        result = StructuredResult(
            schema=RESULT_SCHEMA,
            attempt_id="att-claims-only",
            answer="claimed completion",
            backend="qoder",
            stop_reason="idle_timeout",
            output={
                "partial": True,
                "backend_terminal_observed": False,
                "runtime_observed_changed_file_count": 0,
                "runtime_captured_artifact_count": 0,
                "runtime_verified_test_count": 0,
            },
            changed_files=["claimed.py"],
            tests=[{"command": "claimed test", "exit_code": 0}],
            artifacts=[{"name": "claimed.py"}],
            verification={"status": "PASSED"},
        )
        assessment = self._assess(
            result_json=json.dumps(result.to_dict(), ensure_ascii=False)
        )
        work_product = assessment["work_product"]
        self.assertEqual(assessment["recommended_action"], "inspect")
        self.assertEqual(work_product["evidence_basis"], "runtime_observed")
        self.assertEqual(work_product["changed_file_count"], 0)
        self.assertEqual(work_product["artifact_count"], 0)
        self.assertEqual(work_product["test_count"], 0)
        self.assertFalse(work_product["substantive_evidence"])

    def test_failed_verification_recommends_rejection(self) -> None:
        assessment = self._assess(
            result_json=self._result(
                verification_status="FAILED",
                partial=True,
                terminal_observed=False,
            )
        )
        self.assertEqual(assessment["recommended_action"], "reject")
        self.assertFalse(assessment["operator_decision_required"])
        self.assertEqual(assessment["work_product"]["status"], "rejected")

    def test_needs_review_remains_manual(self) -> None:
        assessment = self._assess(
            result_json=self._result(
                verification_status="NEEDS_REVIEW",
                partial=True,
                terminal_observed=False,
            )
        )
        self.assertEqual(assessment["recommended_action"], "review")
        self.assertTrue(assessment["operator_decision_required"])

    def test_unavailable_and_unreadable_results_fail_closed(self) -> None:
        unavailable = self._assess(result_available=False, result_json=None)
        self.assertEqual(unavailable["recommended_action"], "inspect_or_retry")
        self.assertEqual(unavailable["reason_codes"], ["result_unavailable"])

        unreadable = self._assess(result_json="not-json")
        self.assertEqual(unreadable["work_product"]["status"], "unreadable")
        self.assertEqual(unreadable["reason_codes"], ["result_unreadable"])

    def test_untrusted_terminal_codes_are_sanitized(self) -> None:
        assessment = self._assess(
            terminal_reason="private error at C:/secret/project",
            timeout_reason="private timeout C:/secret/project",
            result_available=False,
            result_json=None,
        )
        self.assertEqual(assessment["execution"]["terminal_reason"], "other")
        self.assertEqual(assessment["execution"]["timeout_reason"], "other")
        self.assertNotIn("C:/secret", json.dumps(assessment))

    def test_assessment_is_content_free(self) -> None:
        assessment = self._assess(
            result_json=self._result(
                verification_status="PASSED",
                partial=True,
                terminal_observed=False,
            )
        )
        encoded = json.dumps(assessment, ensure_ascii=False)
        self.assertNotIn("private answer", encoded)
        self.assertNotIn("C:/private", encoded)
        self.assertNotIn("src/private.py", encoded)
        self.assertNotIn("private test", encoded)
        self.assertNotIn("session-secret", encoded)
        self.assertEqual(
            assessment["work_product"]["evidence_basis"], "runtime_observed"
        )
        self.assertEqual(assessment["work_product"]["changed_file_count"], 1)
        self.assertEqual(assessment["work_product"]["artifact_count"], 1)
        self.assertEqual(assessment["work_product"]["test_count"], 1)


if __name__ == "__main__":
    unittest.main()
