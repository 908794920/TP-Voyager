from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime.backends.codebuddy.model_catalog import (
    CodeBuddyAcpCatalogProbe,
    CodeBuddyCatalogError,
    _is_expected_catalog_notification,
    list_codebuddy_models,
    parse_credits_multiplier,
)


class CodeBuddyAcpCatalogTests(unittest.TestCase):
    def test_catalog_only_flow_projects_reference_billing(self) -> None:
        calls: list[str] = []

        def exchange(method: str, params: dict):
            calls.append(method)
            if method == "initialize":
                return {"result": {"protocolVersion": 1}}
            if method == "session/new":
                return {"result": {"sessionId": "catalog-only", "models": {"currentModelId": "hy3", "availableModels": [{"modelId": "hy3", "name": "HY3", "description": "x1.62"}]}}, "_meta": {}}
            self.assertEqual(method, "close/terminate")
            return {"result": {}}

        with patch("sqlite3.connect") as durable_connect:
            result = CodeBuddyAcpCatalogProbe(exchange).probe()
        durable_connect.assert_not_called()
        self.assertEqual(calls, ["initialize", "session/new", "close/terminate"])
        self.assertEqual(result.state_trace, tuple(calls))
        self.assertEqual(result.models[0].metadata["billing"]["multiplier"], 1.62)
        self.assertTrue(result.models[0].metadata["current"])
        self.assertFalse(result.models[0].metadata["billing"]["calculation_allowed"])

    def test_forbidden_callback_fails_closed_and_closes(self) -> None:
        calls: list[str] = []

        def exchange(method: str, params: dict):
            calls.append(method)
            return {"method": "session/prompt"} if method == "initialize" else {"result": {}}

        with self.assertRaises(CodeBuddyCatalogError):
            CodeBuddyAcpCatalogProbe(exchange).probe()
        self.assertEqual(calls, ["initialize", "close/terminate"])

    def test_any_unexpected_callback_fails_closed_and_closes(self) -> None:
        calls: list[str] = []

        def exchange(method: str, params: dict):
            calls.append(method)
            return {"method": "filesystem/read"} if method == "initialize" else {"result": {}}

        with self.assertRaises(CodeBuddyCatalogError):
            CodeBuddyAcpCatalogProbe(exchange).probe()
        self.assertEqual(calls, ["initialize", "close/terminate"])

    def test_only_bounded_config_directory_notification_is_expected(self) -> None:
        expected = {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "catalog-only",
                "update": {"sessionUpdate": "config_option_update", "configOptions": [{"id": "model"}]},
                "_meta": {"timestamp": "redacted"},
            },
        }
        self.assertTrue(_is_expected_catalog_notification(expected))
        for invalid in (
            {**expected, "id": 7},
            {**expected, "method": "filesystem/read"},
            {**expected, "params": {**expected["params"], "update": {"sessionUpdate": "tool_call", "configOptions": []}}},
            {**expected, "params": {**expected["params"], "update": {"sessionUpdate": "config_option_update", "configOptions": [{}] * 33}}},
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(_is_expected_catalog_notification(invalid))

    def test_multiplier_parser_keeps_invalid_raw_value(self) -> None:
        self.assertEqual(parse_credits_multiplier("x0.00"), ("x0.00", 0.0))
        self.assertEqual(parse_credits_multiplier("x0.79"), ("x0.79", 0.79))
        self.assertEqual(parse_credits_multiplier("x-1"), ("x-1", None))

    def test_auth_malformed_timeout_and_cancel_all_close_without_prompt(self) -> None:
        cases = {
            "auth": lambda method: {"error": {"code": 401}} if method == "initialize" else {"result": {}},
            "malformed": lambda method: [] if method == "initialize" else {"result": {}},
            "timeout": lambda method: (_ for _ in ()).throw(TimeoutError()) if method == "initialize" else {"result": {}},
            "cancel": lambda method: (_ for _ in ()).throw(KeyboardInterrupt()) if method == "initialize" else {"result": {}},
        }
        for name, behavior in cases.items():
            with self.subTest(name=name):
                calls=[]
                def exchange(method, params):
                    calls.append(method)
                    return behavior(method)
                with patch("sqlite3.connect") as durable_connect:
                    with self.assertRaises(CodeBuddyCatalogError):
                        CodeBuddyAcpCatalogProbe(exchange).probe()
                durable_connect.assert_not_called()
                self.assertEqual(calls[-1], "close/terminate")
                self.assertNotIn("session/prompt", calls)
                self.assertNotIn("tool", calls)
                self.assertNotIn("terminal", calls)
                self.assertNotIn("permission", calls)

    def test_tool_terminal_and_permission_callbacks_fail_closed(self) -> None:
        for callback in ("tool/call", "terminal/create", "permission/request"):
            with self.subTest(callback=callback):
                calls=[]
                def exchange(method, params):
                    calls.append(method)
                    return {"method": callback} if method == "initialize" else {"result": {}}
                with self.assertRaises(CodeBuddyCatalogError): CodeBuddyAcpCatalogProbe(exchange).probe()
                self.assertEqual(calls[-1], "close/terminate")

    def test_acp_timeout_falls_back_to_cli_without_live_multiplier(self) -> None:
        completed=SimpleNamespace(returncode=0, stdout="Currently supported: (hy3)", stderr="")
        with patch("agent_runtime.backends.codebuddy.model_catalog._list_codebuddy_models_via_acp", side_effect=TimeoutError()), patch(
            "agent_runtime.backends.codebuddy.model_catalog.resolve_codebuddy_cli", return_value="codebuddy"
        ), patch("agent_runtime.backends.codebuddy.model_catalog.subprocess.run", return_value=completed):
            models=list_codebuddy_models()
        self.assertEqual(models[0].source, "cli_declared")
        self.assertEqual(models[0].metadata["billing"], {"status":"unknown"})

    def test_production_catalog_cancellation_is_controlled(self) -> None:
        with patch(
            "agent_runtime.backends.codebuddy.model_catalog._list_codebuddy_models_via_acp",
            side_effect=CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_UNAVAILABLE"),
        ), patch(
            "agent_runtime.backends.codebuddy.model_catalog.resolve_codebuddy_cli",
            return_value="codebuddy",
        ), patch(
            "agent_runtime.backends.codebuddy.model_catalog.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="Currently supported: (hy3)", stderr=""),
        ):
            models = list_codebuddy_models()
        self.assertEqual(models[0].source, "cli_declared")


if __name__ == "__main__":
    unittest.main()
