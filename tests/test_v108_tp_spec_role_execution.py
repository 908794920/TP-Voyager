from __future__ import annotations

import hashlib
import unittest

from agent_runtime.application.crew import CrewProvider, CrewRegistryService
from agent_runtime.application.dispatch import CaptainDispatchService
from agent_runtime.domain.crew import CrewDescriptor
from agent_runtime.domain.dispatch import (
    ApplyReceipt,
    CaptainDispatchRequest,
    InputArtifactRef,
    PatchPolicy,
    ReadScope,
    TrustedInstructionRef,
    VerificationPolicy,
    WorkerSkillRef,
)


READ_ONLY_ROLE_ACTIONS = {
    "requirement": ("research", "read_only"),
    "product": ("research", "read_only"),
    "architecture": ("research", "read_only"),
    "architecture_review": ("code_review", "read_only"),
    "debug": ("test_failure_triage", "read_only"),
    "independent_review": ("code_review", "read_only"),
    "security_review": ("code_review", "read_only"),
    "delivery_analysis": ("research", "read_only"),
}


def _descriptor(backend: str) -> CrewDescriptor:
    caps = (
        "analyze_context",
        "read_files",
        "search_code",
        "edit_files",
        "run_commands",
        "verify_commands",
    )
    return CrewDescriptor(
        backend=backend,
        display_name=backend.title(),
        maturity="official",
        official_sources=(f"https://example.invalid/{backend}",),
        capabilities=caps,
        controlled_capabilities=caps,
        documented_routes=("controlled",),
        implemented_routes=("controlled",),
        dispatch_ready=True,
    )


def _read_only_skill() -> WorkerSkillRef:
    return WorkerSkillRef.from_dict(
        {
            "name": "tp-spec-read-role",
            "version": "1",
            "sha256": "a" * 64,
            "allowed_models": ["qoder:lite"],
            "allowed_crews": ["qoder"],
            "allowed_task_kinds": ["research", "code_review", "test_failure_triage"],
            "allowed_access_modes": ["read_only"],
            "max_bytes": 4096,
            "artifact_consumer": True,
        }
    )


def _apply_receipt() -> ApplyReceipt:
    return ApplyReceipt.from_dict(
        {
            "schema": "tp-voyager.apply_receipt/v1",
            "repository_identity": "repo",
            "base_commit": "abc",
            "base_tree_hash": "tree",
            "patch_artifact_id": "art-patch",
            "patch_sha256": "1" * 64,
            "result_tree_hash": "result-tree",
            "changed_files": ["src/a.py"],
            "applied_by": "captain_host",
            "applied_at": "2026-08-19T00:00:00+08:00",
            "git_status_digest": "2" * 64,
            "conflicts": [],
            "receipt_sha256": "3" * 64,
        }
    )


class V108TpSpecRoleExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[CaptainDispatchRequest] = []
        self.counter = 0

        def dispatch(request: CaptainDispatchRequest):
            self.counter += 1
            self.calls.append(request)
            return {"ok": True, "task_id": f"task-{self.counter:02d}"}

        registry = CrewRegistryService(
            {
                "qoder": CrewProvider(_descriptor("qoder")),
                "codebuddy": CrewProvider(_descriptor("codebuddy")),
            }
        )
        self.service = CaptainDispatchService(
            registry,
            {"qoder": dispatch, "codebuddy": dispatch},
            artifact_loader=lambda refs: tuple(
                f"artifact-content:{item.artifact_id}" for item in refs
            ),
        )

    def test_all_read_only_role_actions_dispatch_without_read_scope_and_with_role_skill(self) -> None:
        skill = _read_only_skill()
        for role_action, (task_kind, access_mode) in READ_ONLY_ROLE_ACTIONS.items():
            with self.subTest(role_action=role_action):
                before = len(self.calls)
                result = self.service.dispatch(
                    CaptainDispatchRequest(
                        objective=f"perform {role_action}",
                        crew="qoder",
                        task_kind=task_kind,
                        model="lite",
                        access_mode=access_mode,
                        cwd="C:/large-repo",
                        worker_skill_refs=(skill,),
                        worker_skill_content=(f"Role skill for {role_action}.",),
                    )
                )
                self.assertTrue(result["ok"], result)
                self.assertEqual(len(self.calls), before + 1)
                captured = self.calls[-1]
                self.assertIsNone(captured.read_scope)
                self.assertEqual(captured.resolved_read_files, ())
                self.assertEqual(captured.crew, "qoder")
                self.assertEqual(captured.model, "lite")
                self.assertIn("[Trusted Worker Skills]", captured.objective)
                self.assertIn(
                    "worker_skill_refs", captured.routing_metadata()
                )

    def test_development_stays_small_patch_with_explicit_patch_policy(self) -> None:
        missing = self.service.dispatch(
            CaptainDispatchRequest(
                objective="implement backend change",
                crew="qoder",
                task_kind="small_patch",
                model="lite",
                access_mode="patch",
                cwd="C:/repo",
            )
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["reason_code"], "PATCH_POLICY_REQUIRED")

        policy = PatchPolicy.from_dict(
            {
                "allowed_paths": ["src"],
                "commands": [
                    {"id": "verify", "argv": ["python", "-m", "pytest", "-q"]}
                ],
                "verification_command_ids": ["verify"],
            }
        )
        accepted = self.service.dispatch(
            CaptainDispatchRequest(
                objective="implement backend change",
                crew="qoder",
                task_kind="small_patch",
                model="lite",
                access_mode="patch",
                cwd="C:/repo",
                patch_policy=policy,
            )
        )
        self.assertTrue(accepted["ok"], accepted)
        self.assertIs(self.calls[-1].patch_policy, policy)

    def test_verification_keeps_broad_analysis_separate_from_deterministic_commands(self) -> None:
        broad = self.service.dispatch(
            CaptainDispatchRequest(
                objective="independently analyze regression risk",
                crew="qoder",
                task_kind="code_review",
                model="lite",
                access_mode="read_only",
                cwd="C:/repo",
            )
        )
        self.assertTrue(broad["ok"], broad)
        self.assertIsNone(self.calls[-1].read_scope)

        missing = self.service.dispatch(
            CaptainDispatchRequest(
                objective="run deterministic verification",
                crew="qoder",
                task_kind="verify_only",
                model="lite",
                access_mode="verification",
                cwd="C:/verification-worktree",
            )
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["reason_code"], "APPLY_RECEIPT_REQUIRED")

        scope = ReadScope.from_dict(
            {"files": ["src/a.py"], "max_files": 8, "max_bytes": 1024 * 1024}
        )
        policy = VerificationPolicy.from_dict(
            {
                "commands": [
                    {"id": "verify", "argv": ["python", "-m", "pytest", "-q"]}
                ],
                "timeout_seconds": 900,
            }
        )
        accepted = self.service.dispatch(
            CaptainDispatchRequest(
                objective="run deterministic verification",
                crew="qoder",
                task_kind="verify_only",
                model="lite",
                access_mode="verification",
                cwd="C:/verification-worktree",
                apply_receipt=_apply_receipt(),
                verification_policy=policy,
                read_scope=scope,
                resolved_read_files=("src/a.py",),
            )
        )
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(self.calls[-1].access_mode, "verification")
        self.assertIs(self.calls[-1].verification_policy, policy)

    def test_role_skill_trusted_instruction_and_artifact_handoff_remain_hash_pinned(self) -> None:
        skill = _read_only_skill()
        instruction = TrustedInstructionRef.from_dict(
            {
                "root_alias": "tp-spec",
                "path": "roles/architecture.md",
                "sha256": "b" * 64,
                "max_bytes": 4096,
            }
        )
        artifact_bytes = b"architecture evidence"
        artifact = InputArtifactRef.from_dict(
            {
                "artifact_id": "architecture-report",
                "source_task_id": "task-architecture",
                "kind": "technical_report",
                "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                "byte_size": len(artifact_bytes),
            }
        )
        result = self.service.dispatch(
            CaptainDispatchRequest(
                objective="review architecture handoff",
                crew="qoder",
                task_kind="code_review",
                model="lite",
                access_mode="read_only",
                cwd="C:/repo",
                worker_skill_refs=(skill,),
                worker_skill_content=("Review as architecture reviewer.",),
                trusted_instruction_refs=(instruction,),
                trusted_instruction_content=("Use TP-Spec architecture review rules.",),
                input_artifact_refs=(artifact,),
            )
        )
        self.assertTrue(result["ok"], result)
        captured = self.calls[-1]
        metadata = captured.routing_metadata()
        self.assertEqual(metadata["worker_skill_refs"][0]["sha256"], "a" * 64)
        self.assertEqual(metadata["trusted_instruction_refs"][0]["sha256"], "b" * 64)
        self.assertEqual(
            metadata["input_artifact_refs"][0]["artifact_id"],
            "architecture-report",
        )
        self.assertIn("[Trusted Captain Instructions]", captured.objective)
        self.assertIn("[Untrusted Input Artifacts]", captured.objective)
        self.assertIn("artifact-content:architecture-report", captured.objective)

    def test_ultraplan_and_ultrareview_are_separate_dispatches_plus_synthesis(self) -> None:
        groups = (
            (
                "UltraPlan",
                [
                    ("maintainability research", "research"),
                    ("performance research", "research"),
                    ("minimal-change research", "research"),
                    ("plan synthesis", "research"),
                ],
            ),
            (
                "UltraReview",
                [
                    ("correctness review", "code_review"),
                    ("security review", "code_review"),
                    ("regression review", "code_review"),
                    ("review synthesis", "research"),
                ],
            ),
        )
        for name, actions in groups:
            with self.subTest(name=name):
                task_ids = []
                before = len(self.calls)
                for objective, kind in actions:
                    result = self.service.dispatch(
                        CaptainDispatchRequest(
                            objective=objective,
                            crew="qoder",
                            task_kind=kind,
                            model="lite",
                            access_mode="read_only",
                            cwd="C:/repo",
                        )
                    )
                    self.assertTrue(result["ok"], result)
                    task_ids.append(result["task_id"])
                self.assertEqual(len(set(task_ids)), 4)
                dispatched = self.calls[before:]
                self.assertEqual(len(dispatched), 4)
                self.assertTrue(all(item.read_scope is None for item in dispatched))


if __name__ == "__main__":
    unittest.main()
