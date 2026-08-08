"""Maintained TP-Voyager test profiles.

Policy:
- ``smoke`` is the default fast structural confidence gate.
- ``current`` covers the current Captain/Crew execution surface.
- ``regression`` is the maintained cross-core contract suite.
- ``stress`` is reserved for lease/cancel/reconciliation race behavior.
- ``release`` is regression + stress and is explicit, never routine.

Historical full-discovery/audit is intentionally not a maintained profile.
Tests protect current supported behavior, not retired WorkBuddy execution.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestTarget:
    name: str
    timeout_seconds: int = 180
    reason: str = ""


STRESS_TARGETS = (
    TestTarget("tests.test_runtime_stress.LeaseExpiryTests", 240, "lease expiry fencing"),
    TestTarget("tests.test_runtime_stress.SameOwnerReacquireTests", 240, "lease generation reacquisition"),
    TestTarget("tests.test_runtime_stress.DeterministicRaceTests", 300, "cancel/reconciliation races"),
    TestTarget("tests.test_runtime_stress.PublicCancelTerminalRaceTests", 240, "public cancel terminal races"),
    TestTarget("tests.test_runtime_stress.HeartbeatErrorTests", 240, "heartbeat failure handling"),
)

SMOKE_TARGETS = (
    TestTarget("tests.test_backend_contract", 90, "current Crew backend contract"),
    TestTarget("tests.test_runtime_reconciliation", 120, "restart reconciliation and legacy-backend loss semantics"),
    TestTarget("tests.test_v12_workflow.WorkflowV12Tests.test_completed_stage_advances_next_stage", 90, "workflow progression"),
    TestTarget("tests.test_v12_workflow.WorkflowV12Tests.test_optional_operator_checkpoint_blocks_then_approves", 90, "approval boundary"),
    TestTarget("tests.test_v14_tool_runtime.ToolRuntimeServiceTests.test_filesystem_tools_return_bounded_relative_results", 90, "filesystem tool boundary"),
    TestTarget("tests.test_v14_tool_runtime.ToolRuntimeServiceTests.test_path_escape_and_binary_reads_are_rejected_and_audited", 90, "tool path safety"),
    TestTarget("tests.test_v15_knowledge_runtime.KnowledgeRuntimeServiceTests.test_search_returns_verified_citations_and_audits_hashes_only", 90, "knowledge citations"),
    TestTarget("tests.test_v15_knowledge_runtime.KnowledgeRuntimeServiceTests.test_drift_never_returns_stale_content_and_is_audited", 90, "knowledge drift safety"),
    TestTarget("tests.test_patch_worker.PatchPolicyTests.test_policy_rejects_unsafe_paths_duplicate_commands_and_unknown_verification", 90, "bounded patch policy"),
    TestTarget("tests.test_patch_worker.PatchDurableIntegrationTests.test_captain_patch_reuses_durable_core_captures_patch_verifies_and_cleans_worktree", 120, "patch durable/evidence closure"),
    TestTarget("tests.test_crew_registry.CrewRegistryTests.test_catalog_is_content_free_and_does_not_select_or_dispatch", 90, "Crew Registry boundary"),
    TestTarget("tests.test_captain_boundary.CaptainBoundaryTests.test_overview_is_bounded_content_free_and_surfaces_attention", 90, "Captain voyage overview"),
    TestTarget("tests.test_v2_task_launch.TaskLaunchServiceTests.test_qoder_routes_explicit_fields_and_rejects_legacy_review_fields", 90, "shared launch boundary"),
    TestTarget("tests.test_v2_plan_execution_controller.PlanExecutionControllerTests.test_pump_advances_one_task_at_a_time_to_completion", 120, "durable plan execution spine"),
    TestTarget("tests.test_runtime_migrations.MigrationTests.test_empty_database_initializes_to_current_schema", 120, "schema initialization"),
    TestTarget("tests.test_captain_policy_evidence.CaptainPolicyEvidenceTests.test_usage_evidence_records_only_provider_reported_values", 90, "Usage Evidence contract"),
    TestTarget("tests.test_tp_voyager_architecture", 90, "TP-Voyager architecture gate"),
    TestTarget("tests.test_test_profiles", 90, "test-profile policy"),
)

CURRENT_TARGETS = (
    TestTarget("tests.test_patch_worker", 180, "T4 isolated patch/evidence boundary"),
    TestTarget("tests.test_codebuddy_backend", 180, "CodeBuddy official controlled routes"),
    TestTarget("tests.test_qoder_acp_client", 120, "Qoder ACP host permission boundary"),
    TestTarget("tests.test_qoder_backend", 180, "Qoder controlled routes"),
    TestTarget("tests.test_crew_registry", 120, "Crew Registry"),
    TestTarget("tests.test_captain_boundary", 120, "Captain boundary"),
    TestTarget("tests.test_v2_task_launch", 120, "shared Durable Task launch boundary"),
    TestTarget("tests.test_runtime_reconciliation", 120, "restart reconciliation"),
    TestTarget("tests.test_tp_voyager_architecture", 90, "Charter/directory architecture gate"),
    TestTarget("tests.test_test_profiles", 90, "test policy integrity"),
)

REGRESSION_MODULES = (
    "test_activity_log",
    "test_backend_contract",
    "test_captain_boundary",
    "test_captain_policy_evidence",
    "test_codebuddy_backend",
    "test_crew_registry",
    "test_patch_worker",
    "test_qoder_acp_client",
    "test_qoder_backend",
    "test_runtime_diagnostics",
    "test_runtime_domain",
    "test_runtime_migrations",
    "test_runtime_reconciliation",
    "test_runtime_repositories",
    "test_test_profiles",
    "test_tp_voyager_architecture",
    "test_v12_capabilities",
    "test_v12_context",
    "test_v12_workflow",
    "test_v13_completion_recovery",
    "test_v14_tool_runtime",
    "test_v15_knowledge_runtime",
    "test_v16_planner",
    "test_v16_server_api",
    "test_v2_home_migration",
    "test_v2_plan_execution_controller",
    "test_v2_plan_execution_foundation",
    "test_v2_server_api",
    "test_v2_task_launch",
)


def profile_targets(profile: str) -> tuple[TestTarget, ...]:
    canonical = str(profile or "").strip().lower()
    if canonical == "smoke":
        return SMOKE_TARGETS
    if canonical == "current":
        return CURRENT_TARGETS
    if canonical == "stress":
        return STRESS_TARGETS
    if canonical == "regression":
        return tuple(TestTarget(f"tests.{name}", 240, "maintained canonical regression") for name in REGRESSION_MODULES)
    if canonical == "release":
        return profile_targets("regression") + profile_targets("stress")
    raise ValueError("profile must be one of: smoke, current, regression, stress, release")


def regression_modules() -> set[str]:
    return set(REGRESSION_MODULES)
