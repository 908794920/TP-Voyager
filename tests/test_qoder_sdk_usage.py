from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_runtime.application.task_service import TaskService
from agent_runtime.backends.base import BackendStartRequest, BackendUsage
from agent_runtime.backends.qoder.acp_client import AcpRunResult
from agent_runtime.backends.qoder.backend import QoderBackend
from agent_runtime.domain.enums import TaskRoute
from agent_runtime.domain.session import Session
from agent_runtime.domain.task import Task
from agent_runtime.persistence.database import Database


REAL_CLI_PATH = r"C:\Users\tangpeng\.tp-voyager\bin\qodercli-qoder-client.cmd"
MODEL = "qmodel_38max"
REQUEST_ID = "8b47b30b-5b93-4322-b67e-439c9ff11e7d"

# User-provided real Qoder SDK fixtures. These are fixed test data; tests do
# not invoke qoder/qodercli/qoder_agent_sdk.
ACCOUNT_USAGE_FIXTURE = {
    "userQuota": {
        "total": 300,
        "used": 213,
        "remaining": 87,
        "unit": "credits",
    },
    "isQuotaExceeded": False,
}
ASSISTANT_MESSAGE_FIXTURE = {
    "type": "assistant",
    "model": MODEL,
    "usage": {
        "input_tokens": 0,
        "output_tokens": 0,
        "credits": 1.178684375,
        "original_credits": 2.35736875,
        "billable": True,
        "request_id": REQUEST_ID,
    },
}
REAL_IMPORTED_ASSISTANT_ENTRY_FIXTURE = SimpleNamespace(
    type="assistant",
    message=SimpleNamespace(
        type="message",
        role="assistant",
        model=MODEL,
        usage={
            "input_tokens": 0,
            "output_tokens": 0,
            "credits": 1.178684375,
            "original_credits": 2.35736875,
            "billable": True,
            "request_id": REQUEST_ID,
        },
    ),
)
RESULT_MESSAGE_FIXTURE = {
    "type": "result",
    "total_credits": 1.178684375,
    "model_usage": {
        MODEL: {
            "credits": 1.178684375,
        }
    },
}
ACP_ZERO_USAGE_FIXTURE = {
    "usage": {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    },
    "_meta": {
        "quota": {
            "token_count": 0,
            "model_usage": {
                MODEL: {"token_count": 0},
            },
        }
    },
}


def _adapter_type(testcase: unittest.TestCase):
    try:
        module = importlib.import_module("agent_runtime.backends.qoder.sdk_usage")
    except ImportError as exc:  # RED: the adapter does not exist yet.
        testcase.fail(f"Qoder SDK usage adapter is missing: {exc}")
    return module.QoderSdkUsageAdapter


class FakeSdkModule:
    def __init__(self, entries):
        self.entries = list(entries)
        self.import_calls = []

    async def import_session_to_store(
        self,
        session_id,
        store,
        *,
        directory=None,
        include_subagents=True,
        **kwargs,
    ):
        self.import_calls.append(
            {
                "session_id": session_id,
                "directory": str(directory) if directory is not None else None,
                "include_subagents": include_subagents,
                **kwargs,
            }
        )
        await store.append(
            {"project_key": "fixture", "session_id": session_id},
            list(self.entries),
        )


class QoderSdkUsageAdapterTests(unittest.TestCase):
    def _adapter(self, entries):
        adapter_cls = _adapter_type(self)
        sdk = FakeSdkModule(entries)
        return adapter_cls(cli_path=REAL_CLI_PATH, sdk_module=sdk), sdk

    def test_parses_real_imported_nested_assistant_message_usage(self) -> None:
        adapter, _sdk = self._adapter([REAL_IMPORTED_ASSISTANT_ENTRY_FIXTURE])
        result = adapter.collect_session_usage(
            session_id="qoder-session",
            cwd="C:/repo",
            model=MODEL,
        )

        self.assertEqual(result.status, "observed")
        self.assertGreaterEqual(len(result.facts), 1)
        payload = result.facts[0].to_dict()
        usage = payload["usage"]
        self.assertEqual(usage["credits"], 1.178684375)
        self.assertEqual(usage["original_credits"], 2.35736875)
        self.assertIs(usage["billable"], True)
        self.assertTrue(payload["request_id"])
        self.assertEqual(payload["request_id"], REQUEST_ID)
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertIsNone(usage["total_tokens"])

    def test_parses_real_assistant_request_credit_fixture_and_preserves_fields(self) -> None:
        adapter, _sdk = self._adapter([ASSISTANT_MESSAGE_FIXTURE])
        result = adapter.collect_session_usage(
            session_id="qoder-session",
            cwd="C:/repo",
            model=MODEL,
        )

        self.assertEqual(result.status, "observed")
        self.assertEqual(len(result.facts), 1)
        payload = result.facts[0].to_dict()
        usage = payload["usage"]
        self.assertEqual(usage["credits"], 1.178684375)
        self.assertEqual(usage["original_credits"], 2.35736875)
        self.assertIs(usage["billable"], True)
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(payload["request_id"], REQUEST_ID)
        self.assertEqual(payload["sample_id"], REQUEST_ID)

    def test_parses_real_result_total_credits_as_session_credit_only(self) -> None:
        adapter, _sdk = self._adapter([RESULT_MESSAGE_FIXTURE])
        result = adapter.collect_session_usage(
            session_id="qoder-session",
            cwd="C:/repo",
            model=MODEL,
        )

        self.assertEqual(len(result.facts), 1)
        usage = result.facts[0].to_dict()["usage"]
        self.assertIsNone(usage["credits"])
        self.assertEqual(usage["session_credits"], 1.178684375)

    def test_result_model_usage_credit_is_session_fallback_when_total_missing(self) -> None:
        fixture = {
            "type": "result",
            "model_usage": {MODEL: {"credits": 1.178684375}},
        }
        adapter, _sdk = self._adapter([fixture])
        result = adapter.collect_session_usage(
            session_id="qoder-session",
            cwd="C:/repo",
            model=MODEL,
        )
        usage = result.facts[0].to_dict()["usage"]
        self.assertEqual(usage["session_credits"], 1.178684375)
        self.assertIsNone(usage["credits"])

    def test_same_request_id_is_counted_once_and_later_messages_only_enrich_missing_fields(self) -> None:
        later = {
            "type": "assistant",
            "model": MODEL,
            "usage": {
                "request_id": REQUEST_ID,
                "credits": 99.0,
                "cache_read_input_tokens": 17,
                "cache_creation_input_tokens": 3,
            },
        }
        adapter, _sdk = self._adapter([ASSISTANT_MESSAGE_FIXTURE, later])
        result = adapter.collect_session_usage(
            session_id="qoder-session",
            cwd="C:/repo",
            model=MODEL,
        )

        self.assertEqual(len(result.facts), 1)
        usage = result.facts[0].to_dict()["usage"]
        self.assertEqual(usage["credits"], 1.178684375)
        self.assertEqual(usage["cache_read_tokens"], 17)
        self.assertEqual(usage["cache_write_tokens"], 3)

    def test_explicit_zero_is_preserved_while_missing_credit_stays_none(self) -> None:
        explicit_zero = {
            "type": "assistant",
            "model": MODEL,
            "usage": {
                "request_id": "req-zero",
                "input_tokens": 0,
                "output_tokens": 0,
                "credits": 0,
            },
        }
        missing_credit = {
            "type": "assistant",
            "model": MODEL,
            "usage": {
                "request_id": "req-missing",
                "input_tokens": 0,
                "output_tokens": 0,
            },
        }
        adapter, _sdk = self._adapter([explicit_zero, missing_credit])
        result = adapter.collect_session_usage(
            session_id="qoder-session",
            cwd="C:/repo",
            model=MODEL,
        )
        by_id = {fact.to_dict()["request_id"]: fact.to_dict()["usage"] for fact in result.facts}
        self.assertEqual(by_id["req-zero"]["credits"], 0.0)
        self.assertIsNone(by_id["req-missing"]["credits"])
        # Missing fields stay missing even when other explicit zero values
        # would make a derived zero arithmetically possible.
        self.assertIsNone(by_id["req-zero"]["total_tokens"])
        self.assertIsNone(by_id["req-missing"]["total_tokens"])

    def test_sdk_unavailable_returns_provider_omitted_without_fabricating_usage(self) -> None:
        adapter_cls = _adapter_type(self)
        adapter = adapter_cls(cli_path=REAL_CLI_PATH, sdk_loader=lambda: None)
        result = adapter.collect_session_usage(
            session_id="qoder-session",
            cwd="C:/repo",
            model=MODEL,
        )
        self.assertEqual(result.status, "provider_omitted")
        self.assertEqual(result.facts, ())

    def test_adapter_imports_existing_session_without_dispatching_second_agent_task(self) -> None:
        adapter, sdk = self._adapter([ASSISTANT_MESSAGE_FIXTURE, RESULT_MESSAGE_FIXTURE])
        result = adapter.collect_session_usage(
            session_id="qoder-session",
            cwd="C:/repo",
            model=MODEL,
        )
        self.assertEqual(result.status, "observed")
        self.assertEqual(len(sdk.import_calls), 1)
        self.assertEqual(sdk.import_calls[0]["session_id"], "qoder-session")
        self.assertFalse(sdk.import_calls[0]["include_subagents"])
        self.assertFalse(hasattr(sdk, "query"))

    def test_account_quota_fixture_is_separate_from_task_usage_and_not_exhausted(self) -> None:
        adapter_cls = _adapter_type(self)
        normalized = adapter_cls.normalize_account_usage(ACCOUNT_USAGE_FIXTURE)
        self.assertEqual(normalized["user_quota"]["total"], 300)
        self.assertEqual(normalized["user_quota"]["used"], 213)
        self.assertEqual(normalized["user_quota"]["remaining"], 87)
        self.assertEqual(normalized["user_quota"]["unit"], "credits")
        self.assertIs(normalized["is_quota_exceeded"], False)
        self.assertNotIn("credits", normalized)
        self.assertNotIn("session_credits", normalized)


class QoderSdkUsageAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "runtime.db")
        self.db.initialize()
        self.service = TaskService(self.db)
        task = Task(
            task_id="qoder-sdk-usage",
            task_type="qoder",
            status="queued",
            route=TaskRoute.ACP.value,
            created_at=1.0,
            updated_at=1.0,
        )
        session = Session(
            session_id="rs-qoder-sdk-usage",
            task_id=task.task_id,
            backend="qoder",
            route=TaskRoute.ACP.value,
            created_at=1.0,
            updated_at=1.0,
        )
        self.service.create_task(
            task=task,
            session=session,
            metadata={},
            idempotency_key="",
            request_fingerprint="fp-qoder-sdk-usage",
            now=1.0,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_session_credit_fact_does_not_erase_explicit_acp_token_snapshot(self) -> None:
        request = BackendStartRequest(
            task_id="qoder-sdk-usage", attempt_id="at", runtime_session_id="rs",
            prompt="fixture only", cwd=str(self.root), model=MODEL,
        )
        acp_fact = QoderBackend._usage_fact(
            request,
            ACP_ZERO_USAGE_FIXTURE["usage"],
            source="qoder_acp_terminal_snapshot",
            accounting="snapshot",
        )
        self.assertIsNotNone(acp_fact)
        self.service.append_usage_evidence("qoder-sdk-usage", usage=acp_fact.to_dict())

        adapter_cls = _adapter_type(self)
        sdk = FakeSdkModule([ASSISTANT_MESSAGE_FIXTURE, RESULT_MESSAGE_FIXTURE])
        collection = adapter_cls(cli_path=REAL_CLI_PATH, sdk_module=sdk).collect_session_usage(
            session_id="qoder-session",
            cwd=str(self.root),
            model=MODEL,
        )
        for fact in collection.facts:
            self.service.append_usage_evidence("qoder-sdk-usage", usage=fact.to_dict())

        summary = self.service.latest_usage_evidence("qoder-sdk-usage")
        self.assertEqual(summary["usage"]["total_tokens"], 0)
        self.assertEqual(summary["usage"]["credits"], 1.178684375)
        self.assertEqual(summary["usage"]["session_credits"], 1.178684375)

    def test_request_credit_and_result_total_are_not_added_together(self) -> None:
        adapter_cls = _adapter_type(self)
        sdk = FakeSdkModule([ASSISTANT_MESSAGE_FIXTURE, RESULT_MESSAGE_FIXTURE])
        collection = adapter_cls(cli_path=REAL_CLI_PATH, sdk_module=sdk).collect_session_usage(
            session_id="qoder-session",
            cwd=str(self.root),
            model=MODEL,
        )
        for fact in collection.facts:
            self.service.append_usage_evidence("qoder-sdk-usage", usage=fact.to_dict())

        summary = self.service.latest_usage_evidence("qoder-sdk-usage")
        self.assertEqual(summary["usage"]["credits"], 1.178684375)
        self.assertEqual(summary["usage"]["session_credits"], 1.178684375)
        self.assertNotEqual(summary["usage"]["credits"], 2.35736875)
        self.assertEqual(summary["request_id"], REQUEST_ID)


class QoderSdkUsageBackendIntegrationTests(unittest.TestCase):
    def test_acp_without_usage_update_uses_sdk_usage_and_reuses_config_resolved_cli_path(self) -> None:
        adapter_cls = _adapter_type(self)
        sdk = FakeSdkModule([ASSISTANT_MESSAGE_FIXTURE, RESULT_MESSAGE_FIXTURE])
        captured_adapter_kwargs = []
        emitted: list[BackendUsage] = []

        class FakeClient:
            def __init__(self, *, cwd, on_activity, **_kwargs) -> None:
                self.cwd = Path(cwd)
                self.cli_path = REAL_CLI_PATH
                self.on_activity = on_activity
                self.process = type("P", (), {"poll": lambda self: 0})()

            def run(self, **kwargs):
                kwargs["on_dispatch_accepted"]("qoder-session")
                return AcpRunResult(
                    session_id="qoder-session",
                    stop_reason="end_turn",
                    answer="done",
                    observability={
                        "route": "acp",
                        "usage_provenance": {
                            "status": "provider_omitted",
                            "request_identity": "none",
                            "event_count": 0,
                            "events": [],
                        },
                        "acp_prompt_result_fixture": ACP_ZERO_USAGE_FIXTURE,
                    },
                    usage=ACP_ZERO_USAGE_FIXTURE["usage"],
                    usage_samples=(),
                )

            def usage_samples(self):
                return []

            def usage_snapshot(self):
                return dict(ACP_ZERO_USAGE_FIXTURE["usage"])

            def cancel(self, _session_id="") -> None:
                return None

            def close(self) -> None:
                return None

        def sdk_adapter_factory(**kwargs):
            captured_adapter_kwargs.append(dict(kwargs))
            return adapter_cls(sdk_module=sdk, **kwargs)

        class Callbacks:
            def on_dispatch_accepted(self, _session_id: str) -> None:
                return None

            def on_activity(self, _activity) -> None:
                return None

            def on_usage(self, usage: BackendUsage) -> None:
                emitted.append(usage)

            def on_result(self, _result) -> None:
                return None

        request = BackendStartRequest(
            task_id="q-sdk",
            attempt_id="at",
            runtime_session_id="rs",
            prompt="fixture only",
            cwd=str(Path.cwd()),
            model=MODEL,
            metadata={"route": "acp_patch", "patch_policy": {}},
        )
        backend = QoderBackend(
            patch_acp_client_factory=lambda **kwargs: FakeClient(**kwargs),
            sdk_usage_adapter_factory=sdk_adapter_factory,
        )
        result = backend.start(request, Callbacks())

        self.assertEqual(result.answer, "done")
        self.assertEqual(captured_adapter_kwargs, [{"cli_path": REAL_CLI_PATH}])
        sdk_payloads = [fact.to_dict() for fact in emitted if fact.source.startswith("qoder_sdk_")]
        self.assertEqual(len(sdk_payloads), 2)
        request_usage = next(item for item in sdk_payloads if item["source"] == "qoder_sdk_assistant_usage")
        session_usage = next(item for item in sdk_payloads if item["source"] == "qoder_sdk_result")
        self.assertEqual(request_usage["usage"]["credits"], 1.178684375)
        self.assertEqual(session_usage["usage"]["session_credits"], 1.178684375)
        self.assertEqual(result.observability["usage_provenance"]["status"], "observed")
        self.assertEqual(result.observability["usage_provenance"]["acp_status"], "provider_omitted")


if __name__ == "__main__":
    unittest.main()
