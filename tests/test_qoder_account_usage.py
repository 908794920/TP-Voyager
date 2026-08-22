from __future__ import annotations

import json
import unittest


class QoderAccountUsageTests(unittest.TestCase):
    def test_parses_account_quota_without_exposing_account_identity(self) -> None:
        from agent_runtime.backends.qoder.account_usage import (
            QODER_ACCOUNT_USAGE_MARKER,
            parse_qoder_account_usage_output,
        )

        raw = {
            "userId": "user-private-id",
            "userType": "personal_professional_trial",
            "isQuotaExceeded": False,
            "userQuota": {
                "total": 300,
                "used": 272,
                "remaining": 28,
                "percentage": 91,
                "unit": "credits",
            },
            "session": {"total_credits": 999},
        }

        snapshot = parse_qoder_account_usage_output(
            f"noise\n{QODER_ACCOUNT_USAGE_MARKER}{json.dumps(raw)}\n"
        )

        self.assertEqual(snapshot["status"], "observed")
        self.assertEqual(snapshot["auth_status"], "verified")
        self.assertEqual(snapshot["user_type"], "personal_professional_trial")
        self.assertIs(snapshot["is_quota_exceeded"], False)
        self.assertEqual(
            snapshot["user_quota"],
            {
                "total": 300.0,
                "used": 272.0,
                "remaining": 28.0,
                "percentage": 91.0,
                "unit": "credits",
            },
        )
        self.assertNotIn("userId", snapshot)
        self.assertNotIn("session", snapshot)

    def test_missing_snapshot_stays_unknown_instead_of_claiming_expired_login(self) -> None:
        from agent_runtime.backends.qoder.account_usage import parse_qoder_account_usage_output

        snapshot = parse_qoder_account_usage_output("no provider marker")

        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["auth_status"], "unknown")
        self.assertEqual(snapshot["user_quota"], {})

