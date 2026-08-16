from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from agent_runtime.domain.crew import CrewDescriptor, ModelDescriptor
from agent_runtime.application.crew.service import CrewProvider, CrewRegistryService
from agent_runtime.application.crew.routing_profiles import ModelRoutingProfile, ModelRoutingProfiles

from agent_runtime.configuration import VoyagerUserConfig


class ModelRoutingProfilesTests(unittest.TestCase):
    def test_missing_operator_file_uses_read_only_bundled_baseline(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfiles

        profiles = ModelRoutingProfiles.load(Path(self._tmpdir()) / "runtime")
        self.assertEqual(profiles.status, "bundled_baseline")
        self.assertEqual(profiles.metadata()["source"], "bundled_model_routing_baseline")
        self.assertEqual(profiles.profile_count, 26)
        self.assertIsNotNone(profiles.sha256)
        deepseek = profiles.get("codebuddy:deepseek-v4-flash")
        self.assertEqual(deepseek["capability_tier"], "UNCLASSIFIED")
        self.assertEqual(deepseek["legacy_capability_tier"], "L3")
        self.assertEqual(deepseek["tier_authority"], "standard_v1")
        qwen = profiles.get("qoder:qmodel_38max")
        self.assertEqual(qwen["capability_tier"], "UNCLASSIFIED")
        self.assertEqual(qwen["legacy_capability_tier"], "L3")
        self.assertEqual(qwen["tier_authority"], "standard_v1")

    def test_valid_profile_is_strictly_loaded_and_projected(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfiles

        root = Path(self._tmpdir())
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "tp-voyager.model_routing_profiles/v1",
            "updated_at": "2026-08-12",
            "profiles": {
                "codebuddy:deepseek-v4-flash": {
                    "canonical_family": "deepseek-v4-flash-0731",
                    "capability_tier": "L2",
                    "recommended_tasks": ["implementation", "debugging"],
                    "risk_boundaries": ["architecture decisions require Captain review"],
                    "suggested_effort": "high",
                    "evidence_sources": ["https://api-docs.deepseek.com/quick_start/pricing/"],
                }
            },
        }
        (root / "model_routing_profiles.json").write_text(json.dumps(payload), encoding="utf-8")
        profiles = ModelRoutingProfiles.load(root)
        profile = profiles.get("codebuddy:deepseek-v4-flash")
        self.assertEqual(profiles.status, "loaded")
        self.assertEqual(profiles.profile_count, 1)
        self.assertIsNotNone(profiles.sha256)
        self.assertEqual(profile["capability_tier"], "UNCLASSIFIED")
        self.assertEqual(profile["legacy_capability_tier"], "L2")
        self.assertEqual(profile["tier_authority"], "standard_v1_uncalibrated")
        self.assertIsNone(profile["scorecard"])
        self.assertEqual(profile["suggested_effort"], "high")
        self.assertEqual(profile["recommended_tasks"], ["implementation", "debugging"])
        self.assertEqual(profiles.route_ids("codebuddy"), ("codebuddy:deepseek-v4-flash",))

    def test_duplicate_json_key_fails_closed(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfileError, ModelRoutingProfiles

        root = Path(self._tmpdir())
        root.mkdir(parents=True, exist_ok=True)
        (root / "model_routing_profiles.json").write_text(
            '{"schema":"tp-voyager.model_routing_profiles/v1","profiles":{"qoder:lite":{"capability_tier":"L0"},"qoder:lite":{"capability_tier":"L1"}}}',
            encoding="utf-8",
        )
        with self.assertRaises(ModelRoutingProfileError):
            ModelRoutingProfiles.load(root)

    def test_bundled_baseline_is_valid_and_covers_current_provider_route_ids(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfiles

        baseline = ModelRoutingProfiles.bundled_baseline_path()
        self.assertTrue(baseline.exists())
        root = Path(self._tmpdir())
        root.mkdir(parents=True, exist_ok=True)
        (root / "model_routing_profiles.json").write_bytes(baseline.read_bytes())
        profiles = ModelRoutingProfiles.load(root)
        expected = {
            "codebuddy:hy3", "codebuddy:glm-5.2", "codebuddy:glm-5.1",
            "codebuddy:glm-5v-turbo", "codebuddy:minimax-m3-pay", "codebuddy:minimax-m2.7",
            "codebuddy:kimi-k3-2", "codebuddy:kimi-k2.7", "codebuddy:kimi-k2.6",
            "codebuddy:deepseek-v4-pro", "codebuddy:deepseek-v4-flash", "codebuddy:glm-5.3",
            "qoder:ultimate", "qoder:performance", "qoder:efficient",
            "qoder:lite", "qoder:cmodel", "qoder:qmodel_38max", "qoder:qmodel_latest",
            "qoder:qmodel", "qoder:kmodel_latest", "qoder:kmodel", "qoder:gm51model",
            "qoder:dmodel", "qoder:dfmodel", "qoder:mmodel",
        }
        self.assertEqual(set(profiles.route_ids()), expected)
        lite = profiles.get("qoder:lite")
        self.assertEqual(lite["capability_tier"], "DYNAMIC")
        self.assertEqual(lite["provider_tier_label"], "Lite")
        self.assertEqual(lite["tier_authority"], "provider_dynamic")
        self.assertIsNone(lite["scorecard"])
        hy3 = profiles.get("codebuddy:hy3")
        self.assertEqual(hy3["capability_tier"], "UNCLASSIFIED")
        self.assertEqual(hy3["legacy_capability_tier"], "L1")
        deepseek = profiles.get("codebuddy:deepseek-v4-flash")
        self.assertEqual(deepseek["canonical_family"], "deepseek-v4-flash")
        self.assertEqual(deepseek["provider_identity"], "operator_confirmed")
        self.assertEqual(deepseek["capability_tier"], "UNCLASSIFIED")
        self.assertEqual(deepseek["legacy_capability_tier"], "L3")
        self.assertEqual(deepseek["profile_confidence"], "high")
        qwen = profiles.get("qoder:qmodel_38max")
        self.assertEqual(qwen["canonical_family"], "qwen3.8-max")
        self.assertEqual(qwen["provider_identity"], "operator_confirmed")
        self.assertEqual(qwen["capability_tier"], "UNCLASSIFIED")
        self.assertEqual(qwen["legacy_capability_tier"], "L3")
        self.assertEqual(qwen["profile_confidence"], "medium-high")
        self.assertEqual(profiles.get("qoder:cmodel")["capability_tier"], "UNCLASSIFIED")

    def test_repository_example_is_small_valid_and_covers_four_core_routes(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfiles

        example = Path(__file__).resolve().parents[1] / "docs" / "examples" / "model_routing_profiles.example.json"
        root = Path(self._tmpdir())
        root.mkdir(parents=True, exist_ok=True)
        (root / "model_routing_profiles.json").write_bytes(example.read_bytes())
        profiles = ModelRoutingProfiles.load(root)
        self.assertEqual(set(profiles.route_ids()), {
            "qoder:lite", "codebuddy:hy3", "codebuddy:deepseek-v4-flash", "qoder:qmodel_38max"
        })

    def test_initialize_installs_bundled_baseline_without_overwriting_operator_file(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfileError, ModelRoutingProfiles

        root = Path(self._tmpdir()) / "runtime"
        result = ModelRoutingProfiles.initialize(root)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["profile_count"], 26)
        self.assertTrue((root / "model_routing_profiles.json").is_file())
        self.assertIn("operator_model_research", result["required_evidence_root_aliases"])
        with self.assertRaises(ModelRoutingProfileError):
            ModelRoutingProfiles.initialize(root)

    def test_trusted_local_evidence_is_verified_by_alias_relative_path_and_hash(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfiles

        root = Path(self._tmpdir())
        evidence_root = root / "research"
        evidence_root.mkdir(parents=True)
        evidence = evidence_root / "models.md"
        evidence.write_text("operator model research\n", encoding="utf-8")
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        config = VoyagerUserConfig.defaults(root).to_dict()
        config["trusted_roots"]["model_evidence"] = {"operator_model_research": str(evidence_root.resolve())}
        (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        payload = {
            "schema": "tp-voyager.model_routing_profiles/v1",
            "profiles": {
                "codebuddy:hy3": {
                    "canonical_family": "hy3",
                    "capability_tier": "L1",
                    "profile_confidence": "medium-high",
                    "specialties": ["routine_coding"],
                    "benchmark_evidence": [{
                        "source": "artificial_analysis",
                        "tested_model": "Hy3",
                        "model_match": "exact",
                        "metrics": {"intelligence_index": 41},
                        "url": "https://artificialanalysis.ai/models/hy3"
                    }],
                    "evidence_refs": [{
                        "kind": "trusted_file",
                        "root_alias": "operator_model_research",
                        "path": "models.md",
                        "sha256": digest
                    }]
                }
            }
        }
        (root / "model_routing_profiles.json").write_text(json.dumps(payload), encoding="utf-8")
        profiles = ModelRoutingProfiles.load(root)
        profile = profiles.get("codebuddy:hy3")
        self.assertEqual(profile["profile_confidence"], "medium-high")
        self.assertEqual(profile["benchmark_evidence"][0]["metrics"]["intelligence_index"], 41)
        self.assertEqual(profile["evidence_status"], "verified")
        ref = profile["evidence_refs"][0]
        self.assertEqual(ref["verification"], "verified")
        self.assertEqual(ref["actual_sha256"], digest)
        self.assertNotIn(str(evidence_root.resolve()), json.dumps(profile, ensure_ascii=False))

    def test_evidence_hash_mismatch_marks_profile_stale_without_rejecting_profile(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfiles

        root = Path(self._tmpdir())
        evidence_root = root / "research"
        evidence_root.mkdir(parents=True)
        (evidence_root / "models.md").write_text("new content", encoding="utf-8")
        config = VoyagerUserConfig.defaults(root).to_dict()
        config["trusted_roots"]["model_evidence"] = {"operator_model_research": str(evidence_root.resolve())}
        (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        payload = {
            "schema": "tp-voyager.model_routing_profiles/v1",
            "profiles": {
                "qoder:qmodel_38max": {
                    "capability_tier": "L3",
                    "evidence_refs": [{
                        "kind": "trusted_file",
                        "root_alias": "operator_model_research",
                        "path": "models.md",
                        "sha256": "0" * 64
                    }]
                }
            }
        }
        (root / "model_routing_profiles.json").write_text(json.dumps(payload), encoding="utf-8")
        profile = ModelRoutingProfiles.load(root).get("qoder:qmodel_38max")
        self.assertEqual(profile["capability_tier"], "UNCLASSIFIED")
        self.assertEqual(profile["legacy_capability_tier"], "L3")
        self.assertEqual(profile["tier_authority"], "standard_v1_uncalibrated")
        self.assertEqual(profile["evidence_status"], "stale")
        self.assertEqual(profile["evidence_refs"][0]["verification"], "hash_mismatch")

    def test_trusted_file_evidence_rejects_unsafe_relative_paths(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfileError, ModelRoutingProfiles

        root = Path(self._tmpdir())
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "tp-voyager.model_routing_profiles/v1",
            "profiles": {
                "codebuddy:hy3": {
                    "evidence_refs": [{
                        "kind": "trusted_file", "root_alias": "operator_model_research",
                        "path": "../secret.md", "sha256": "0" * 64
                    }]
                }
            }
        }
        (root / "model_routing_profiles.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ModelRoutingProfileError):
            ModelRoutingProfiles.load(root)

    def test_unknown_profile_field_and_invalid_route_id_are_rejected(self) -> None:
        from agent_runtime.application.crew.routing_profiles import ModelRoutingProfileError, ModelRoutingProfiles

        root = Path(self._tmpdir())
        root.mkdir(parents=True, exist_ok=True)
        for name, payload in {
            "unknown": {
                "schema": "tp-voyager.model_routing_profiles/v1",
                "profiles": {"qoder:lite": {"capability_tier": "L0", "score": 10}},
            },
            "route": {
                "schema": "tp-voyager.model_routing_profiles/v1",
                "profiles": {"Qoder:lite": {"capability_tier": "L0"}},
            },
        }.items():
            with self.subTest(name=name):
                (root / "model_routing_profiles.json").write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ModelRoutingProfileError):
                    ModelRoutingProfiles.load(root)

    def _tmpdir(self) -> str:
        import tempfile
        path = tempfile.mkdtemp(prefix="tp-v106-")
        self.addCleanup(__import__("shutil").rmtree, path, True)
        return path


def _crew_descriptor(name: str) -> CrewDescriptor:
    return CrewDescriptor(
        backend=name,
        display_name=name.title(),
        maturity="official",
        official_sources=(f"https://example.invalid/{name}",),
        capabilities=("analyze_context",),
        controlled_capabilities=("analyze_context",),
        dispatch_ready=True,
        model_discovery="provider_live",
    )


class RoutableModelCatalogTests(unittest.TestCase):
    def _profiles(self, *items: ModelRoutingProfile) -> ModelRoutingProfiles:
        return ModelRoutingProfiles(
            profiles=tuple(items),
            status="loaded",
            sha256="profiles123",
            updated_at="2026-08-12",
        )

    def test_provider_policy_profile_and_evidence_are_merged_without_selection(self) -> None:
        model = ModelDescriptor(
            "codebuddy",
            "deepseek-v4-flash",
            display_name="DeepSeek-V4-Flash",
            available=True,
            source="codebuddy_acp_account_live",
            observed_at=11.0,
            metadata={
                "catalog_status": "complete",
                "billing": {
                    "status": "reference_only",
                    "multiplier": 0.05,
                    "calculation_allowed": False,
                },
                "context_window_tokens": 1048576,
                "supported_efforts": ["low", "high", "max"],
            },
        )
        profiles = self._profiles(
            ModelRoutingProfile(
                route_id="codebuddy:deepseek-v4-flash",
                canonical_family="deepseek-v4-flash-0731",
                provider_identity="operator_confirmed",
                capability_tier="L3",
                profile_confidence="high",
                specialties=("coding_agent",),
                recommended_tasks=("implementation", "debugging"),
                risk_boundaries=("architecture decisions require Captain review",),
                suggested_effort="high",
            )
        )
        service = CrewRegistryService(
            {"codebuddy": CrewProvider(_crew_descriptor("codebuddy"), models=lambda: [model])},
            model_policy_loader=lambda: SimpleNamespace(
                allowed_models=frozenset({"codebuddy:deepseek-v4-flash"}),
                sha256="policy123",
            ),
            routing_profiles_loader=lambda: profiles,
        )
        result = service.model_catalog("codebuddy")
        self.assertEqual(result["schema"], "tp-voyager.model_catalog/v2")
        self.assertFalse(result["catalog"]["selection_performed"])
        route = result["models"][0]
        self.assertEqual(route["route_id"], "codebuddy:deepseek-v4-flash")
        self.assertEqual(route["allowlist_status"], "allowed")
        self.assertTrue(route["routable"])
        self.assertEqual(route["routability_status"], "confirmed")
        self.assertEqual(route["reference_multiplier"], 0.05)
        self.assertFalse(route["calculation_allowed"])
        self.assertEqual(route["capability_profile"]["capability_tier"], "L3")
        self.assertEqual(route["capability_profile"]["profile_confidence"], "high")
        self.assertEqual(route["profile_evidence_status"], "not_declared")
        self.assertEqual(route["reasoning"]["supported_efforts"], ["low", "high", "max"])
        self.assertEqual(route["reasoning"]["suggested_effort"], "high")
        self.assertTrue(route["reasoning"]["suggested_effort_supported"])
        self.assertEqual(route["sources"]["authorization"], "operator_dispatch_policy")
        self.assertEqual(route["sources"]["capability_profile"], "operator_model_routing_profiles")
        self.assertFalse(result["selection_performed"])
        self.assertFalse(result["dispatch_performed"])

    def test_qoder_live_context_and_thinking_config_are_normalized(self) -> None:
        model = ModelDescriptor(
            "qoder",
            "qmodel_38max",
            available=True,
            source="official_dynamic_sdk",
            metadata={
                "catalog_status": "complete",
                "context_config": {
                    "200K": {"token_count": 200000, "is_default": True},
                    "1M": {"token_count": 1000000},
                },
                "thinking_config": {
                    "enabled": {
                        "efforts": {
                            "low": {}, "medium": {"is_default": True}, "xhigh": {}
                        }
                    }
                },
            },
        )
        service = CrewRegistryService(
            {"qoder": CrewProvider(_crew_descriptor("qoder"), models=lambda: [model])},
            model_policy_loader=lambda: SimpleNamespace(allowed_models=None, sha256="baseline"),
            routing_profiles_loader=lambda: ModelRoutingProfiles(),
        )
        route = service.model_catalog("qoder")["models"][0]
        self.assertEqual(route["context_window_tokens"], 1000000)
        self.assertEqual(route["reasoning"]["supported_efforts"], ["low", "medium", "xhigh"])

    def test_reference_multiplier_never_enables_cost_calculation(self) -> None:
        model = ModelDescriptor(
            "codebuddy", "hy3", available=True,
            metadata={
                "catalog_status": "complete",
                "billing": {"multiplier": 0.0, "calculation_allowed": True},
            },
        )
        service = CrewRegistryService(
            {"codebuddy": CrewProvider(_crew_descriptor("codebuddy"), models=lambda: [model])},
            model_policy_loader=lambda: SimpleNamespace(allowed_models=None, sha256="baseline"),
            routing_profiles_loader=lambda: ModelRoutingProfiles(),
        )
        route = service.model_catalog("codebuddy")["models"][0]
        self.assertEqual(route["reference_multiplier"], 0.0)
        self.assertFalse(route["calculation_allowed"])
        self.assertFalse(route["metadata"]["billing"]["calculation_allowed"])

    def test_explicit_empty_supported_efforts_marks_profile_suggestion_unsupported(self) -> None:
        model = ModelDescriptor(
            "codebuddy", "deepseek-v4-flash", available=True,
            metadata={
                "catalog_status": "complete",
                "supported_efforts": [],
                "effort_support_status": "unsupported_by_controlled_backend",
            },
        )
        profiles = self._profiles(ModelRoutingProfile(
            route_id="codebuddy:deepseek-v4-flash", capability_tier="L3", suggested_effort="high"
        ))
        service = CrewRegistryService(
            {"codebuddy": CrewProvider(_crew_descriptor("codebuddy"), models=lambda: [model])},
            model_policy_loader=lambda: SimpleNamespace(allowed_models=None, sha256="baseline"),
            routing_profiles_loader=lambda: profiles,
        )
        route = service.model_catalog("codebuddy")["models"][0]
        self.assertEqual(route["reasoning"]["supported_efforts"], [])
        self.assertEqual(route["reasoning"]["support_status"], "known")
        self.assertFalse(route["reasoning"]["suggested_effort_supported"])

    def test_explicit_policy_denial_keeps_model_visible_but_not_routable(self) -> None:
        model = ModelDescriptor(
            "qoder", "dmodel", available=True, source="official_dynamic_sdk",
            metadata={"catalog_status": "complete"},
        )
        service = CrewRegistryService(
            {"qoder": CrewProvider(_crew_descriptor("qoder"), models=lambda: [model])},
            model_policy_loader=lambda: SimpleNamespace(
                allowed_models=frozenset({"qoder:lite"}), sha256="policy123"
            ),
            routing_profiles_loader=lambda: ModelRoutingProfiles(),
        )
        route = service.model_catalog("qoder")["models"][0]
        self.assertEqual(route["allowlist_status"], "denied")
        self.assertFalse(route["routable"])
        self.assertEqual(route["routability_status"], "denied_by_policy")

    def test_policy_or_profile_only_route_remains_visible_with_unknown_availability(self) -> None:
        profiles = self._profiles(
            ModelRoutingProfile(
                route_id="qoder:qmodel_38max",
                canonical_family="qwen3.8-max",
                capability_tier="L3",
                recommended_tasks=("architecture",),
                suggested_effort="medium",
            )
        )
        service = CrewRegistryService(
            {"qoder": CrewProvider(_crew_descriptor("qoder"), models=lambda: [])},
            model_policy_loader=lambda: SimpleNamespace(
                allowed_models=frozenset({"qoder:qmodel_38max"}), sha256="policy123"
            ),
            routing_profiles_loader=lambda: profiles,
        )
        result = service.model_catalog("qoder")
        route = result["models"][0]
        self.assertEqual(route["route_id"], "qoder:qmodel_38max")
        self.assertIsNone(route["available"])
        self.assertEqual(route["allowlist_status"], "allowed")
        self.assertIsNone(route["routable"])
        self.assertEqual(route["routability_status"], "availability_unconfirmed")
        self.assertEqual(result["catalog"]["projected_model_count"], 1)

    def test_backend_not_dispatch_ready_is_not_routable(self) -> None:
        descriptor = _crew_descriptor("qoder")
        descriptor = CrewDescriptor(
            backend=descriptor.backend,
            display_name=descriptor.display_name,
            maturity=descriptor.maturity,
            official_sources=descriptor.official_sources,
            capabilities=descriptor.capabilities,
            controlled_capabilities=descriptor.controlled_capabilities,
            dispatch_ready=False,
            model_discovery=descriptor.model_discovery,
        )
        model = ModelDescriptor(
            "qoder", "lite", available=True, source="official_dynamic_sdk",
            metadata={"catalog_status": "complete"},
        )
        service = CrewRegistryService(
            {"qoder": CrewProvider(descriptor, models=lambda: [model])},
            model_policy_loader=lambda: SimpleNamespace(allowed_models=None, sha256="baseline"),
            routing_profiles_loader=lambda: ModelRoutingProfiles(),
        )
        route = service.model_catalog("qoder")["models"][0]
        self.assertFalse(route["routable"])
        self.assertEqual(route["routability_status"], "crew_not_dispatch_ready")


    def test_invalid_dispatch_policy_is_fail_closed_for_routability(self) -> None:
        def invalid_policy():
            raise ValueError("invalid operator policy")

        service = CrewRegistryService(
            {
                "qoder": CrewProvider(
                    _crew_descriptor("qoder"),
                    models=lambda: [ModelDescriptor("qoder", "lite", available=True, metadata={"catalog_status": "complete"})],
                )
            },
            model_policy_loader=invalid_policy,
            routing_profiles_loader=lambda: ModelRoutingProfiles(),
        )
        result = service.model_catalog("qoder")
        route = result["models"][0]
        self.assertEqual(result["catalog"]["authorization"]["status"], "invalid")
        self.assertEqual(route["allowlist_status"], "policy_invalid")
        self.assertFalse(route["routable"])
        self.assertEqual(route["routability_status"], "policy_invalid")

    def test_invalid_advisory_profile_does_not_override_live_policy_facts(self) -> None:
        def invalid_profiles():
            raise ValueError("invalid advisory metadata")

        service = CrewRegistryService(
            {
                "qoder": CrewProvider(
                    _crew_descriptor("qoder"),
                    models=lambda: [ModelDescriptor("qoder", "lite", available=True, metadata={"catalog_status": "complete"})],
                )
            },
            model_policy_loader=lambda: SimpleNamespace(allowed_models=None, sha256="builtin-safe-baseline"),
            routing_profiles_loader=invalid_profiles,
        )
        result = service.model_catalog("qoder")
        route = result["models"][0]
        self.assertEqual(result["catalog"]["routing_profiles"]["status"], "invalid")
        self.assertEqual(route["allowlist_status"], "unrestricted")
        self.assertTrue(route["routable"])
        self.assertIsNone(route["capability_profile"])
        self.assertEqual(route["sources"]["capability_profile"], "unavailable")


if __name__ == "__main__":
    unittest.main()
