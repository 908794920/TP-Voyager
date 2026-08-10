from __future__ import annotations

import unittest

from agent_runtime.testing.profiles import CURRENT_TARGETS, REGRESSION_MODULES, SMOKE_TARGETS, profile_targets
from agent_runtime.testing.runner import build_parser


class TestProfileTests(unittest.TestCase):
    def test_only_maintained_profiles_exist(self) -> None:
        for name in ("smoke", "current", "regression", "stress", "release"):
            self.assertGreater(len(profile_targets(name)), 0)
        for retired in ("audit", "all"):
            with self.assertRaises(ValueError):
                profile_targets(retired)

    def test_smoke_and_current_are_bounded_tp_voyager_surfaces(self) -> None:
        self.assertLessEqual(len(SMOKE_TARGETS), 20)
        self.assertLessEqual(len(CURRENT_TARGETS), 11)
        smoke = {item.name for item in SMOKE_TARGETS}
        current = {item.name for item in CURRENT_TARGETS}
        self.assertTrue(any("test_patch_worker" in name for name in smoke))
        self.assertTrue(any("test_runtime_reconciliation" in name for name in smoke))
        for name in (
            "tests.test_codebuddy_backend", "tests.test_qoder_backend", "tests.test_crew_registry",
            "tests.test_captain_boundary", "tests.test_patch_worker",
        ):
            self.assertIn(name, current)

    def test_regression_contains_no_retired_workbuddy_transport_modules(self) -> None:
        retired = {
            "test_backend_integration", "test_config", "test_gateway_activity", "test_gateway_reader_stop",
            "test_history", "test_multiplexer", "test_runtime_restart", "test_runtime_server_integration",
        }
        self.assertTrue(retired.isdisjoint(REGRESSION_MODULES))
        self.assertIn("test_runtime_reconciliation", REGRESSION_MODULES)
        self.assertIn("test_v105_flow_control", REGRESSION_MODULES)
        self.assertIn("test_v105_server_contract", REGRESSION_MODULES)

    def test_release_is_regression_plus_stress(self) -> None:
        self.assertEqual(profile_targets("release"), profile_targets("regression") + profile_targets("stress"))

    def test_runner_does_not_offer_historical_audit(self) -> None:
        parser = build_parser()
        for value in ("smoke", "current", "regression", "stress", "release"):
            self.assertEqual(parser.parse_args([value, "--list"]).profile, value)
        with self.assertRaises(SystemExit):
            parser.parse_args(["audit"])


if __name__ == "__main__":
    unittest.main()
