from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime.application.crew import CrewProvider, CrewRegistryService
from agent_runtime.application.dispatch import CaptainDispatchService
from agent_runtime.application.voyage import VoyageOverviewService
from agent_runtime.domain.crew import CrewDescriptor
from agent_runtime.domain.dispatch import CaptainDispatchRequest, PatchPolicy
from agent_runtime.domain.task import Task


class _Tasks:
    def __init__(self, tasks):
        self._tasks = list(tasks)

    def list_tasks(self):
        return list(self._tasks)


def _descriptor(name: str, *, ready: bool, caps=()) -> CrewDescriptor:
    return CrewDescriptor(
        backend=name,
        display_name=name.title(),
        maturity="official",
        official_sources=(f"https://example.invalid/{name}",),
        capabilities=tuple(caps),
        controlled_capabilities=tuple(caps) if ready else (),
        documented_routes=("sdk",),
        implemented_routes=("sdk",) if ready else (),
        dispatch_ready=ready,
    )


class CaptainBoundaryTests(unittest.TestCase):
    def test_overview_is_bounded_content_free_and_surfaces_attention(self) -> None:
        tasks = [
            Task("run", "qoder", "running", "acp", 1, 7, started_at=2),
            Task("done", "qoder", "completed", "acp", 1, 6, started_at=2, finished_at=6, result_available=True, result_json='{"answer":"secret"}'),
            Task("lost", "qoder", "lost", "acp", 1, 5, started_at=2, finished_at=5),
        ]
        result = VoyageOverviewService(_Tasks(tasks)).overview(limit=1)
        self.assertEqual(result["schema"], "tp-voyager.overview/v1")
        self.assertEqual(result["active_count"], 1)
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["attention_count"], 1)
        self.assertTrue(result["captain_action_required"])
        self.assertEqual(len(result["active"]), 1)
        self.assertEqual(len(result["recent_completed"]), 1)
        self.assertNotIn("secret", str(result))
        self.assertFalse(result["content_included"])

    def test_overview_keeps_legacy_task_visible_without_promoting_it_to_target_crew(self) -> None:
        task = Task("legacy", "workbuddy", "running", "gateway", 1, 2)
        item = VoyageOverviewService(_Tasks([task])).overview()["active"][0]
        self.assertEqual(item["crew"], "workbuddy")
        self.assertFalse(item["target_crew"])
        self.assertTrue(item["legacy_or_unknown_crew"])

    def test_dispatch_explicitly_rejects_workbuddy(self) -> None:
        service = CaptainDispatchService(CrewRegistryService({}))
        result = service.dispatch(CaptainDispatchRequest("inspect", "workbuddy", "research"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "CREW_NOT_SUPPORTED")
        self.assertFalse(result["dispatch_performed"])

    def test_dispatch_fails_closed_when_official_transport_is_not_controlled_ready(self) -> None:
        called = []
        caps = ("analyze_context", "read_files", "search_code")
        registry = CrewRegistryService({"qoder": CrewProvider(_descriptor("qoder", ready=False, caps=caps))})
        service = CaptainDispatchService(registry, {"qoder": lambda request: called.append(request) or {"ok": True}})
        result = service.dispatch(CaptainDispatchRequest("inspect", "qoder", "research"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "CREW_NOT_CONTROLLED_READY")
        self.assertEqual(called, [])

    def test_patch_mode_requires_explicit_policy_and_does_not_widen_scope(self) -> None:
        caps = ("analyze_context", "read_files", "search_code", "edit_files", "run_commands")
        registry = CrewRegistryService({"qoder": CrewProvider(_descriptor("qoder", ready=True, caps=caps))})
        calls = []
        service = CaptainDispatchService(
            registry, {"qoder": lambda request: calls.append(request) or {"ok": True, "task_id": "x"}}
        )
        missing = service.dispatch(
            CaptainDispatchRequest("fix", "qoder", "small_patch", access_mode="patch")
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["reason_code"], "PATCH_POLICY_REQUIRED")
        self.assertEqual(calls, [])

        no_verification = service.dispatch(
            CaptainDispatchRequest(
                "fix",
                "qoder",
                "small_patch",
                access_mode="patch",
                patch_policy=PatchPolicy.from_dict({"allowed_paths": ["src"]}),
            )
        )
        self.assertFalse(no_verification["ok"])
        self.assertEqual(no_verification["reason_code"], "VERIFICATION_COMMAND_REQUIRED")
        self.assertEqual(calls, [])

        policy = PatchPolicy.from_dict(
            {
                "allowed_paths": ["src"],
                "commands": [{"id": "verify", "argv": ["python", "-V"]}],
                "verification_command_ids": ["verify"],
            }
        )
        accepted = service.dispatch(
            CaptainDispatchRequest(
                "fix", "qoder", "small_patch", access_mode="patch", patch_policy=policy
            )
        )
        self.assertTrue(accepted["ok"])
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0].patch_policy, policy)


    def test_high_level_dispatch_can_auto_create_codebuddy_context_manifest(self) -> None:
        from agent_runtime import server

        captured = []

        class _Contexts:
            def register(self, cwd, files):
                self.cwd = cwd
                self.files = list(files)
                return SimpleNamespace(manifest={"context_id": "ctx-auto"})

        class _Dispatch:
            def dispatch(self, request):
                captured.append(request)
                return {"ok": True, "task_id": "task-auto"}

        contexts = _Contexts()
        with patch("agent_runtime.server._context_service", return_value=contexts), patch(
            "agent_runtime.server._captain_dispatch_service", return_value=_Dispatch()
        ):
            result = server.task_dispatch(
                objective="inspect bounded files",
                crew="codebuddy",
                task_kind="research",
                cwd="C:/repo",
                context_files=["README.md", "src/a.py"],
                timeout_seconds=600,
            )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["context_auto_created"])
        self.assertEqual(result["context_id"], "ctx-auto")
        self.assertEqual(contexts.files, ["README.md", "src/a.py"])
        self.assertEqual(captured[0].context_id, "ctx-auto")
        self.assertEqual(captured[0].timeout_seconds, 600)

    def test_high_level_dispatch_rejects_ambiguous_or_irrelevant_context_files(self) -> None:
        from agent_runtime import server

        ambiguous = server.task_dispatch(
            objective="inspect", crew="codebuddy", task_kind="research", cwd=".",
            context_id="existing", context_files=["README.md"],
        )
        self.assertFalse(ambiguous["ok"])
        self.assertEqual(ambiguous["reason_code"], "INVALID_CONTEXT_REQUEST")

        qoder = server.task_dispatch(
            objective="inspect", crew="qoder", task_kind="research", cwd=".",
            context_files=["README.md"],
        )
        self.assertFalse(qoder["ok"])
        self.assertEqual(qoder["reason_code"], "CONTEXT_FILES_NOT_APPLICABLE")

    def test_controlled_ready_synthetic_dispatch_does_not_auto_select_or_fallback(self) -> None:
        caps = ("analyze_context", "read_files", "search_code")
        registry = CrewRegistryService({"qoder": CrewProvider(_descriptor("qoder", ready=True, caps=caps))})
        calls = []

        def dispatch(request):
            calls.append(request)
            return {"ok": True, "task_id": "task-1"}

        result = CaptainDispatchService(registry, {"qoder": dispatch}).dispatch(
            CaptainDispatchRequest("inspect", "qoder", "research")
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["crew"], "qoder")
        self.assertEqual(result["task_id"], "task-1")
        self.assertFalse(result["selection_performed"])
        self.assertTrue(result["dispatch_performed"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
