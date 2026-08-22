"""v1.0.9: explicit ``workspace_strategy`` dispatch contract tests.

Covers the four-strategy enum, the default value, fail-closed validation at
the domain and MCP boundaries, the model_only/live_readonly preparation
semantics, and the full API -> CaptainDispatchRequest -> routing_metadata
chain including the idempotency contract.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_runtime.api import mcp_server as server
from agent_runtime.domain.dispatch import CaptainDispatchRequest
from agent_runtime.domain.enums import WorkspaceStrategy


class WorkspaceStrategyEnumTests(unittest.TestCase):
    def test_enum_members_and_values(self) -> None:
        values = [item.value for item in WorkspaceStrategy]
        self.assertEqual(
            values,
            ["model_only", "live_readonly", "frozen_context", "isolated_patch"],
        )
        # Members are str-backed so persisted string values stay interpretable.
        self.assertEqual(WorkspaceStrategy.MODEL_ONLY, "model_only")
        self.assertEqual(WorkspaceStrategy.LIVE_READONLY, "live_readonly")
        self.assertEqual(WorkspaceStrategy.FROZEN_CONTEXT, "frozen_context")
        self.assertEqual(WorkspaceStrategy.ISOLATED_PATCH, "isolated_patch")

    def test_enum_round_trips_as_string(self) -> None:
        for member in WorkspaceStrategy:
            self.assertEqual(WorkspaceStrategy(str(member.value)), member)


class CaptainDispatchRequestStrategyTests(unittest.TestCase):
    def test_default_strategy_is_isolated_patch(self) -> None:
        request = CaptainDispatchRequest(
            objective="smoke", crew="codebuddy", task_kind="research"
        )
        self.assertEqual(request.workspace_strategy, "isolated_patch")

    def test_explicit_strategy_is_accepted_and_normalized(self) -> None:
        request = CaptainDispatchRequest(
            objective="smoke", crew="codebuddy", task_kind="research",
            workspace_strategy=" MODEL_ONLY ",
        )
        self.assertEqual(request.workspace_strategy, "model_only")

    def test_invalid_strategy_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            CaptainDispatchRequest(
                objective="smoke", crew="codebuddy", task_kind="research",
                workspace_strategy="unknown_strategy",
            )
        self.assertIn("workspace_strategy", str(ctx.exception))

    def test_all_enum_values_accepted_by_domain(self) -> None:
        for member in WorkspaceStrategy:
            request = CaptainDispatchRequest(
                objective="smoke", crew="codebuddy", task_kind="research",
                workspace_strategy=member.value,
            )
            self.assertEqual(request.workspace_strategy, member.value)

    def test_routing_metadata_persists_strategy(self) -> None:
        request = CaptainDispatchRequest(
            objective="smoke", crew="codebuddy", task_kind="research",
            workspace_strategy="frozen_context",
        )
        self.assertEqual(request.routing_metadata()["workspace_strategy"], "frozen_context")


class McpWorkspaceStrategyDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._environment = patch.dict(
            "os.environ", {"TP_VOYAGER_HOME": str(Path(self.tmp.name) / "tp-voyager-home")}, clear=False
        )
        self._environment.start()
        self.db_path = Path(self.tmp.name) / "runtime.db"
        database = server.configure_runtime_database(self.db_path)
        assert database is not None
        server.TASKS.clear()
        server.IDEMPOTENCY_TASKS.clear()
        # Workers are stubbed, so crew worker slots would otherwise accumulate.
        server._CREW_ACTIVE_WORKERS["qoder"] = 0
        server._CREW_ACTIVE_WORKERS["codebuddy"] = 0

    def tearDown(self) -> None:
        server.TASKS.clear()
        server.IDEMPOTENCY_TASKS.clear()
        server._CREW_ACTIVE_WORKERS["qoder"] = 0
        server._CREW_ACTIVE_WORKERS["codebuddy"] = 0
        server.configure_runtime_database(None)
        self._environment.stop()
        self.tmp.cleanup()

    def _dispatch(
        self,
        *,
        strategy: str = "isolated_patch",
        idempotency_key: str = "",
        cwd: str | None = None,
        patch_policy: dict | None = None,
    ) -> dict:
        with patch.object(server, "_start_worker_thread", return_value=None):
            return server.task_dispatch(
                objective="Inspect the bounded fixture without modifying it",
                crew="qoder",
                task_kind="research",
                cwd=self.tmp.name if cwd is None else cwd,
                model="lite",
                idempotency_key=idempotency_key,
                patch_policy=patch_policy,
                workspace_strategy=strategy,
                timeout_seconds=30,
            )

    def _session_routing(self, task_id: str) -> dict:
        runtime = server._runtime_service()
        session = runtime.get_session(task_id)
        metadata = server.parse_session_metadata(session.metadata_json)
        routing = metadata.get("routing_metadata")
        self.assertIsInstance(routing, dict)
        return routing

    def test_rejects_invalid_workspace_strategy(self) -> None:
        result = self._dispatch(strategy="unknown_strategy")
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("reason_code"), "INVALID_WORKSPACE_STRATEGY")
        self.assertIn("workspace_strategy", str(result.get("detail") or ""))

    def test_model_only_clears_cwd_and_snapshot_contracts(self) -> None:
        captured: list[dict] = []

        def _spy(**kwargs):
            captured.append(kwargs)
            return CaptainDispatchRequest(**kwargs)

        with patch.object(server, "_start_worker_thread", return_value=None):
            with patch.object(server, "CaptainDispatchRequest", side_effect=_spy) as spy:
                result = server.task_dispatch(
                    objective="model smoke only",
                    crew="qoder",
                    task_kind="research",
                    cwd=self.tmp.name,
                    model="lite",
                    repository_research={
                        "url": "https://example.invalid/repo.git",
                        "report_path": "reports/r.md",
                    },
                    workspace_strategy="model_only",
                    timeout_seconds=30,
                )
        self.assertTrue(result.get("ok"), result)
        spy.assert_called_once()
        self.assertEqual(captured[0]["cwd"], "")
        # Snapshot/research contracts are dropped for model checks.
        self.assertIsNone(captured[0]["repository_research"])
        self.assertIsNone(captured[0]["repository_snapshot_ref"])

        task_id = str(result.get("task_id") or "")
        self.assertTrue(task_id)
        routing = self._session_routing(task_id)
        self.assertEqual(routing.get("workspace_strategy"), "model_only")
        # model_only must not produce repository snapshot/research routing.
        self.assertNotIn("repository_snapshot_ref", routing)
        self.assertNotIn("repository_research", routing)

    def test_live_readonly_clears_patch_policy(self) -> None:
        result = self._dispatch(strategy="live_readonly", patch_policy={"forbidden_paths": ["x"]})
        self.assertTrue(result.get("ok"), result)
        task_id = str(result.get("task_id") or "")
        routing = self._session_routing(task_id)
        self.assertEqual(routing.get("workspace_strategy"), "live_readonly")
        # patch_policy was stripped, so it must not appear in routing metadata.
        self.assertNotIn("patch_policy", routing)

    def test_strategy_flows_through_routing_metadata(self) -> None:
        for strategy in ("model_only", "live_readonly", "frozen_context", "isolated_patch"):
            # Workers are stubbed, so each stub dispatch must release its slot
            # before the next one or the crew limit is reached.
            server._CREW_ACTIVE_WORKERS["qoder"] = 0
            result = self._dispatch(strategy=strategy)
            self.assertTrue(result.get("ok"), result)
            task_id = str(result.get("task_id") or "")
            routing = self._session_routing(task_id)
            self.assertEqual(routing.get("workspace_strategy"), strategy)

    def test_idempotency_contract_includes_workspace_strategy(self) -> None:
        result = self._dispatch(strategy="frozen_context", idempotency_key="ws-contract-1")
        self.assertTrue(result.get("ok"), result)
        task_id = str(result.get("task_id") or "")
        routing = self._session_routing(task_id)
        contract = routing.get("captain_request_contract")
        self.assertIsInstance(contract, dict)
        self.assertEqual(contract.get("workspace_strategy"), "frozen_context")

    def test_idempotency_conflict_across_different_strategies(self) -> None:
        first = self._dispatch(strategy="frozen_context", idempotency_key="ws-conflict-1")
        self.assertTrue(first.get("ok"), first)
        conflicting = self._dispatch(strategy="model_only", idempotency_key="ws-conflict-1")
        self.assertFalse(conflicting.get("ok"), conflicting)
        self.assertEqual(conflicting.get("reason_code"), "IDEMPOTENCY_CONFLICT")

    def test_same_strategy_is_idempotent(self) -> None:
        first = self._dispatch(strategy="isolated_patch", idempotency_key="ws-repeat-1")
        self.assertTrue(first.get("ok"), first)
        repeated = self._dispatch(strategy="isolated_patch", idempotency_key="ws-repeat-1")
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertEqual(repeated.get("task_id"), first.get("task_id"))


if __name__ == "__main__":
    unittest.main()
