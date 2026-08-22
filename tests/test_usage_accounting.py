from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.application.task_service import TaskService
from agent_runtime.application.voyage.observability import AgentObservationStore, VoyageAgentProjection
from agent_runtime.backends.base import BackendStartRequest, BackendUsage
from agent_runtime.backends.codebuddy.backend import CodeBuddyBackend
from agent_runtime.backends.qoder.acp_client import AcpRunResult
from agent_runtime.backends.qoder.backend import QoderBackend
from agent_runtime.domain.enums import EventType, TaskRoute
from agent_runtime.domain.session import Session
from agent_runtime.domain.structured_result import RESULT_SCHEMA, StructuredResult
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database


class UsageAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()
        self.service = TaskService(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _task(self, task_id: str, *, group_id: str = "") -> None:
        task = Task(
            task_id=task_id,
            task_type="qoder",
            status="queued",
            route=TaskRoute.ACP.value,
            created_at=1.0,
            updated_at=1.0,
        )
        session = Session(
            session_id=f"rs-{task_id}",
            task_id=task_id,
            backend="qoder",
            route=TaskRoute.ACP.value,
            created_at=1.0,
            updated_at=1.0,
        )
        metadata = {}
        if group_id:
            metadata = {"routing_metadata": {"presentation_group_id": group_id}}
        self.service.create_task(
            task=task,
            session=session,
            metadata=metadata,
            idempotency_key="",
            request_fingerprint=f"fp-{task_id}",
            now=1.0,
        )

    def test_codebuddy_usage_maps_credit_and_token_breakdown(self) -> None:
        request = BackendStartRequest(
            task_id="cb", attempt_id="at", runtime_session_id="rs", prompt="x", cwd=str(self.root), model="hy3"
        )
        fact = CodeBuddyBackend._usage_fact(
            request,
            {
                "input_tokens": 100,
                "cache_read_input_tokens": 40,
                "cache_creation_input_tokens": 10,
                "output_tokens": 20,
                "reasoning_tokens": 5,
                "answer_tokens": 15,
                "credit": 1.25,
            },
            None,
        )
        self.assertIsNotNone(fact)
        payload = fact.to_dict()
        usage = payload["usage"]
        self.assertEqual(payload["scope"], "turn")
        self.assertEqual(usage["credits"], 1.25)
        self.assertEqual(usage["input_tokens"], 150)
        self.assertEqual(usage["cache_read_tokens"], 40)
        self.assertEqual(usage["cache_write_tokens"], 10)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["reasoning_tokens"], 5)
        self.assertEqual(usage["answer_tokens"], 15)
        self.assertEqual(usage["cache_miss_tokens"], 100)
        self.assertEqual(usage["total_tokens"], 170)
        self.assertIn("input_tokens", usage["derived_fields"])
        self.assertIn("cache_miss_tokens", usage["derived_fields"])
        self.assertIn("total_tokens", usage["derived_fields"])


    def test_codebuddy_real_acp_meta_usage_maps_prompt_tokens_without_cache_double_count(self) -> None:
        request = BackendStartRequest(
            task_id="cb-real-acp", attempt_id="at", runtime_session_id="rs",
            prompt="x", cwd=str(self.root), model="deepseek-v4-flash"
        )
        fact = CodeBuddyBackend._usage_fact(
            request,
            {
                "_meta": {
                    "usage": {
                        "prompt_tokens": 28600,
                        "completion_tokens": 2,
                        "total_tokens": 28602,
                        "prompt_cache_hit_tokens": 28544,
                        "prompt_cache_miss_tokens": 56,
                        "credit": 0.09,
                    }
                }
            },
            None,
            source="codebuddy_acp_usage_update",
            accounting="delta",
            sample_id="req-real-123",
        )
        self.assertIsNotNone(fact)
        payload = fact.to_dict()
        usage = payload["usage"]
        self.assertEqual(payload["source"], "codebuddy_acp_usage_update")
        self.assertEqual(usage["input_tokens"], 28600)
        self.assertEqual(usage["cache_read_tokens"], 28544)
        self.assertEqual(usage["cache_miss_tokens"], 56)
        self.assertIsNone(usage["cache_write_tokens"])
        self.assertEqual(usage["output_tokens"], 2)
        self.assertEqual(usage["total_tokens"], 28602)
        self.assertEqual(usage["credits"], 0.09)
        self.assertNotIn("input_tokens", usage["derived_fields"])

    def test_codebuddy_acp_missing_provider_total_stays_none_instead_of_being_derived(self) -> None:
        request = BackendStartRequest(
            task_id="cb-missing-total", attempt_id="at", runtime_session_id="rs",
            prompt="x", cwd=str(self.root), model="deepseek-v4-flash",
        )
        fact = CodeBuddyBackend._usage_fact(
            request, {"prompt_tokens": 12, "completion_tokens": 3}, None,
            source="codebuddy_acp_usage_update",
        )
        self.assertIsNotNone(fact)
        usage = fact.to_dict()["usage"]
        self.assertEqual(usage["input_tokens"], 12)
        self.assertEqual(usage["output_tokens"], 3)
        self.assertIsNone(usage["total_tokens"])
        self.assertNotIn("total_tokens", usage["derived_fields"])

    def test_codebuddy_acp_credit_zero_is_observed_and_missing_credit_stays_none(self) -> None:
        request = BackendStartRequest(
            task_id="cb-zero", attempt_id="at", runtime_session_id="rs",
            prompt="x", cwd=str(self.root), model="deepseek-v4-flash"
        )
        zero = CodeBuddyBackend._usage_fact(
            request, {"prompt_tokens": 1, "credit": 0}, None,
            source="codebuddy_acp_usage_update",
        )
        missing = CodeBuddyBackend._usage_fact(
            request, {"prompt_tokens": 1}, None,
            source="codebuddy_acp_usage_update",
        )
        self.assertEqual(zero.to_dict()["usage"]["credits"], 0.0)
        self.assertIsNone(missing.to_dict()["usage"]["credits"])

    def test_qoder_usage_maps_request_and_session_credits_without_conflation(self) -> None:
        request = BackendStartRequest(
            task_id="q", attempt_id="at", runtime_session_id="rs", prompt="x", cwd=str(self.root), model="lite"
        )
        fact = QoderBackend._usage_fact(
            request,
            {
                "usage": {
                    "credits": 0.45,
                    "original_credits": 0.6,
                    "billable": True,
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "cached_tokens": 20,
                },
                "total_credits": 3.2,
                "model_usage": {"lite": {"input_tokens": 120, "output_tokens": 30, "credits": 0.45}},
            },
        )
        self.assertIsNotNone(fact)
        usage = fact.to_dict()["usage"]
        self.assertEqual(usage["credits"], 0.45)
        self.assertEqual(usage["session_credits"], 3.2)
        self.assertEqual(usage["original_credits"], 0.6)
        self.assertIs(usage["billable"], True)
        self.assertEqual(usage["cache_read_tokens"], 20)

    def test_qoder_nested_model_usage_is_preserved_as_session_breakdown(self) -> None:
        request = BackendStartRequest(
            task_id="q-model", attempt_id="at", runtime_session_id="rs",
            prompt="x", cwd=str(self.root), model="lite",
        )
        fact = QoderBackend._usage_fact(
            request,
            {
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "modelUsage": {"lite": {"credits": 0.25, "input_tokens": 20}},
                },
                "total_credits": 0.25,
            },
        )
        self.assertIsNotNone(fact)
        payload = fact.to_dict()
        self.assertEqual(payload["model_usage"]["lite"]["credits"], 0.25)

    def test_distinct_usage_evidence_is_append_only_and_exact_duplicates_are_idempotent(self) -> None:
        self._task("usage-task")
        first = BackendUsage(provider="qoder", scope="turn", credits=0.4, session_credits=1.2, source="qoder_result").to_dict()
        second = BackendUsage(provider="qoder", scope="turn", credits=0.5, session_credits=1.7, source="qoder_result").to_dict()
        self.assertTrue(self.service.append_usage_evidence("usage-task", usage=first))
        self.assertFalse(self.service.append_usage_evidence("usage-task", usage=first))
        self.assertTrue(self.service.append_usage_evidence("usage-task", usage=second))
        summary = self.service.latest_usage_evidence("usage-task")
        self.assertEqual(summary["usage"]["credits"], 0.9)
        self.assertEqual(summary["usage"]["session_credits"], 1.7)
        self.assertEqual(summary["sample_count"], 2)

    def test_backend_usage_drops_raw_provider_strings_and_keeps_only_usage_scalars(self) -> None:
        payload = BackendUsage(
            provider="codebuddy",
            provider_usage={
                "inputTokens": 12,
                "outputTokens": 3,
                "billable": True,
                "rawInput": "secret prompt",
                "rawOutput": "secret reply",
                "command": "rm -rf /",
                "path": "/absolute/private/path",
            },
        ).to_dict()
        self.assertEqual(payload["provider_usage"], {
            "inputTokens": 12,
            "outputTokens": 3,
            "billable": True,
        })

    def test_multiple_turn_original_credits_sum_but_session_total_is_latest_snapshot(self) -> None:
        self._task("usage-original")
        first = BackendUsage(
            provider="qoder", scope="turn", credits=0.4, original_credits=0.6,
            session_credits=1.2, source="qoder_result",
        ).to_dict()
        second = BackendUsage(
            provider="qoder", scope="turn", credits=0.5, original_credits=0.7,
            session_credits=1.7, source="qoder_result",
        ).to_dict()
        self.assertTrue(self.service.append_usage_evidence("usage-original", usage=first))
        self.assertTrue(self.service.append_usage_evidence("usage-original", usage=second))
        summary = self.service.latest_usage_evidence("usage-original")
        self.assertEqual(summary["usage"]["credits"], 0.9)
        self.assertAlmostEqual(summary["usage"]["original_credits"], 1.3)
        self.assertEqual(summary["usage"]["session_credits"], 1.7)


    def test_codebuddy_distinct_turn_samples_accumulate_token_and_credit_once(self) -> None:
        self._task("cb-turns")
        request = BackendStartRequest(
            task_id="cb-turns", attempt_id="at", runtime_session_id="rs",
            prompt="x", cwd=str(self.root), model="hy3",
        )
        for sample_id, raw in (
            ("turn-1", {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10, "credit": 0.4}),
            ("turn-2", {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10, "credit": 0.5}),
        ):
            fact = CodeBuddyBackend._usage_fact(
                request, raw, None,
                source="codebuddy_acp_usage_update", accounting="delta", sample_id=sample_id,
            )
            self.assertIsNotNone(fact)
            self.assertTrue(self.service.append_usage_evidence("cb-turns", usage=fact.to_dict()))
        summary = self.service.latest_usage_evidence("cb-turns")
        self.assertEqual(summary["usage"]["input_tokens"], 15)
        self.assertEqual(summary["usage"]["output_tokens"], 5)
        self.assertEqual(summary["usage"]["total_tokens"], 20)
        self.assertEqual(summary["usage"]["credits"], 0.9)

    def test_qoder_distinct_requests_accumulate_request_usage_and_keep_latest_session_total(self) -> None:
        self._task("q-requests")
        request = BackendStartRequest(
            task_id="q-requests", attempt_id="at", runtime_session_id="rs",
            prompt="x", cwd=str(self.root), model="lite",
        )
        for sample_id, credits, total, input_tokens, output_tokens in (
            ("req-1", 0.25, 1.25, 10, 2),
            ("req-2", 0.40, 1.65, 20, 3),
        ):
            fact = QoderBackend._usage_fact(
                request,
                {"usage": {"credits": credits, "input_tokens": input_tokens, "output_tokens": output_tokens}, "total_credits": total},
                source="qoder_acp_usage_update", accounting="delta", sample_id=sample_id,
            )
            self.assertIsNotNone(fact)
            self.assertTrue(self.service.append_usage_evidence("q-requests", usage=fact.to_dict()))
        # terminal/finally can replay the last request as a session snapshot;
        # it must not be added to the request deltas.
        terminal = QoderBackend._usage_fact(
            request,
            {"usage": {"credits": 0.40, "input_tokens": 20, "output_tokens": 3}, "total_credits": 1.65},
            source="qoder_acp_terminal_snapshot", accounting="snapshot",
        )
        self.assertIsNotNone(terminal)
        self.assertTrue(self.service.append_usage_evidence("q-requests", usage=terminal.to_dict()))
        self.assertFalse(self.service.append_usage_evidence("q-requests", usage=terminal.to_dict()))
        summary = self.service.latest_usage_evidence("q-requests")
        self.assertEqual(summary["usage"]["input_tokens"], 30)
        self.assertEqual(summary["usage"]["output_tokens"], 5)
        self.assertEqual(summary["usage"]["total_tokens"], 35)
        self.assertEqual(summary["usage"]["credits"], 0.65)
        self.assertEqual(summary["usage"]["session_credits"], 1.65)

    def test_delta_samples_deduplicate_by_provider_sample_id_and_sum_distinct_turns(self) -> None:
        self._task("sample-id-usage")
        first = BackendUsage(
            provider="codebuddy", scope="turn", credits=0.4, total_tokens=10,
            source="codebuddy_acp_usage_update", accounting="delta", sample_id="turn-1",
        ).to_dict()
        first_richer = BackendUsage(
            provider="codebuddy", scope="turn", credits=0.4, total_tokens=12,
            source="codebuddy_acp_usage_update", accounting="delta", sample_id="turn-1",
        ).to_dict()
        second = BackendUsage(
            provider="codebuddy", scope="turn", credits=0.5, total_tokens=20,
            source="codebuddy_acp_usage_update", accounting="delta", sample_id="turn-2",
        ).to_dict()
        self.assertTrue(self.service.append_usage_evidence("sample-id-usage", usage=first))
        self.assertTrue(self.service.append_usage_evidence("sample-id-usage", usage=first_richer))
        self.assertTrue(self.service.append_usage_evidence("sample-id-usage", usage=second))
        summary = self.service.latest_usage_evidence("sample-id-usage")
        self.assertEqual(summary["usage"]["credits"], 0.9)
        self.assertEqual(summary["usage"]["total_tokens"], 32)

    def test_same_provider_sample_id_merges_late_enrichment_without_losing_prior_fields(self) -> None:
        self._task("sample-enrichment")
        first = BackendUsage(
            provider="qoder", scope="turn", credits=0.25, input_tokens=10,
            source="qoder_acp_usage_update", accounting="delta", sample_id="req-1",
        ).to_dict()
        second = BackendUsage(
            provider="qoder", scope="turn", output_tokens=2, original_credits=0.3,
            source="qoder_acp_usage_update", accounting="delta", sample_id="req-1",
        ).to_dict()
        self.assertTrue(self.service.append_usage_evidence("sample-enrichment", usage=first))
        self.assertTrue(self.service.append_usage_evidence("sample-enrichment", usage=second))
        summary = self.service.latest_usage_evidence("sample-enrichment")
        self.assertEqual(summary["usage"]["credits"], 0.25)
        self.assertEqual(summary["usage"]["input_tokens"], 10)
        self.assertEqual(summary["usage"]["output_tokens"], 2)
        self.assertEqual(summary["usage"]["original_credits"], 0.3)

    def test_codebuddy_request_delta_and_terminal_snapshot_replays_do_not_double_count(self) -> None:
        self._task("codebuddy-terminal-idempotent")
        request_fact = BackendUsage(
            provider="codebuddy", scope="turn", credits=0.75, input_tokens=8, output_tokens=2,
            source="codebuddy_acp_usage_update", accounting="delta", sample_id="turn-1",
        ).to_dict()
        terminal_fact = BackendUsage(
            provider="codebuddy", scope="turn", credits=0.75, input_tokens=8, output_tokens=2,
            source="codebuddy_sdk_result", accounting="snapshot",
        ).to_dict()
        self.assertTrue(self.service.append_usage_evidence("codebuddy-terminal-idempotent", usage=request_fact))
        self.assertFalse(self.service.append_usage_evidence("codebuddy-terminal-idempotent", usage=request_fact))
        self.assertTrue(self.service.append_usage_evidence("codebuddy-terminal-idempotent", usage=terminal_fact))
        self.assertFalse(self.service.append_usage_evidence("codebuddy-terminal-idempotent", usage=terminal_fact))
        summary = self.service.latest_usage_evidence("codebuddy-terminal-idempotent")
        self.assertEqual(summary["usage"]["credits"], 0.75)
        self.assertEqual(summary["usage"]["input_tokens"], 8)
        self.assertEqual(summary["usage"]["output_tokens"], 2)

    def test_snapshot_usage_uses_latest_values_without_accumulating_repeated_refreshes(self) -> None:
        self._task("snapshot-usage")
        first = BackendUsage(
            provider="qoder", scope="turn", credits=0.25, total_tokens=10,
            session_credits=1.25, source="qoder_acp_usage_update", accounting="snapshot",
        ).to_dict()
        second = BackendUsage(
            provider="qoder", scope="turn", credits=0.40, total_tokens=20,
            session_credits=1.65, source="qoder_acp_usage_update", accounting="snapshot",
        ).to_dict()
        self.assertTrue(self.service.append_usage_evidence("snapshot-usage", usage=first))
        self.assertFalse(self.service.append_usage_evidence("snapshot-usage", usage=first))
        self.assertTrue(self.service.append_usage_evidence("snapshot-usage", usage=second))
        self.assertFalse(self.service.append_usage_evidence("snapshot-usage", usage=second))
        summary = self.service.latest_usage_evidence("snapshot-usage")
        self.assertEqual(summary["usage"]["credits"], 0.40)
        self.assertEqual(summary["usage"]["total_tokens"], 20)
        self.assertEqual(summary["usage"]["session_credits"], 1.65)

    def test_request_deltas_do_not_add_terminal_or_finally_snapshot_credit(self) -> None:
        self._task("terminal-idempotent")
        for usage in (
            BackendUsage(provider="qoder", scope="turn", credits=0.25, total_tokens=10,
                         source="qoder_acp_usage_update", accounting="delta", sample_id="req-1"),
            BackendUsage(provider="qoder", scope="turn", credits=0.40, total_tokens=20,
                         source="qoder_acp_usage_update", accounting="delta", sample_id="req-2"),
            BackendUsage(provider="qoder", scope="turn", credits=0.40, total_tokens=20,
                         session_credits=1.65, source="qoder_acp_terminal_snapshot", accounting="snapshot"),
        ):
            self.service.append_usage_evidence("terminal-idempotent", usage=usage.to_dict())
        # Simulate finally replaying the same terminal snapshot.
        replay = BackendUsage(provider="qoder", scope="turn", credits=0.40, total_tokens=20,
                              session_credits=1.65, source="qoder_acp_terminal_snapshot", accounting="snapshot").to_dict()
        self.assertFalse(self.service.append_usage_evidence("terminal-idempotent", usage=replay))
        summary = self.service.latest_usage_evidence("terminal-idempotent")
        self.assertEqual(summary["usage"]["credits"], 0.65)
        self.assertEqual(summary["usage"]["total_tokens"], 30)
        self.assertEqual(summary["usage"]["session_credits"], 1.65)

    def test_qoder_backend_result_and_finally_replay_same_samples_idempotently(self) -> None:
        self._task("q-backend-replay")
        request = BackendStartRequest(
            task_id="q-backend-replay", attempt_id="at", runtime_session_id="rs",
            prompt="x", cwd=str(self.root), model="lite",
            metadata={"route": "acp_patch", "patch_policy": {}},
        )
        samples = (
            {
                "usage": {"credits": 0.25, "input_tokens": 10, "output_tokens": 2, "total_credits": 1.25},
                "sample_id": "req-1", "accounting": "delta",
            },
            {
                "usage": {"credits": 0.40, "input_tokens": 20, "output_tokens": 3, "total_credits": 1.65},
                "sample_id": "req-2", "accounting": "delta",
            },
        )

        class FakeClient:
            def __init__(self, *, cwd, on_activity, **_kwargs) -> None:
                self.cwd = cwd
                self.on_activity = on_activity
                self.process = type("P", (), {"poll": lambda self: 0})()

            def run(self, **kwargs):
                kwargs["on_dispatch_accepted"]("qoder-session")
                return AcpRunResult(
                    session_id="qoder-session", stop_reason="end_turn", answer="done",
                    observability={"route": "acp", "event_count": 2},
                    usage={"credits": 0.40, "input_tokens": 20, "output_tokens": 3, "total_credits": 1.65},
                    usage_samples=samples,
                )

            def usage_samples(self):
                return [dict(item) for item in samples]

            def usage_snapshot(self):
                return {"credits": 0.40, "input_tokens": 20, "output_tokens": 3, "total_credits": 1.65}

            def cancel(self, _session_id="") -> None:
                return None

            def close(self) -> None:
                return None

        accepted: list[str] = []
        append_results: list[bool] = []

        class Callbacks:
            def on_dispatch_accepted(_self, session_id: str) -> None:
                accepted.append(session_id)

            def on_activity(_self, _activity) -> None:
                return None

            def on_usage(_self, usage: BackendUsage) -> None:
                append_results.append(
                    self.service.append_usage_evidence("q-backend-replay", usage=usage.to_dict())
                )

            def on_result(_self, _result) -> None:
                return None

        backend = QoderBackend(patch_acp_client_factory=lambda **kwargs: FakeClient(**kwargs))
        result = backend.start(request, Callbacks())

        self.assertEqual(result.answer, "done")
        self.assertEqual(accepted, ["qoder-session"])
        # Result stage persists each logical request; finally replays the same
        # bounded samples and exact durable fingerprints reject both repeats.
        self.assertEqual(append_results, [True, True, False, False])
        summary = self.service.latest_usage_evidence("q-backend-replay")
        self.assertEqual(summary["usage"]["credits"], 0.65)
        self.assertEqual(summary["usage"]["input_tokens"], 30)
        self.assertEqual(summary["usage"]["output_tokens"], 5)
        self.assertEqual(summary["usage"]["total_tokens"], 35)
        self.assertEqual(summary["usage"]["session_credits"], 1.65)

    def test_missing_usage_stays_null_instead_of_zero(self) -> None:
        payload = BackendUsage(provider="qoder", scope="turn", source="qoder_result").to_dict()
        usage = payload["usage"]
        self.assertIsNone(usage["total_tokens"])
        self.assertIsNone(usage["credits"])
        self.assertIsNone(usage["session_credits"])

    def test_group_projection_sums_child_turn_usage_but_not_session_cumulative_credits(self) -> None:
        self._task("g-1", group_id="pg-1")
        self._task("g-2", group_id="pg-1")
        self.service.append_usage_evidence(
            "g-1",
            usage=BackendUsage(provider="qoder", scope="turn", total_tokens=100, credits=0.4, session_credits=1.2).to_dict(),
        )
        self.service.append_usage_evidence(
            "g-2",
            usage=BackendUsage(provider="codebuddy", scope="turn", total_tokens=50, credits=0.3, session_credits=9.9).to_dict(),
        )
        projection = VoyageAgentProjection(self.service, AgentObservationStore())
        grouped = projection.group(presentation_group_id="pg-1")
        self.assertEqual(grouped["usage"]["total_tokens"], 150)
        self.assertEqual(grouped["usage"]["credits"], 0.7)
        self.assertNotIn("session_credits", grouped["usage"])
        explicit = projection.group(task_ids=["g-1", "g-2"])
        self.assertEqual(explicit["usage"]["total_tokens"], 150)
        self.assertEqual(explicit["usage"]["credits"], 0.7)

    def test_usage_evidence_survives_database_reopen_and_projection_refresh(self) -> None:
        self._task("persisted-usage")
        fact = BackendUsage(
            provider="qoder",
            scope="turn",
            model="lite",
            total_tokens=180,
            input_tokens=150,
            output_tokens=30,
            credits=0.45,
            session_credits=3.2,
            source="qoder_acp_usage_update",
        ).to_dict()
        self.assertTrue(self.service.append_usage_evidence("persisted-usage", usage=fact))

        reopened = TaskService(Database(self.root / "runtime.db"))
        reopened.db.initialize()
        projection = VoyageAgentProjection(reopened, AgentObservationStore())
        first = projection.detail("persisted-usage")
        second = projection.detail("persisted-usage")

        self.assertEqual(first["usage"], second["usage"])
        self.assertEqual(second["usage"]["usage"]["credits"], 0.45)
        self.assertEqual(second["usage"]["usage"]["session_credits"], 3.2)
        self.assertNotIn("reported_cost", second["usage"]["usage"])
        self.assertNotIn("currency", second["usage"]["usage"])
        self.assertNotIn("provider_usage", second["usage"])


    def test_completed_usage_survives_refresh_without_exposing_money_fields(self) -> None:
        self._task("completed-usage")
        task = self.service.get_task("completed-usage")
        assert task is not None
        self.service.update_status(
            "completed-usage", status="running",
            event_type=EventType.TASK_STARTED.value, version=task.version, started_at=2.0,
        )
        fact = BackendUsage(
            provider="codebuddy", scope="turn", model="hy3", total_tokens=250,
            input_tokens=200, output_tokens=50, credits=0.8,
            reported_cost=12.34, currency="USD", source="codebuddy_sdk_result",
        ).to_dict()
        self.service.append_usage_evidence("completed-usage", usage=fact)
        task = self.service.get_task("completed-usage")
        assert task is not None
        self.service.save_result(
            "completed-usage",
            structured_result=StructuredResult(
                schema=RESULT_SCHEMA, attempt_id=task.current_attempt_id or "",
                answer="done", backend="codebuddy", stop_reason="end_turn",
            ),
            status="completed", version=task.version, terminal_reason="end_turn",
        )

        reopened = TaskService(Database(self.root / "runtime.db"))
        reopened.db.initialize()
        projection = VoyageAgentProjection(reopened, AgentObservationStore())
        first = projection.detail("completed-usage")
        second = projection.detail("completed-usage")
        for detail in (first, second):
            self.assertEqual(detail["usage"]["usage"]["total_tokens"], 250)
            self.assertEqual(detail["usage"]["usage"]["credits"], 0.8)
            self.assertNotIn("reported_cost", detail["usage"]["usage"])
            self.assertNotIn("currency", detail["usage"]["usage"])

    def test_codebuddy_acp_shaped_nested_usage_maps_credit_without_cost_fields(self) -> None:
        request = BackendStartRequest(
            task_id="cb-nested", attempt_id="at", runtime_session_id="rs",
            prompt="x", cwd=str(self.root), model="hy3"
        )
        fact = CodeBuddyBackend._usage_fact(
            request,
            {
                "usage": {
                    "input_tokens": 80,
                    "cache_read_tokens": 20,
                    "cache_write_tokens": 5,
                    "output_tokens": 10,
                    "credit": 0.75,
                }
            },
            9.99,
        )
        self.assertIsNotNone(fact)
        payload = fact.to_dict()
        self.assertEqual(payload["usage"]["credits"], 0.75)
        self.assertEqual(payload["usage"]["total_tokens"], 115)
        # Token/Credit projection never treats the legacy SDK monetary field as Credit.
        self.assertNotEqual(payload["usage"]["credits"], payload["usage"].get("reported_cost"))



if __name__ == "__main__":
    unittest.main()
