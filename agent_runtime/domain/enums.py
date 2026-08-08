"""Central status and event enums for the durable runtime.

The durable state machine is stable across backend generations.  String values
are stored as-is in SQLite so historical rows remain interpretable even after
a backend leaves the supported TP-Voyager Crew set.
"""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    """Durable task lifecycle status.

    ``cancelling`` keeps the existing callers' semantics: a cancel request
    moves the task into ``cancelling`` and only a backend-acknowledged
    termination moves it to ``cancelled`` (cancel_requested != cancel_confirmed).

    PR3 adds ``lost`` and ``orphaned`` for restart reconciliation: they are
    terminal bookkeeping states meaning the bridge cannot safely determine
    the backend truth (LOST) or found a live local host it cannot rebind
    (ORPHANED).  They are never auto-converted to failed/cancelled.
    """

    QUEUED = "queued"
    CONNECTING = "connecting"
    RUNNING = "running"
    OBSERVING = "observing"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"
    ORPHANED = "orphaned"


TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.LOST,
        TaskStatus.ORPHANED,
    }
)


TERMINAL_STATUS_VALUES = frozenset(status.value for status in TERMINAL_STATUSES)
"""PR3.1: the single string set of terminal statuses.

Every write transaction that must never overwrite a terminal row (cancel
requests, reconciliation writers, public terminal checks) uses this one
set, so LOST/ORPHANED can never be silently missed again.
"""


class TaskRoute(str, Enum):
    """Which transport produced the session backing this task."""

    GATEWAY = "gateway"
    ACP_RESUME = "acp_resume"
    ACP = "acp"
    PRINT = "print"


class TaskType(str, Enum):
    # WORKBUDDY remains a historical persisted value only.
    WORKBUDDY = "workbuddy"
    QODER = "qoder"
    CODEBUDDY = "codebuddy"


class EventType(str, Enum):
    """Append-only audit event types (PR1 minimum set)."""

    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_STATUS_CHANGED = "task_status_changed"
    SESSION_CREATED = "session_created"
    BACKEND_DISPATCH_REQUESTED = "backend_dispatch_requested"
    BACKEND_DISPATCH_ACCEPTED = "backend_dispatch_accepted"
    ACTIVITY_OBSERVED = "activity_observed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCEL_CONFIRMED = "cancel_confirmed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    RESULT_AVAILABLE = "result_available"
    TASK_LOST = "task_lost"
    TASK_ORPHANED = "task_orphaned"
    TASK_CHILD_LINKED = "task_child_linked"


class EventVisibility(str, Enum):
    """Whether an event payload may appear in public projections."""

    PUBLIC = "public"
    INTERNAL = "internal"


class BackendKind(str, Enum):
    """Persisted backend identifiers; legacy values remain readable."""

    WORKBUDDY = "workbuddy"  # historical data compatibility only
    QODER = "qoder"
    CODEBUDDY = "codebuddy"


class EvidenceType(str, Enum):
    """PR4 evidence kinds (stored as-is; validated by the DB CHECK too).

    ``agent_claim`` is the only mandatory bottom-line record: it expresses
    "the agent returned final task material", never "the result is correct".
    """

    AGENT_CLAIM = "agent_claim"
    TEST = "test"
    COMMAND = "command"
    FILE = "file"
    REVIEW = "review"
    ARTIFACT = "artifact"
    USAGE = "usage"


class TrustState(str, Enum):
    """PR4 evidence trust stages (origin and trust_state are independent).

    ``verified_*`` states may only be produced by a Verifier or an explicit
    human verification channel; the PR4 core path must never emit them.
    """

    DECLARED = "declared"
    OBSERVED = "observed"
    VERIFIED_PASSED = "verified_passed"
    VERIFIED_FAILED = "verified_failed"
    NEEDS_REVIEW = "needs_review"
    SKIPPED = "skipped"


class EvidenceOrigin(str, Enum):
    """PR4 evidence provenance: where the fact came from."""

    AGENT = "agent"
    BACKEND = "backend"
    RUNTIME = "runtime"
    VERIFIER = "verifier"
    HUMAN = "human"


class ArtifactOrigin(str, Enum):
    """PR4 artifact declaration provenance (Backend cannot pass storage keys)."""

    AGENT = "agent"
    BACKEND = "backend"
    RUNTIME = "runtime"


class ArtifactKind(str, Enum):
    """PR4 artifact declaration kinds."""

    FILE = "file"
    PATCH = "patch"
    REPORT = "report"
    BUILD = "build"
    LOG = "log"


class CaptureState(str, Enum):
    """PR4 artifact capture lifecycle.

    PR4-B/C produce ``declared`` and ``rejected`` only; ``captured`` and
    ``missing`` are produced by PR4-D physical content ingestion.
    """

    DECLARED = "declared"
    CAPTURED = "captured"
    MISSING = "missing"
    REJECTED = "rejected"


class WorkflowStatus(str, Enum):
    """V1.2 linear workflow control-plane status."""

    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStageStatus(str, Enum):
    """One ordered workflow stage.  Only one stage is ready at a time."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class WorkflowEventType(str, Enum):
    """Append-only workflow audit events (separate from task events)."""

    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_STATUS_CHANGED = "workflow_status_changed"
    STAGE_READY = "stage_ready"
    STAGE_TASK_BOUND = "stage_task_bound"
    STAGE_STATUS_CHANGED = "stage_status_changed"
    APPROVAL_RECORDED = "approval_recorded"


class WorkflowCompletionPolicy(str, Enum):
    LEGACY = "legacy"
    PLAN_EXECUTION_V2 = "plan_execution_v2"


class PlanExecutionStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlanExecutionEventType(str, Enum):
    EXECUTION_CREATED = "execution_created"
    WORKFLOW_CREATED = "workflow_created"
    STEP_MATERIAL_BOUND = "step_material_bound"
    STEP_READY = "step_ready"
    TASK_CREATED = "task_created"
    TASK_BOUND = "task_bound"
    STAGE_ADVANCED = "stage_advanced"
    GATE_BLOCKED = "gate_blocked"
    INPUT_REQUIRED = "input_required"
    EXECUTION_RESUMED = "execution_resumed"
    EXECUTION_CANCEL_REQUESTED = "execution_cancel_requested"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_CANCELLED = "execution_cancelled"


class PlannerTaskKind(str, Enum):
    """V1.6 deterministic planning policy categories."""

    ANALYSIS = "analysis"
    IMPLEMENTATION = "implementation"
    REVIEW = "review"
    DOCUMENTATION = "documentation"
    MAINTENANCE = "maintenance"


class PlannerComplexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PlannerRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PlannerPlanStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    PREPARED = "prepared"


class PlannerStepKind(str, Enum):
    ANALYSIS = "analysis"
    IMPLEMENTATION = "implementation"
    DOCUMENTATION = "documentation"
    VERIFICATION = "verification"
    REVIEW = "review"
    REPORT = "report"


class PlannerEventType(str, Enum):
    PLAN_CREATED = "plan_created"
    PLAN_VALIDATED = "plan_validated"
    PLAN_PREPARED = "plan_prepared"
