from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from agent_runtime.application.crew import CrewProvider, CrewRegistryService
from agent_runtime.application.dispatch.policy import GlobalDispatchModelPolicy
from agent_runtime.application.dispatch.profiles import WorkerProfileError, WorkerSkillResolver
from agent_runtime.application.dispatch.service import CaptainDispatchService
from agent_runtime.domain.crew import CrewDescriptor
from agent_runtime.domain.dispatch import CaptainDispatchRequest, WorkerSkillRef


class WorkerSkillTests(unittest.TestCase):
    def test_hash_pinned_skill_is_transient_and_constrains_model(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"review"/"1.md"; path.parent.mkdir(); path.write_text("Review only the assigned files.", encoding="utf-8")
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            ref=WorkerSkillRef.from_dict({
                "name":"review", "version":"1", "sha256":digest,
                "allowed_models":["codebuddy:hy3"], "allowed_crews":["codebuddy"],
                "allowed_task_kinds":["research"], "allowed_access_modes":["read_only"],
                "max_bytes":4096, "artifact_consumer":False,
            })
            resolved=WorkerSkillResolver(root).resolve(ref)
        descriptor=CrewDescriptor(
            backend="codebuddy", display_name="CodeBuddy", maturity="official",
            official_sources=("https://example.invalid",), capabilities=("analyze_context",),
            controlled_capabilities=("analyze_context",), documented_routes=("sdk",), implemented_routes=("sdk",), dispatch_ready=True,
        )
        captured=[]
        service=CaptainDispatchService(
            CrewRegistryService({"codebuddy":CrewProvider(descriptor)}),
            dispatchers={"codebuddy":lambda request: captured.append(request) or {"ok":True}},
            global_model_policy=GlobalDispatchModelPolicy(),
        )
        result=service.dispatch(CaptainDispatchRequest(
            objective="Inspect", crew="codebuddy", task_kind="research", model="hy3",
            worker_skill_refs=(ref,), worker_skill_content=(resolved.content,),
        ))
        self.assertTrue(result["ok"], result)
        self.assertIn("[Trusted Worker Skills]", captured[0].objective)
        self.assertNotIn("Review only", str(captured[0].routing_metadata()))
        rejected=service.dispatch(CaptainDispatchRequest(
            objective="Inspect", crew="codebuddy", task_kind="research", model="kimi",
            worker_skill_refs=(ref,), worker_skill_content=(resolved.content,),
        ))
        self.assertFalse(rejected["ok"])

    def test_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"review"/"1.md"; path.parent.mkdir(); path.write_text("content", encoding="utf-8")
            ref=WorkerSkillRef.from_dict({
                "name":"review", "version":"1", "sha256":"a"*64,
                "allowed_crews":["codebuddy"], "allowed_task_kinds":["research"],
                "allowed_access_modes":["read_only"], "max_bytes":4096,
                "artifact_consumer":False,
            })
            with self.assertRaises(WorkerProfileError): WorkerSkillResolver(root).resolve(ref)

    def test_manifest_constraints_fail_closed(self):
        with self.assertRaises(ValueError):
            WorkerSkillRef.from_dict({"name":"review", "version":"1", "sha256":"a"*64})

    def test_manifest_size_is_enforced(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"review"/"1.md"; path.parent.mkdir(); path.write_text("12345", encoding="utf-8")
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            ref=WorkerSkillRef.from_dict({
                "name":"review", "version":"1", "sha256":digest,
                "allowed_crews":["codebuddy"], "allowed_task_kinds":["research"],
                "allowed_access_modes":["read_only"], "max_bytes":4,
                "artifact_consumer":False,
            })
            with self.assertRaises(WorkerProfileError):
                WorkerSkillResolver(root).resolve(ref)

if __name__ == "__main__": unittest.main()
