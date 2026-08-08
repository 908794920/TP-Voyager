"""Deterministic, caller-driven V1.6 Planner foundation.

The planner converts explicit caller intent into a small linear execution
specification.  It never creates workflows/tasks, selects a model/backend,
dispatches an agent, retries, or writes knowledge.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any, Iterable

from agent_runtime.domain.enums import (
    PlannerComplexity,
    PlannerEventType,
    PlannerPlanStatus,
    PlannerRiskLevel,
    PlannerStepKind,
    PlannerTaskKind,
)
from agent_runtime.domain.ids import (
    new_planner_event_id,
    new_planner_plan_id,
    new_planner_step_id,
)
from agent_runtime.domain.planner import (
    PlannerDependency,
    PlannerEvent,
    PlannerPlan,
    PlannerStep,
)
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.context_repository import ContextRepository
from agent_runtime.persistence.knowledge_repository import KnowledgeRepository
from agent_runtime.persistence.planner_repository import PlannerRepository

PLANNER_PLAN_SCHEMA = "workbuddy.planner_plan/v1"
PLANNER_LIST_SCHEMA = "workbuddy.planner_list/v1"
PLANNER_HISTORY_SCHEMA = "workbuddy.planner_history/v1"
EXECUTION_SPEC_SCHEMA = "workbuddy.execution_spec/v1"
_POLICY_VERSION = "workbuddy.planner-policy/v1"

MAX_REQUIREMENT_CHARS = 20_000
MAX_ACCEPTANCE_ITEMS = 32
MAX_ACCEPTANCE_ITEM_CHARS = 1_000
MAX_STEPS = 8
MAX_LIST_LIMIT = 200
MAX_HISTORY_LIMIT = 500

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")

_STEP_TEMPLATES: dict[str, dict[str, Any]] = {
    "analyze": {
        "title": "Analyze scope",
        "kind": PlannerStepKind.ANALYSIS.value,
        "capabilities": ["filesystem.list", "filesystem.read_text", "git.status"],
        "reason": "scope_must_be_bounded",
    },
    "diagnose": {
        "title": "Diagnose current behavior",
        "kind": PlannerStepKind.ANALYSIS.value,
        "capabilities": ["filesystem.read_text", "git.status", "git.diff"],
        "reason": "maintenance_requires_diagnosis",
    },
    "inspect": {
        "title": "Inspect implementation",
        "kind": PlannerStepKind.ANALYSIS.value,
        "capabilities": ["filesystem.read_text", "git.status", "git.diff"],
        "reason": "review_requires_observed_material",
    },
    "implement": {
        "title": "Implement bounded change",
        "kind": PlannerStepKind.IMPLEMENTATION.value,
        "capabilities": ["backend.coding"],
        "reason": "implementation_requested",
    },
    "remediate": {
        "title": "Apply bounded remediation",
        "kind": PlannerStepKind.IMPLEMENTATION.value,
        "capabilities": ["backend.coding"],
        "reason": "maintenance_change_requested",
    },
    "document": {
        "title": "Produce documentation",
        "kind": PlannerStepKind.DOCUMENTATION.value,
        "capabilities": ["backend.documentation"],
        "reason": "documentation_requested",
    },
    "verify": {
        "title": "Verify acceptance criteria",
        "kind": PlannerStepKind.VERIFICATION.value,
        "capabilities": ["verification.execute"],
        "reason": "verification_required",
    },
    "review": {
        "title": "Review verified result",
        "kind": PlannerStepKind.REVIEW.value,
        "capabilities": ["backend.review"],
        "reason": "independent_review_required",
    },
    "report": {
        "title": "Report findings",
        "kind": PlannerStepKind.REPORT.value,
        "capabilities": ["backend.reporting"],
        "reason": "result_must_be_reported",
    },
}

_BASE_POLICIES = {
    PlannerTaskKind.ANALYSIS.value: ("analyze", "report"),
    PlannerTaskKind.IMPLEMENTATION.value: ("analyze", "implement", "verify"),
    PlannerTaskKind.REVIEW.value: ("inspect", "review", "report"),
    PlannerTaskKind.DOCUMENTATION.value: ("analyze", "document", "verify"),
    PlannerTaskKind.MAINTENANCE.value: ("diagnose", "remediate", "verify"),
}


class PlannerError(ValueError):
    code = "planner_error"


class PlannerPolicyError(PlannerError):
    code = "policy_rejected"


class PlannerNotFoundError(PlannerPolicyError):
    code = "plan_not_found"


class PlannerConflictError(PlannerPolicyError):
    code = "plan_conflict"


class PlannerService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = PlannerRepository(db)
        self.knowledge = KnowledgeRepository(db)
        self.contexts = ContextRepository(db)

    def create(
        self,
        *,
        name: str,
        requirement: str,
        task_kind: str = "implementation",
        complexity: str = "medium",
        acceptance_criteria: Iterable[str] | None = None,
        knowledge_id: str | None = None,
        context_id: str | None = None,
        runtime: str | None = None,
        agent_profile: str | None = None,
        require_approval: bool = False,
        verification_required: bool = True,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        display_name = self._name(name)
        raw_requirement = self._requirement(requirement)
        kind = self._enum(task_kind, PlannerTaskKind, "task_kind")
        level = self._enum(complexity, PlannerComplexity, "complexity")
        acceptance = self._acceptance(acceptance_criteria)
        identifier = self._id(plan_id, "plan_id") if plan_id else new_planner_plan_id()
        knowledge_ref = self._optional_id(knowledge_id, "knowledge_id")
        context_ref = self._optional_id(context_id, "context_id")
        runtime_ref = self._optional_text(runtime, "runtime", 80)
        profile_ref = self._optional_text(agent_profile, "agent_profile", 120)
        approval = self._strict_bool(require_approval, "require_approval")
        verify = self._strict_bool(verification_required, "verification_required")
        risk = self._risk(kind, level)
        keys = self._policy_keys(kind, level, verification_required=verify)
        if not 1 <= len(keys) <= MAX_STEPS:
            raise PlannerPolicyError("planner step count exceeds policy limit")

        requirement_hash = self._sha256(raw_requirement)
        acceptance_hash = self._sha256(
            json.dumps(acceptance, ensure_ascii=False, separators=(",", ":"))
        )
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            existing = self.repo.get_plan_in_connection(connection, identifier)
            if existing is not None:
                steps = self.repo.list_steps_in_connection(connection, identifier)
                if (
                    existing.name == display_name
                    and existing.task_kind == kind
                    and existing.complexity == level
                    and existing.requirement_sha256 == requirement_hash
                    and existing.acceptance_sha256 == acceptance_hash
                    and existing.knowledge_id == knowledge_ref
                    and existing.context_id == context_ref
                    and existing.runtime == runtime_ref
                    and existing.agent_profile == profile_ref
                    and [item.step_key for item in steps] == list(keys)
                    and [item.approval_required for item in steps]
                    == [item == self._approval_key(keys) if approval else False for item in keys]
                    and [item.verification_required for item in steps]
                    == [item == "verify" for item in keys]
                ):
                    return {**self._projection(existing, steps, self.repo.list_dependencies_in_connection(connection, identifier)), "replayed": True}
                raise PlannerConflictError("plan_id already exists with different intent")

            self._validate_references(connection, knowledge_ref, context_ref)
            plan = PlannerPlan(
                plan_id=identifier,
                name=display_name,
                task_kind=kind,
                complexity=level,
                risk_level=risk,
                status=PlannerPlanStatus.DRAFT.value,
                requirement_sha256=requirement_hash,
                acceptance_sha256=acceptance_hash,
                policy_version=_POLICY_VERSION,
                step_count=len(keys),
                knowledge_id=knowledge_ref,
                context_id=context_ref,
                runtime=runtime_ref,
                agent_profile=profile_ref,
                created_at=db_now,
                updated_at=db_now,
            )
            steps: list[PlannerStep] = []
            dependencies: list[PlannerDependency] = []
            previous_id: str | None = None
            approval_key = self._approval_key(keys) if approval else ""
            for position, key in enumerate(keys, start=1):
                template = _STEP_TEMPLATES[key]
                step_id = new_planner_step_id()
                step = PlannerStep(
                    step_id=step_id,
                    plan_id=identifier,
                    step_key=key,
                    title=str(template["title"]),
                    position=position,
                    kind=str(template["kind"]),
                    approval_required=key == approval_key,
                    verification_required=key == "verify",
                    capabilities_json=json.dumps(
                        template["capabilities"], ensure_ascii=False, separators=(",", ":")
                    ),
                    reason_code=str(template["reason"]),
                    created_at=db_now,
                )
                steps.append(step)
                if previous_id is not None:
                    dependencies.append(
                        PlannerDependency(identifier, step_id, previous_id)
                    )
                previous_id = step_id
            self.repo.create_plan(connection, plan, steps, dependencies)
            self.repo.append_event(
                connection,
                PlannerEvent(
                    event_id=new_planner_event_id(),
                    plan_id=identifier,
                    event_type=PlannerEventType.PLAN_CREATED.value,
                    event_time=db_now,
                    status=plan.status,
                    reason_code="deterministic_policy_applied",
                    step_count=plan.step_count,
                ),
            )
        return {**self._projection(plan, steps, dependencies), "replayed": False}

    def validate(self, plan_id: str) -> dict[str, Any]:
        identifier = self._id(plan_id, "plan_id")
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            plan = self._required_in_connection(connection, identifier)
            steps = self.repo.list_steps_in_connection(connection, identifier)
            dependencies = self.repo.list_dependencies_in_connection(connection, identifier)
            self._validate_references(connection, plan.knowledge_id, plan.context_id)
            self._validate_structure(plan, steps, dependencies)
            if plan.status in {PlannerPlanStatus.VALIDATED.value, PlannerPlanStatus.PREPARED.value}:
                return {**self._projection(plan, steps, dependencies), "replayed": True}
            if plan.status != PlannerPlanStatus.DRAFT.value:
                raise PlannerConflictError("plan is not in draft state")
            changed = self.repo.update_status(
                connection,
                identifier,
                expected_status=PlannerPlanStatus.DRAFT.value,
                status=PlannerPlanStatus.VALIDATED.value,
                updated_at=db_now,
            )
            if not changed:
                raise PlannerConflictError("plan changed concurrently")
            plan = replace(
                plan,
                status=PlannerPlanStatus.VALIDATED.value,
                updated_at=db_now,
                version=plan.version + 1,
            )
            self.repo.append_event(
                connection,
                PlannerEvent(
                    event_id=new_planner_event_id(),
                    plan_id=identifier,
                    event_type=PlannerEventType.PLAN_VALIDATED.value,
                    event_time=db_now,
                    status=plan.status,
                    reason_code="structure_and_references_valid",
                    step_count=plan.step_count,
                ),
            )
        return {**self._projection(plan, steps, dependencies), "replayed": False}

    def prepare(self, plan_id: str) -> dict[str, Any]:
        identifier = self._id(plan_id, "plan_id")
        with self.db.immediate_fenced_transaction() as (connection, db_now):
            plan = self._required_in_connection(connection, identifier)
            steps = self.repo.list_steps_in_connection(connection, identifier)
            dependencies = self.repo.list_dependencies_in_connection(connection, identifier)
            self._validate_references(connection, plan.knowledge_id, plan.context_id)
            self._validate_structure(plan, steps, dependencies)
            if plan.status == PlannerPlanStatus.DRAFT.value:
                raise PlannerPolicyError("plan must be validated before preparation")
            replayed = plan.status == PlannerPlanStatus.PREPARED.value
            if not replayed:
                if plan.status != PlannerPlanStatus.VALIDATED.value:
                    raise PlannerConflictError("plan is not validated")
                changed = self.repo.update_status(
                    connection,
                    identifier,
                    expected_status=PlannerPlanStatus.VALIDATED.value,
                    status=PlannerPlanStatus.PREPARED.value,
                    updated_at=db_now,
                )
                if not changed:
                    raise PlannerConflictError("plan changed concurrently")
                plan = replace(
                    plan,
                    status=PlannerPlanStatus.PREPARED.value,
                    updated_at=db_now,
                    version=plan.version + 1,
                )
                self.repo.append_event(
                    connection,
                    PlannerEvent(
                        event_id=new_planner_event_id(),
                        plan_id=identifier,
                        event_type=PlannerEventType.PLAN_PREPARED.value,
                        event_time=db_now,
                        status=plan.status,
                        reason_code="explicit_execution_spec_prepared",
                        step_count=plan.step_count,
                    ),
                )
        return {
            **self._projection(plan, steps, dependencies),
            "replayed": replayed,
            "execution_spec": self._execution_spec(plan, steps),
        }

    def status(self, plan_id: str) -> dict[str, Any]:
        identifier = self._id(plan_id, "plan_id")
        plan = self.repo.get_plan(identifier)
        if plan is None:
            raise PlannerNotFoundError(f"unknown plan_id: {identifier}")
        return self._projection(
            plan, self.repo.list_steps(identifier), self.repo.list_dependencies(identifier)
        )

    def list(self, *, status: str = "", limit: int = 100) -> dict[str, Any]:
        canonical_status = ""
        if str(status or "").strip():
            canonical_status = self._enum(status, PlannerPlanStatus, "status")
        bounded = self._bounded_int(limit, "limit", 1, MAX_LIST_LIMIT)
        return {
            "ok": True,
            "schema": PLANNER_LIST_SCHEMA,
            "plans": [self._plan_public(item) for item in self.repo.list_plans(status=canonical_status, limit=bounded)],
            **self._non_action_flags(),
        }

    def history(self, *, plan_id: str = "", limit: int = 100) -> dict[str, Any]:
        identifier = self._id(plan_id, "plan_id") if str(plan_id or "").strip() else ""
        bounded = self._bounded_int(limit, "limit", 1, MAX_HISTORY_LIMIT)
        if identifier and self.repo.get_plan(identifier) is None:
            raise PlannerNotFoundError(f"unknown plan_id: {identifier}")
        events = self.repo.list_events(plan_id=identifier, limit=bounded)
        return {
            "ok": True,
            "schema": PLANNER_HISTORY_SCHEMA,
            "events": [
                {
                    "seq": item.seq,
                    "event_id": item.event_id,
                    "plan_id": item.plan_id,
                    "event_type": item.event_type,
                    "event_time": item.event_time,
                    "status": item.status,
                    "reason_codes": [x for x in item.reason_code.split(";") if x],
                    "step_count": item.step_count,
                    "raw_requirement_stored": False,
                    "raw_acceptance_stored": False,
                }
                for item in events
            ],
            **self._non_action_flags(),
        }

    @staticmethod
    def _risk(task_kind: str, complexity: str) -> str:
        if complexity == PlannerComplexity.HIGH.value:
            return PlannerRiskLevel.HIGH.value
        if task_kind in {PlannerTaskKind.IMPLEMENTATION.value, PlannerTaskKind.MAINTENANCE.value}:
            return PlannerRiskLevel.MEDIUM.value
        return PlannerRiskLevel.LOW.value

    @staticmethod
    def _policy_keys(task_kind: str, complexity: str, *, verification_required: bool) -> tuple[str, ...]:
        keys = list(_BASE_POLICIES[task_kind])
        if not verification_required:
            keys = [item for item in keys if item != "verify"]
        if complexity == PlannerComplexity.HIGH.value and "review" not in keys:
            if "report" in keys:
                keys.insert(keys.index("report"), "review")
            else:
                keys.append("review")
        return tuple(keys)

    @staticmethod
    def _approval_key(keys: tuple[str, ...]) -> str:
        for candidate in ("implement", "remediate", "document", "review", "report"):
            if candidate in keys:
                return candidate
        return keys[-1]

    def _validate_references(self, connection, knowledge_id: str | None, context_id: str | None) -> None:
        if knowledge_id:
            collection = self.knowledge.get_collection_in_connection(connection, knowledge_id)
            if collection is None:
                raise PlannerPolicyError(f"unknown knowledge_id: {knowledge_id}")
            if context_id and collection.context_id != context_id:
                raise PlannerPolicyError("knowledge_id does not belong to context_id")
        if context_id and self.contexts.get_manifest_in_connection(connection, context_id) is None:
            raise PlannerPolicyError(f"unknown context_id: {context_id}")

    @staticmethod
    def _validate_structure(
        plan: PlannerPlan,
        steps: list[PlannerStep],
        dependencies: list[PlannerDependency],
    ) -> None:
        if len(steps) != plan.step_count or not 1 <= len(steps) <= MAX_STEPS:
            raise PlannerPolicyError("plan step count is inconsistent")
        positions = [item.position for item in steps]
        if positions != list(range(1, len(steps) + 1)):
            raise PlannerPolicyError("plan positions must be contiguous")
        by_id = {item.step_id: item for item in steps}
        inbound: dict[str, list[str]] = {item.step_id: [] for item in steps}
        for item in dependencies:
            if item.plan_id != plan.plan_id or item.step_id not in by_id or item.depends_on_step_id not in by_id:
                raise PlannerPolicyError("plan dependency references an unknown step")
            if by_id[item.depends_on_step_id].position >= by_id[item.step_id].position:
                raise PlannerPolicyError("plan dependencies must point backward")
            inbound[item.step_id].append(item.depends_on_step_id)
        if inbound[steps[0].step_id]:
            raise PlannerPolicyError("first plan step must not have dependencies")
        for index, step in enumerate(steps[1:], start=1):
            expected = steps[index - 1].step_id
            if inbound[step.step_id] != [expected]:
                raise PlannerPolicyError("baseline plans must be strictly linear")

    def _required_in_connection(self, connection, plan_id: str) -> PlannerPlan:
        plan = self.repo.get_plan_in_connection(connection, plan_id)
        if plan is None:
            raise PlannerNotFoundError(f"unknown plan_id: {plan_id}")
        return plan

    def _projection(
        self,
        plan: PlannerPlan,
        steps: list[PlannerStep],
        dependencies: list[PlannerDependency],
    ) -> dict[str, Any]:
        dependencies_by_step: dict[str, list[str]] = {item.step_id: [] for item in steps}
        for item in dependencies:
            dependencies_by_step.setdefault(item.step_id, []).append(item.depends_on_step_id)
        return {
            "ok": True,
            "schema": PLANNER_PLAN_SCHEMA,
            "plan": self._plan_public(plan),
            "steps": [
                {
                    "step_id": item.step_id,
                    "step_key": item.step_key,
                    "title": item.title,
                    "position": item.position,
                    "kind": item.kind,
                    "approval_required": item.approval_required,
                    "verification_required": item.verification_required,
                    "capabilities": json.loads(item.capabilities_json),
                    "reason_code": item.reason_code,
                    "depends_on_step_ids": dependencies_by_step.get(item.step_id, []),
                }
                for item in steps
            ],
            "raw_requirement_stored": False,
            "raw_acceptance_stored": False,
            **self._non_action_flags(),
        }

    @staticmethod
    def _plan_public(plan: PlannerPlan) -> dict[str, Any]:
        return {
            "plan_id": plan.plan_id,
            "name": plan.name,
            "task_kind": plan.task_kind,
            "complexity": plan.complexity,
            "risk_level": plan.risk_level,
            "status": plan.status,
            "requirement_sha256": plan.requirement_sha256,
            "acceptance_sha256": plan.acceptance_sha256,
            "policy_version": plan.policy_version,
            "step_count": plan.step_count,
            "knowledge_id": plan.knowledge_id,
            "context_id": plan.context_id,
            "runtime": plan.runtime,
            "agent_profile": plan.agent_profile,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
            "version": plan.version,
        }

    @staticmethod
    def _execution_spec(plan: PlannerPlan, steps: list[PlannerStep]) -> dict[str, Any]:
        return {
            "schema": EXECUTION_SPEC_SCHEMA,
            "plan_id": plan.plan_id,
            "workflow": {
                "name": plan.name,
                "context_id": plan.context_id,
                "stages": [
                    {
                        "stage_key": item.step_key,
                        "title": item.title,
                        "approval_required": item.approval_required,
                        "runtime": plan.runtime,
                        "agent_profile": plan.agent_profile,
                    }
                    for item in steps
                ],
            },
            "caller_must_create_workflow": True,
            "caller_must_create_and_bind_tasks": True,
            "selection_performed": False,
            "dispatch_performed": False,
        }

    @staticmethod
    def _non_action_flags() -> dict[str, bool]:
        return {
            "backend_selected": False,
            "model_selected": False,
            "workflow_created": False,
            "task_created": False,
            "dispatch_performed": False,
            "automatic_knowledge_injection": False,
            "automatic_knowledge_writeback": False,
        }

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _name(value: str) -> str:
        canonical = str(value or "").strip()
        if not _NAME_RE.fullmatch(canonical):
            raise PlannerPolicyError("name must be 1-160 printable characters")
        return canonical

    @staticmethod
    def _requirement(value: str) -> str:
        canonical = str(value or "").strip()
        if not canonical:
            raise PlannerPolicyError("requirement is required")
        if len(canonical) > MAX_REQUIREMENT_CHARS:
            raise PlannerPolicyError("requirement exceeds limit")
        return canonical

    @staticmethod
    def _acceptance(values: Iterable[str] | None) -> list[str]:
        if values is None:
            return []
        if isinstance(values, (str, bytes)):
            raise PlannerPolicyError("acceptance_criteria must be a list of strings")
        result: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise PlannerPolicyError("acceptance_criteria items must be strings")
            item = value.strip()
            if not item or len(item) > MAX_ACCEPTANCE_ITEM_CHARS:
                raise PlannerPolicyError("acceptance criterion is empty or too long")
            result.append(item)
        if len(result) > MAX_ACCEPTANCE_ITEMS:
            raise PlannerPolicyError("acceptance_criteria exceeds item limit")
        return result

    @staticmethod
    def _enum(value: Any, enum_type, field: str) -> str:
        canonical = str(value or "").strip().lower()
        allowed = {item.value for item in enum_type}
        if canonical not in allowed:
            raise PlannerPolicyError(f"{field} must be one of: {', '.join(sorted(allowed))}")
        return canonical

    @staticmethod
    def _id(value: Any, field: str) -> str:
        canonical = str(value or "").strip()
        if not _ID_RE.fullmatch(canonical):
            raise PlannerPolicyError(f"{field} is invalid")
        return canonical

    @classmethod
    def _optional_id(cls, value: Any, field: str) -> str | None:
        canonical = str(value or "").strip()
        return cls._id(canonical, field) if canonical else None

    @staticmethod
    def _optional_text(value: Any, field: str, maximum: int) -> str | None:
        canonical = str(value or "").strip()
        if not canonical:
            return None
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in canonical) or len(canonical) > maximum:
            raise PlannerPolicyError(f"{field} is invalid")
        return canonical

    @staticmethod
    def _strict_bool(value: Any, field: str) -> bool:
        if type(value) is not bool:
            raise PlannerPolicyError(f"{field} must be a boolean")
        return value

    @staticmethod
    def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PlannerPolicyError(f"{field} must be an integer")
        if not minimum <= value <= maximum:
            raise PlannerPolicyError(f"{field} must be between {minimum} and {maximum}")
        return value
