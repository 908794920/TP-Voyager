from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import pytest

from agent_runtime.application.crew.model_evaluation import (
    ModelEvaluationError,
    ModelEvaluationSourceRegistry,
    validate_standard_evidence,
)
from agent_runtime.application.crew.model_scorecard import (
    ModelScorecardError,
    build_scorecard,
    load_tier_rules,
)
from agent_runtime.application.crew.routing_profiles import (
    ModelRoutingProfileError,
    ModelRoutingProfiles,
)


class ModelEvaluationStandardTests(unittest.TestCase):
    def _home(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="tp-model-eval-"))
        self.addCleanup(__import__("shutil").rmtree, root, True)
        return root

    def _legacy_payload(self) -> dict:
        return {
            "schema": "tp-voyager.model_routing_profiles/v1",
            "updated_at": "2026-08-15",
            "profiles": {
                "codebuddy:glm-5.2": {
                    "canonical_family": "glm-5.2",
                    "provider_identity": "provider_declared",
                    "capability_tier": "L3",
                    "recommended_tasks": ["implementation"],
                    "risk_boundaries": ["review architecture changes"],
                    "suggested_effort": "high",
                    "benchmark_evidence": [{
                        "source": "artificial_analysis",
                        "release": "2026-06-25",
                        "tested_model": "GLM-5.2",
                        "model_match": "exact",
                        "metrics": {"intelligence_index": 51},
                        "url": "https://artificialanalysis.ai/models/glm-5-2",
                    }],
                },
                "qoder:ultimate": {
                    "canonical_family": "qoder-ultimate-tier",
                    "provider_identity": "dynamic_tier",
                    "capability_tier": "L3",
                },
                "qoder:auto": {
                    "canonical_family": "qoder-auto-tier",
                    "provider_identity": "dynamic_tier",
                    "capability_tier": "DYNAMIC",
                },
            },
        }

    def test_source_registry_bundled_is_strict_and_marks_archived_sources(self) -> None:
        registry = ModelEvaluationSourceRegistry.load_bundled()
        self.assertEqual(registry.schema, "tp-voyager.model_evaluation_sources/v1")
        self.assertEqual(registry.source("terminal_bench")["role"], "primary")
        self.assertEqual(registry.source("bigcodebench")["status"], "archived")
        self.assertIn("primary_approval", registry.source("terminal_bench")["requires"])

    def test_primary_evidence_requires_exact_identity_version_context_and_approval(self) -> None:
        registry = ModelEvaluationSourceRegistry.load_bundled()
        valid = {
            "evidence_schema": "tp-voyager.model_evidence/v1",
            "evidence_id": "tb21-kimi-k3-kimicode-20260815",
            "source_id": "terminal_bench",
            "source_role": "primary",
            "subject_type": "model_agent",
            "model": {
                "tested_model": "Kimi K3",
                "canonical_family": "kimi-k3",
                "model_match": "exact",
            },
            "benchmark": {"id": "terminal-bench", "version": "2.1", "task_count": 89},
            "execution": {
                "agent": "Kimi Code CLI",
                "agent_version": "unknown",
                "harness": "harbor",
                "harness_version": "unknown",
                "reasoning_effort": "high",
                "attempts_per_task": 3,
            },
            "result": {"metric": "pass@1", "value": 84.0, "scale": "percent"},
            "provenance": {
                "observed_at": "2026-08-15",
                "published_at": "2026-07-01",
                "url": "https://artificialanalysis.ai/agents/coding",
                "methodology_url": "https://artificialanalysis.ai/methodology/coding-agents-benchmarking",
                "primary_approved_by": "openai-research-2026-08-15",
                "primary_approved_at": "2026-08-15T09:00:00Z",
                "approval_basis_url": "https://artificialanalysis.ai/agents/coding",
            },
            "relationships": {"composite_of": [], "duplicate_of": None},
        }
        normalized = validate_standard_evidence(valid, registry)
        self.assertEqual(normalized["source_role"], "primary")
        self.assertEqual(normalized["model"]["model_match"], "exact")

        for field_path in (
            ("benchmark", "version"),
            ("execution", "agent"),
            ("execution", "harness"),
            ("execution", "reasoning_effort"),
            ("execution", "attempts_per_task"),
            ("provenance", "primary_approved_by"),
            ("provenance", "approval_basis_url"),
        ):
            with self.subTest(field_path=field_path):
                bad = json.loads(json.dumps(valid))
                bad[field_path[0]].pop(field_path[1])
                with self.assertRaises(ModelEvaluationError):
                    validate_standard_evidence(bad, registry)

        near = json.loads(json.dumps(valid))
        near["model"]["model_match"] = "family"
        with self.assertRaises(ModelEvaluationError):
            validate_standard_evidence(near, registry)

    def test_unknown_evidence_field_fails_closed(self) -> None:
        registry = ModelEvaluationSourceRegistry.load_bundled()
        evidence = {
            "evidence_schema": "tp-voyager.model_evidence/v1",
            "evidence_id": "provider-kimi-k3",
            "source_id": "provider_official",
            "source_role": "provider",
            "subject_type": "provider_claim",
            "model": {"tested_model": "Kimi K3", "canonical_family": "kimi-k3", "model_match": "exact"},
            "benchmark": {"id": "provider-model-card", "version": "2026-07-16", "task_count": None},
            "execution": {"agent": None, "agent_version": None, "harness": None, "harness_version": None, "reasoning_effort": None, "attempts_per_task": None},
            "result": {"metric": "release", "value": "Kimi K3", "scale": "text"},
            "provenance": {"observed_at": "2026-08-15", "published_at": "2026-07-16", "url": "https://www.kimi.com/", "methodology_url": None, "primary_approved_by": None, "primary_approved_at": None, "approval_basis_url": None},
            "relationships": {"composite_of": [], "duplicate_of": None},
            "surprise": True,
        }
        with self.assertRaises(ModelEvaluationError):
            validate_standard_evidence(evidence, registry)

    def test_v1_operator_file_loads_read_only_into_uncalibrated_v2_semantics(self) -> None:
        home = self._home()
        path = home / "model_routing_profiles.json"
        payload = self._legacy_payload()
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        mtime = path.stat().st_mtime_ns

        profiles = ModelRoutingProfiles.load(home)

        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        self.assertEqual(profiles.schema, "tp-voyager.model_routing_profiles/v1")
        self.assertEqual(profiles.normalized_schema, "tp-voyager.model_routing_profiles/v2")
        fixed = profiles.get("codebuddy:glm-5.2")
        self.assertEqual(fixed["legacy_capability_tier"], "L3")
        self.assertEqual(fixed["capability_tier"], "UNCLASSIFIED")
        self.assertEqual(fixed["tier_authority"], "standard_v1_uncalibrated")
        self.assertIsNone(fixed["scorecard"])
        self.assertEqual(fixed["benchmark_evidence"][0]["metrics"]["intelligence_index"], 51)
        dynamic = profiles.get("qoder:ultimate")
        self.assertEqual(dynamic["capability_tier"], "DYNAMIC")
        self.assertEqual(dynamic["provider_tier_label"], "Ultimate")
        self.assertEqual(dynamic["tier_authority"], "provider_dynamic")
        self.assertIsNone(dynamic["scorecard"])
        self.assertIsNone(profiles.get("qoder:auto"))
        self.assertIn("qoder:auto", profiles.retired_routes)

    def test_migration_dry_run_is_read_only_and_write_is_atomic_idempotent(self) -> None:
        home = self._home()
        path = home / "model_routing_profiles.json"
        path.write_text(json.dumps(self._legacy_payload(), indent=2), encoding="utf-8")
        before = hashlib.sha256(path.read_bytes()).hexdigest()

        dry = ModelRoutingProfiles.migrate(home, write=False)
        self.assertEqual(dry["source_schema"], "tp-voyager.model_routing_profiles/v1")
        self.assertEqual(dry["target_schema"], "tp-voyager.model_routing_profiles/v2")
        self.assertFalse(dry["written"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(dry["retired_routes"], ["qoder:auto"])

        written = ModelRoutingProfiles.migrate(home, write=True)
        self.assertTrue(written["written"])
        disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(disk["schema"], "tp-voyager.model_routing_profiles/v2")
        self.assertNotIn("qoder:auto", disk["profiles"])
        fixed = disk["profiles"]["codebuddy:glm-5.2"]
        self.assertEqual(fixed["legacy_capability_tier"], "L3")
        self.assertEqual(fixed["capability_tier"], "UNCLASSIFIED")
        self.assertEqual(fixed["tier_authority"], "standard_v1_uncalibrated")
        self.assertIsNone(fixed["scorecard"])
        self.assertEqual(fixed["benchmark_evidence"][0]["metrics"]["intelligence_index"], 51)
        self.assertEqual(ModelRoutingProfiles.load(home).normalized_schema, "tp-voyager.model_routing_profiles/v2")

        second = ModelRoutingProfiles.migrate(home, write=True)
        self.assertFalse(second["written"])
        self.assertEqual(second["status"], "already_v2")

    def test_v2_scorecard_is_persisted_snapshot_and_tier_mismatch_fails_closed(self) -> None:
        home = self._home()
        payload = {
            "schema": "tp-voyager.model_routing_profiles/v2",
            "updated_at": "2026-08-15",
            "evaluation_standard": "tp-voyager.model_evaluation/v1",
            "tier_rules_status": "calibrated",
            "profiles": {
                "codebuddy:kimi-k3-2": {
                    "canonical_family": "kimi-k3",
                    "provider_identity": "provider_declared",
                    "legacy_capability_tier": "L3",
                    "capability_tier": "L3",
                    "tier_authority": "standard_v1",
                    "scorecard": {
                        "schema": "tp-voyager.model_scorecard/v1",
                        "rules_version": "tp-voyager.model_tier_rules/v1",
                        "rules_status": "calibrated",
                        "evaluated_at": "2026-08-15",
                        "dimensions": {
                            "repository_engineering": {"status": "measured", "evidence_ids": ["e1"]},
                            "terminal_agentic": {"status": "measured", "evidence_ids": ["e2"]},
                            "codebase_understanding": {"status": "measured", "evidence_ids": ["e3"]},
                            "general_coding": {"status": "N/A", "evidence_ids": []},
                            "multimodal_coding": {"status": "N/A", "evidence_ids": []},
                        },
                        "coverage": "high",
                        "confidence": "high",
                        "tier": "L3",
                    },
                    "standard_evidence": [],
                }
            },
        }
        path = home / "model_routing_profiles.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = ModelRoutingProfiles.load(home)
        self.assertEqual(loaded.get("codebuddy:kimi-k3-2")["scorecard"]["tier"], "L3")
        bad = json.loads(json.dumps(payload))
        bad["profiles"]["codebuddy:kimi-k3-2"]["capability_tier"] = "L2"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ModelRoutingProfileError):
            ModelRoutingProfiles.load(home)

    def test_dynamic_v2_profile_must_be_dynamic_and_have_no_scorecard(self) -> None:
        home = self._home()
        payload = {
            "schema": "tp-voyager.model_routing_profiles/v2",
            "updated_at": "2026-08-15",
            "evaluation_standard": "tp-voyager.model_evaluation/v1",
            "tier_rules_status": "calibrated",
            "profiles": {
                "qoder:ultimate": {
                    "canonical_family": "qoder-ultimate-tier",
                    "provider_identity": "dynamic_tier",
                    "provider_tier_label": "Ultimate",
                    "legacy_capability_tier": "L3",
                    "capability_tier": "DYNAMIC",
                    "tier_authority": "provider_dynamic",
                    "scorecard": None,
                    "standard_evidence": [],
                }
            },
        }
        path = home / "model_routing_profiles.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(ModelRoutingProfiles.load(home).get("qoder:ultimate")["capability_tier"], "DYNAMIC")
        bad = json.loads(json.dumps(payload))
        bad["profiles"]["qoder:ultimate"]["capability_tier"] = "L3"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ModelRoutingProfileError):
            ModelRoutingProfiles.load(home)

    def test_scorecard_refuses_provider_only_promotion_and_does_not_double_count_composite(self) -> None:
        registry = ModelEvaluationSourceRegistry.load_bundled()
        rules = load_tier_rules()
        provider = {
            "evidence_schema": "tp-voyager.model_evidence/v1",
            "evidence_id": "provider-minimax-m3-tb21",
            "source_id": "provider_official",
            "source_role": "provider",
            "subject_type": "provider_claim",
            "model": {"tested_model": "MiniMax M3", "canonical_family": "minimax-m3", "model_match": "exact"},
            "benchmark": {"id": "terminal-bench", "version": "2.1", "task_count": 89},
            "execution": {"agent": "Terminus 2", "agent_version": None, "harness": "provider_internal", "harness_version": None, "reasoning_effort": None, "attempts_per_task": None},
            "result": {"metric": "pass@1", "value": 66.0, "scale": "percent"},
            "provenance": {"observed_at": "2026-08-15", "published_at": "2026-06-01", "url": "https://www.minimax.io/news/minimax-m3", "methodology_url": None, "primary_approved_by": None, "primary_approved_at": None, "approval_basis_url": None},
            "relationships": {"composite_of": [], "duplicate_of": None},
        }
        scorecard = build_scorecard("minimax-m3", [provider], registry, rules)
        self.assertEqual(scorecard["tier"], "UNCLASSIFIED")
        self.assertEqual(scorecard["coverage"], "low")

    def test_uncalibrated_rules_never_generate_formal_tier(self) -> None:
        registry = ModelEvaluationSourceRegistry.load_bundled()
        rules = dict(load_tier_rules())
        rules["status"] = "uncalibrated"
        scorecard = build_scorecard("kimi-k3", [], registry, rules)
        self.assertEqual(scorecard["tier"], "UNCLASSIFIED")
        self.assertEqual(scorecard["rules_status"], "uncalibrated")

    def test_cli_migrate_and_validator_are_explicit_and_read_only_by_default(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from agent_runtime.cli import main

        home = self._home()
        path = home / "model_routing_profiles.json"
        path.write_text(json.dumps(self._legacy_payload()), encoding="utf-8")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.dict(__import__("os").environ, {"TP_VOYAGER_HOME": str(home)}, clear=False):
            out = StringIO()
            with redirect_stdout(out):
                rc = main(["model-routing-migrate", "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertFalse(json.loads(out.getvalue())["written"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

            out = StringIO()
            with redirect_stdout(out):
                rc = main(["model-routing-migrate", "--write"])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(out.getvalue())["written"])

            out = StringIO()
            with redirect_stdout(out):
                rc = main(["model-evaluation-validate"])
            self.assertEqual(rc, 0)
            report = json.loads(out.getvalue())
            self.assertEqual(report["schema"], "tp-voyager.model_evaluation_validation/v1")
            self.assertEqual(report["invalid_evidence"], 0)
            self.assertEqual(report["tier_authority_conflicts"], 0)
            self.assertEqual(report["tier_rules"], "uncalibrated")
            self.assertFalse(report["network_access_performed"])
            self.assertFalse(report["write_performed"])


def test_current_bundled_baseline_retires_auto_and_persists_standard_scorecards(tmp_path):
    from agent_runtime.application.crew.routing_profiles import ModelRoutingProfiles

    profiles = ModelRoutingProfiles.load(tmp_path)
    routes = set(profiles.route_ids())
    dynamic = [p for p in profiles.profiles if p.provider_identity == "dynamic_tier"]
    fixed = [p for p in profiles.profiles if p.provider_identity != "dynamic_tier"]

    assert "qoder:auto" not in routes
    assert {p.route_id for p in dynamic} == {
        "qoder:ultimate", "qoder:performance", "qoder:efficient", "qoder:Lite"
    }
    assert "codebuddy:glm-5.3" in routes
    assert profiles.schema == "tp-voyager.model_routing_profiles/v2"
    assert profiles.tier_rules_status == "calibrated"
    assert all(p.scorecard is not None for p in fixed)
    assert all(p.capability_tier == p.scorecard["tier"] for p in fixed)
    assert all(p.tier_authority == "standard_v1" for p in fixed)
    kimi = profiles.get("codebuddy:kimi-k3-2")
    assert kimi is not None
    assert kimi["capability_tier"] == "L3"
    assert kimi["scorecard"]["coverage"] == "high"


def test_provider_registry_metadata_url_can_be_omitted_but_evidence_provenance_cannot(tmp_path):
    from agent_runtime.application.crew.model_evaluation import (
        ModelEvaluationError, ModelEvaluationSourceRegistry, validate_standard_evidence,
    )

    registry_doc = {
        "schema": "tp-voyager.model_evaluation_sources/v1",
        "updated_at": "2026-08-15",
        "sources": {
            "provider_official": {
                "status": "active", "role": "provider", "source_type": "provider",
                "dimensions": [], "requires": ["exact_model_identity", "provenance_url", "observed_at"],
                "composite_of": [], "freshness_policy_days": 365,
                "url": None, "methodology_url": None,
            }
        },
    }
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(registry_doc), encoding="utf-8")
    registry = ModelEvaluationSourceRegistry.load(path)
    record = _evidence(source_id="provider_official", source_role="provider", subject_type="provider_claim")
    record["provenance"]["url"] = None
    with pytest.raises(ModelEvaluationError, match="provenance URL"):
        validate_standard_evidence(record, registry)


def _primary_component_evidence(*, evidence_id: str, benchmark_id: str, version: str, value: float) -> dict:
    return {
        "evidence_schema": "tp-voyager.model_evidence/v1",
        "evidence_id": evidence_id,
        "source_id": "artificial_analysis_coding_agent",
        "source_role": "primary",
        "subject_type": "model_agent",
        "model": {"tested_model": "Kimi K3", "canonical_family": "kimi-k3", "model_match": "exact"},
        "benchmark": {"id": benchmark_id, "version": version, "task_count": 100},
        "execution": {"agent": "Kimi Code CLI", "agent_version": None, "harness": "Artificial Analysis", "harness_version": "v1.3", "reasoning_effort": None, "attempts_per_task": 3},
        "result": {"metric": "pass@1", "value": value, "scale": "percent"},
        "provenance": {"observed_at": "2026-08-15", "published_at": None, "url": "https://artificialanalysis.ai/agents/coding-agents/comparisons/claude-code-vs-kimi-code-cli", "methodology_url": "https://artificialanalysis.ai/methodology/coding-agents-benchmarking", "primary_approved_by": "openai-research-2026-08-15", "primary_approved_at": "2026-08-15T09:00:00Z", "approval_basis_url": "https://artificialanalysis.ai/agents/coding-agents/comparisons/claude-code-vs-kimi-code-cli"},
        "relationships": {"composite_of": [], "duplicate_of": None},
    }


def test_scorecard_only_promotes_primary_versions_in_calibrated_domain():
    registry = ModelEvaluationSourceRegistry.load_bundled()
    rules = load_tier_rules()
    good = _primary_component_evidence(evidence_id="k3-deepswe-current", benchmark_id="deep-swe", version="AA-CAI-v1.3", value=64.0)
    old = _primary_component_evidence(evidence_id="k3-tbench-old", benchmark_id="terminal-bench", version="2.0-old-harness", value=99.0)
    scorecard = build_scorecard("kimi-k3", [good, old], registry, rules)
    assert scorecard["tier"] == "L1"
    terminal = scorecard["dimensions"]["terminal_agentic"]["measurements"][0]
    assert terminal["primary"] is False
    assert terminal["calibration_compatible"] is False


def test_tier_rules_include_l0_for_below_l1_but_valid_primary():
    registry = ModelEvaluationSourceRegistry.load_bundled()
    rules = load_tier_rules()
    low = _primary_component_evidence(evidence_id="k3-deepswe-low", benchmark_id="deep-swe", version="AA-CAI-v1.3", value=5.0)
    scorecard = build_scorecard("kimi-k3", [low], registry, rules)
    assert scorecard["tier"] == "L0"


def _evidence(*, source_id: str, source_role: str, subject_type: str) -> dict:
    return {
        "evidence_schema": "tp-voyager.model_evidence/v1",
        "evidence_id": "provider-test-evidence",
        "source_id": source_id,
        "source_role": source_role,
        "subject_type": subject_type,
        "model": {"tested_model": "Test Model", "canonical_family": "test-model", "model_match": "exact"},
        "benchmark": {"id": "provider-model-card", "version": "2026-08-15", "task_count": None},
        "execution": {"agent": None, "agent_version": None, "harness": None, "harness_version": None, "reasoning_effort": None, "attempts_per_task": None},
        "result": {"metric": "release", "value": "available", "scale": "text"},
        "provenance": {"observed_at": "2026-08-15", "published_at": None, "url": "https://example.com/model", "methodology_url": None, "primary_approved_by": None, "primary_approved_at": None, "approval_basis_url": None},
        "relationships": {"composite_of": [], "duplicate_of": None},
    }


def test_migration_post_replace_validation_failure_restores_original_file(tmp_path):
    from unittest.mock import patch
    from agent_runtime.application.crew.routing_profiles import ModelRoutingProfileError, ModelRoutingProfiles

    legacy = {
        "schema": "tp-voyager.model_routing_profiles/v1",
        "updated_at": "2026-08-14",
        "profiles": {
            "codebuddy:glm-5.2": {
                "canonical_family": "glm-5.2",
                "capability_tier": "L3",
                "recommended_tasks": ["implementation"],
                "risk_boundaries": ["Captain review"],
                "suggested_effort": "high",
                "benchmark_evidence": [],
            }
        },
    }
    path = tmp_path / "model_routing_profiles.json"
    path.write_text(json.dumps(legacy, indent=2), encoding="utf-8")
    original = path.read_bytes()

    with patch.object(ModelRoutingProfiles, "load", side_effect=ModelRoutingProfileError("post-write failed")):
        with pytest.raises(ModelRoutingProfileError, match="post-write failed"):
            ModelRoutingProfiles.migrate(tmp_path, write=True)

    assert path.read_bytes() == original

if __name__ == "__main__":
    unittest.main()

