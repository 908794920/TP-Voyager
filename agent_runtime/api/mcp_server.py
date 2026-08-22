from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_runtime.activity_log import ActivityLogger
from agent_runtime.configuration import VoyagerUserConfig, VoyagerUserConfigError
from agent_runtime.runtime.backend_callbacks import RuntimeBackendCallbacks
from agent_runtime.api.schemas import CAPTAIN_TOOL_NAMES
from agent_runtime.api.voyager_panel import (
    VOYAGER_PANEL_MIME_TYPE,
    VOYAGER_PANEL_URI,
    VOYAGER_RUNTIME_PROFILE_MIME_TYPE,
    VOYAGER_RUNTIME_PROFILE_URI,
    render_voyager_panel_html,
    render_voyager_runtime_profile_html,
)
from agent_runtime.domain.enums import (
    TERMINAL_STATUS_VALUES,
    EventType,
)
from agent_runtime.domain.ids import new_runtime_session_id, new_task_id
from agent_runtime.domain.lineage import TaskLineage
from agent_runtime.domain.workflow import WorkflowStageSpec
from agent_runtime.domain.session import Session
from agent_runtime.domain.crew_outcome import parse_crew_outcome
from agent_runtime.domain.structured_result import (
    RESULT_SCHEMA,
    StructuredResult,
    StructuredResultParseError,
    parse_structured_result,
)
from agent_runtime.domain.task import Task
from agent_runtime.domain.evidence import Evidence
from agent_runtime.domain.enums import EvidenceOrigin, EvidenceType, TrustState
from agent_runtime.domain.ids import new_evidence_id
from agent_runtime.domain.timeutil import now_epoch
from agent_runtime.verification.artifacts import (
    ArtifactCaptureBatch,
    ArtifactCaptureService,
    WorkspaceBaseline,
    capture_workspace_baseline,
    normalize_backend_result,
)
from agent_runtime.verification.artifacts.normalizer import DeclaredArtifact
from agent_runtime.verification import VerificationPlan, VerificationService
from agent_runtime.persistence.database import Database
from agent_runtime.persistence.runtime_paths import canonical_runtime_home, runtime_database_path
from agent_runtime.persistence.errors import (
    LeaseLostError,
    RuntimePersistenceError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from agent_runtime.runtime.lease import LeaseHeartbeat, LeaseService
from agent_runtime.application.reconciliation_service import (
    ReconciliationService,
)
from agent_runtime.application.task_service import (
    TaskService,
    parse_session_metadata,
)
from agent_runtime.application.task_launch_service import (
    TaskLaunchRequest,
    TaskLaunchService,
)
from agent_runtime.application.workflow_service import (
    WorkflowError,
    WorkflowService,
)
from agent_runtime.application.replay_service import (
    ReplayNotFoundError,
    ReplayService,
)
from agent_runtime.application.capability_service import (
    BackendCapabilityService,
    CapabilityQueryError,
    CapabilityRequirements,
)
from agent_runtime.application.context_service import (
    ContextError,
    ProjectContextService,
)
from agent_runtime.application.knowledge_service import (
    KnowledgeError,
    KnowledgeRuntimeService,
)
from agent_runtime.application.planner_service import (
    PlannerError,
    PlannerService,
)
from agent_runtime.application.plan_execution_service import (
    PlanExecutionError,
    PlanExecutionService,
    PlanStepMaterial,
)
from agent_runtime.application.outcome_service import assess_task_result
from agent_runtime.application.crew import CrewProvider, CrewRegistryService
from agent_runtime.application.crew.routing_profiles import ModelRoutingProfiles
from agent_runtime.application.dispatch import CaptainDispatchService
from agent_runtime.application.dispatch.policy import GlobalDispatchModelPolicy
from agent_runtime.application.dispatch.profiles import (
    TrustedTextError,
    WorkerProfileError,
    WorkerProfileResolver,
    WorkerSkillResolver,
    resolve_trusted_instruction_refs,
)
from agent_runtime.application.dispatch.artifact_inputs import ArtifactInputResolver
from agent_runtime.application.voyage import (
    AgentObservationRecorder,
    AgentObservationStore,
    VoyageAgentProjection,
    VoyageOverviewService,
)
from agent_runtime.domain.dispatch import (
    WORKSPACE_STRATEGIES,
    ApplyReceipt,
    CaptainDispatchRequest,
    CommandSpec,
    ModelParameters,
    ModelPolicy,
    PatchPolicy,
    ReadScope,
    RepositoryResearchSpec,
    RepositorySnapshotRef,
    ScopeSegmentSpec,
    TrustedInstructionRef,
    VerificationPolicy,
    WorkerProfileRef,
    WorkerSkillRef,
    canonical_input_artifact_refs,
)
from agent_runtime.domain.run_control import RunControlSpec
from agent_runtime.backends.codebuddy import CodeBuddyBackend
from agent_runtime.backends.codebuddy.captain_dispatch import CodeBuddyContextReadOnlyDispatcher
from agent_runtime.backends.codebuddy.capability import descriptor as codebuddy_crew_descriptor
from agent_runtime.backends.codebuddy.process import probe_codebuddy_cli
from agent_runtime.backends.codebuddy.model_catalog import list_codebuddy_models
from agent_runtime.backends.qoder.capability import descriptor as qoder_crew_descriptor
from agent_runtime.backends.qoder.account_usage import collect_qoder_account_snapshot
from agent_runtime.backends.qoder.model_catalog import list_qoder_models
from agent_runtime.backends.qoder.process import probe_qoder_cli
from agent_runtime.backends.qoder.captain_dispatch import QoderReadOnlyDispatcher
from agent_runtime.application.dispatch.workspace import (
    PatchWorkspaceCleanupError,
    PatchWorkspaceService,
)
from agent_runtime.application.dispatch.repository_research import (
    RepositoryResearchError,
    RepositoryResearchService,
)
from agent_runtime.verification.subject import (
    VerificationSubjectError,
    VerificationSubjectService,
)
from agent_runtime.application.tool_service import (
    ToolRuntimeError,
    ToolRuntimeService,
)
from agent_runtime.backends.errors import (
    BackendCancelledError,
    BackendError,
    BackendTimeoutError,
)
from agent_runtime.backends.base import (
    BackendActivity,
    BackendCancelRequest,
    BackendResult,
    BackendResumeRequest,
    BackendStartRequest,
    BackendUsage,
    SubAgentBackend,
)
from agent_runtime.backends.qoder import QoderBackend
from agent_runtime.backends.registry import BackendRegistry


ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = canonical_runtime_home() / "runtime" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
_AGENT_OBSERVATION_STORE = AgentObservationStore()
_AGENT_OBSERVATIONS = AgentObservationRecorder(_AGENT_OBSERVATION_STORE)

mcp = FastMCP(
    "tp_voyager",
    instructions=(
        "TP-Voyager: durable execution/control plane for a Captain AI. "
        "Target Crew backends are CodeBuddy CLI and Qoder CLI."
    ),
    log_level="WARNING",
)


_CAPTAIN_TOOL_NAMES = CAPTAIN_TOOL_NAMES


def _mcp_surface() -> str:
    value = str(os.environ.get("TP_VOYAGER_MCP_SURFACE") or "captain").strip().lower()
    return "diagnostic" if value == "diagnostic" else "captain"


def _mcp_tool(**tool_kwargs: Any):
    """Register only Captain tools by default; keep legacy tools callable internally.

    ``TP_VOYAGER_MCP_SURFACE=diagnostic`` restores the complete compatibility
    surface for maintenance.  This is one Runtime with two visibility profiles,
    not a second control plane or state machine.  ``tool_kwargs`` is forwarded
    to FastMCP so the read-only observability tool can attach MCP Apps metadata
    without changing registration behavior for existing tools.
    """
    def decorator(func):
        if _mcp_surface() == "diagnostic" or func.__name__ in _CAPTAIN_TOOL_NAMES:
            return mcp.tool(**tool_kwargs)(func)
        return func
    return decorator


@mcp.resource(
    VOYAGER_PANEL_URI,
    name="TP-Voyager Agent panel",
    title="TP-Voyager Agent",
    description="Read-only current-task Agent presence and execution trace UI.",
    mime_type=VOYAGER_PANEL_MIME_TYPE,
    meta={
        "ui": {
            "prefersBorder": True,
            "csp": {
                "connectDomains": [],
                "resourceDomains": [],
            },
        }
    },
)
def voyager_panel_resource() -> str:
    """Return the self-contained MCP Apps UI resource."""
    return render_voyager_panel_html()


@mcp.resource(
    VOYAGER_RUNTIME_PROFILE_URI,
    name="TP-Voyager Runtime profile",
    title="TP-Voyager 运行与账户",
    description="Read-only current configuration, model catalog, and Crew account status UI.",
    mime_type=VOYAGER_RUNTIME_PROFILE_MIME_TYPE,
    meta={
        "ui": {
            "prefersBorder": True,
            "csp": {"connectDomains": [], "resourceDomains": []},
        }
    },
)
def voyager_runtime_profile_resource() -> str:
    """Return the self-contained Runtime Profile MCP Apps resource."""
    return render_voyager_runtime_profile_html()


from agent_runtime.runtime.handles import (
    IDEMPOTENCY_TASKS,
    TASKS,
    TASKS_LOCK,
    TaskState,
)


# ---------------------------------------------------------------------------
# Durable runtime database (SQLite is the source of truth for task state).
# The in-memory ``TASKS`` dict is only a handle cache for run handles
# (thread / condition / queue / backend connections).
# ---------------------------------------------------------------------------

RUNTIME_DATABASE: Database | None = None
RUNTIME_DATABASE_LOCK = threading.Lock()
_CREW_WORKER_SLOT_LOCK = threading.Lock()
_CREW_ACTIVE_WORKERS = {"qoder": 0, "codebuddy": 0}


def _try_acquire_crew_worker_slot(runtime: str) -> bool:
    config = VoyagerUserConfig.load()
    if runtime == "qoder":
        limit = config.crew.qoder.max_concurrent_tasks
    elif runtime == "codebuddy":
        limit = config.crew.codebuddy.max_concurrent_tasks
    else:
        raise VoyagerUserConfigError(f"unsupported crew runtime for concurrency limit: {runtime}")
    with _CREW_WORKER_SLOT_LOCK:
        if _CREW_ACTIVE_WORKERS[runtime] >= limit:
            return False
        _CREW_ACTIVE_WORKERS[runtime] += 1
        return True


def _release_crew_worker_slot(runtime: str) -> None:
    with _CREW_WORKER_SLOT_LOCK:
        if runtime in _CREW_ACTIVE_WORKERS:
            _CREW_ACTIVE_WORKERS[runtime] = max(0, _CREW_ACTIVE_WORKERS[runtime] - 1)


def _run_worker_with_crew_slot(
    worker_target: Any,
    task: TaskState,
    timeout_seconds: float,
    runtime: str,
) -> None:
    try:
        worker_target(task, timeout_seconds)
    finally:
        _release_crew_worker_slot(runtime)


def _start_worker_thread(thread: threading.Thread) -> None:
    """Start one Runtime-owned worker thread.

    This test seam prevents failure-injection tests from patching
    ``threading.Thread.start`` process-wide.  Artifact baseline capture uses
    subprocess pipe-reader threads, so a global patch can break unrelated
    Python internals before the Runtime worker is created.
    """
    thread.start()


def configure_runtime_database(path: str | Path | None) -> Database | None:
    """Inject an explicit runtime database (tests) or reset to lazy default."""
    global RUNTIME_DATABASE, _RUNTIME_LEASE, _PLAN_EXECUTION_SERVICE
    with RUNTIME_DATABASE_LOCK:
        with _CREW_WORKER_SLOT_LOCK:
            for runtime in _CREW_ACTIVE_WORKERS:
                _CREW_ACTIVE_WORKERS[runtime] = 0
        if path is None:
            RUNTIME_DATABASE = None
            # The lease service binds to the previous database/instance:
            # reset it so the next lookup re-binds to the new database.
            _RUNTIME_LEASE = None
            _PLAN_EXECUTION_SERVICE = None
            return None
        database = Database(path)
        database.initialize()
        RUNTIME_DATABASE = database
        _RUNTIME_LEASE = None
        _PLAN_EXECUTION_SERVICE = None
        return database


def _get_runtime_database() -> Database:
    """Lazily initialize the runtime database; failures are explicit."""
    global RUNTIME_DATABASE
    with RUNTIME_DATABASE_LOCK:
        if RUNTIME_DATABASE is None:
            database = Database(runtime_database_path())
            database.initialize()
            RUNTIME_DATABASE = database
        return RUNTIME_DATABASE


def _runtime_database_or_none() -> Database | None:
    """Return the configured database without forcing lazy initialization."""
    with RUNTIME_DATABASE_LOCK:
        return RUNTIME_DATABASE


def _runtime_service() -> TaskService:
    return TaskService(_get_runtime_database())


def _voyage_agent_projection() -> VoyageAgentProjection:
    return VoyageAgentProjection(_runtime_service(), _AGENT_OBSERVATION_STORE)


def _workflow_service() -> WorkflowService:
    return WorkflowService(_get_runtime_database())


def _replay_service() -> ReplayService:
    return ReplayService(_get_runtime_database())


def _context_service() -> ProjectContextService:
    return ProjectContextService(_get_runtime_database())


def _worker_profile_resolver() -> WorkerProfileResolver:
    try:
        configured = VoyagerUserConfig.load().resources.worker_profiles_root
    except VoyagerUserConfigError as exc:
        raise ValueError("TP-Voyager user config is invalid") from exc
    root = Path(configured).expanduser() if configured else ROOT / "skills" / "tp-voyager-captain" / "worker-profiles"
    return WorkerProfileResolver(root)


def _worker_skill_resolver() -> WorkerSkillResolver:
    try:
        configured = VoyagerUserConfig.load().resources.worker_skills_root
    except VoyagerUserConfigError as exc:
        raise ValueError("TP-Voyager user config is invalid") from exc
    if not configured:
        raise ValueError("resources.worker_skills_root is not configured")
    return WorkerSkillResolver(configured)


def _knowledge_service() -> KnowledgeRuntimeService:
    return KnowledgeRuntimeService(_get_runtime_database())


def _planner_service() -> PlannerService:
    return PlannerService(_get_runtime_database())


def _tool_runtime_service() -> ToolRuntimeService:
    return ToolRuntimeService(_get_runtime_database())


_RUNTIME_LEASE: LeaseService | None = None
_RUNTIME_LEASE_LOCK = threading.Lock()


def _runtime_lease_service() -> LeaseService:
    """Process-wide lease service (one stable instance identity)."""
    global _RUNTIME_LEASE
    if _RUNTIME_LEASE is None:
        with _RUNTIME_LEASE_LOCK:
            if _RUNTIME_LEASE is None:
                _RUNTIME_LEASE = LeaseService(_get_runtime_database())
    return _RUNTIME_LEASE


def _ensure_worker_lease(task: TaskState) -> bool:
    """PR3.1 fast diagnostic: is this worker still the live lease owner?

    PR3.1: this is NOT the fencing guarantee — the real guarantee is the
    lease condition inside every terminal write transaction (see
    ``TaskService.save_result`` / ``update_status`` / ``mark_cancel_confirmed``).
    This check only fails fast for the common case (lease already lost)
    so the worker can record ``persist_error`` before attempting the write.
    """
    lease = task.lease
    if lease is None:
        # Legacy in-process tasks (never leased) keep the historical path.
        return True
    try:
        return _runtime_lease_service().ensure(task.task_id, lease)
    except RuntimePersistenceError:
        return False


def _runtime_service_or_none() -> TaskService | None:
    database = _runtime_database_or_none()
    return TaskService(database) if database is not None else None


def _try_runtime_service() -> TaskService | None:
    """Return a TaskService, trying lazy init on cold start.

    Unlike ``_runtime_service_or_none()``, this tries to auto-initialize
    the default database path so that status/result/list work in a fresh
    process without explicit ``configure_runtime_database()``.

    Returns ``None`` only when there is no database path configured and
    no default path exists (clean first run).  Raises
    ``RuntimePersistenceError`` if the database exists but cannot be
    opened or initialized.
    """
    database = _runtime_database_or_none()
    if database is not None:
        return TaskService(database)
    try:
        return TaskService(_get_runtime_database())
    except RuntimePersistenceError:
        # If the runtime directory doesn't exist at all (clean first run),
        # this is a soft "no DB" — not an error.  If it exists but is
        # corrupted or locked, the exception propagates to callers.
        db_path = runtime_database_path()
        if not db_path.parent.exists():
            return None
        raise


def _crew_registry_service() -> CrewRegistryService:
    """Compose the Captain-facing Crew Registry without creating new state."""
    try:
        tasks = _try_runtime_service()
    except RuntimePersistenceError:
        tasks = None
    return CrewRegistryService(
        {
            "codebuddy": CrewProvider(
                descriptor=codebuddy_crew_descriptor(),
                probe=probe_codebuddy_cli,
                models=list_codebuddy_models,
            ),
            "qoder": CrewProvider(
                descriptor=qoder_crew_descriptor(),
                probe=probe_qoder_cli,
                models=list_qoder_models,
            ),
        },
        task_service=tasks,
        model_policy_loader=lambda: GlobalDispatchModelPolicy.load(
            canonical_runtime_home()
        ),
        routing_profiles_loader=lambda: ModelRoutingProfiles.load(
            canonical_runtime_home()
        ),
    )


def _voyage_overview_service() -> VoyageOverviewService:
    """Project compact Captain progress from existing Durable Task truth."""
    return VoyageOverviewService(_runtime_service())


def _captain_dispatch_service() -> CaptainDispatchService:
    """Compose only Captain-controlled Crew routes accepted by TP-Voyager."""
    launch = _task_launch_service()
    patch_workspaces = PatchWorkspaceService(_get_runtime_database().path.parent / "workspaces")
    return CaptainDispatchService(
        _crew_registry_service(),
        global_model_policy=GlobalDispatchModelPolicy.load(canonical_runtime_home()),
        artifact_loader=lambda refs: tuple(
            item.content
            for item in ArtifactInputResolver(
                _runtime_service(), _get_runtime_database().path.parent / "artifacts"
            ).resolve(refs)
        ),
        dispatchers={
            "codebuddy": CodeBuddyContextReadOnlyDispatcher(
                launch,
                _context_service(),
                patch_workspaces=patch_workspaces,
            ),
            "qoder": QoderReadOnlyDispatcher(
                launch,
                patch_workspaces=patch_workspaces,
            ),
        },
    )


# ---------------------------------------------------------------------------
# Official Crew backend factories.
# ---------------------------------------------------------------------------


_QODER_BACKEND: SubAgentBackend | None = None
_QODER_BACKEND_LOCK = threading.Lock()


def _create_qoder_backend() -> SubAgentBackend:
    global _QODER_BACKEND
    if _QODER_BACKEND is None:
        with _QODER_BACKEND_LOCK:
            if _QODER_BACKEND is None:
                _QODER_BACKEND = QoderBackend()
    return _QODER_BACKEND


def reset_qoder_backend() -> None:
    global _QODER_BACKEND
    with _QODER_BACKEND_LOCK:
        _QODER_BACKEND = None


_CODEBUDDY_BACKEND: SubAgentBackend | None = None
_CODEBUDDY_BACKEND_LOCK = threading.Lock()


def _create_codebuddy_backend() -> SubAgentBackend:
    global _CODEBUDDY_BACKEND
    if _CODEBUDDY_BACKEND is None:
        with _CODEBUDDY_BACKEND_LOCK:
            if _CODEBUDDY_BACKEND is None:
                _CODEBUDDY_BACKEND = CodeBuddyBackend()
    return _CODEBUDDY_BACKEND


def reset_codebuddy_backend() -> None:
    global _CODEBUDDY_BACKEND
    with _CODEBUDDY_BACKEND_LOCK:
        _CODEBUDDY_BACKEND = None


_BACKENDS = BackendRegistry()
_BACKENDS.register("qoder", lambda: _create_qoder_backend())
_BACKENDS.register("codebuddy", lambda: _create_codebuddy_backend())


def _backend_for_runtime(runtime: str) -> SubAgentBackend:
    return _BACKENDS.resolve(runtime)


def _request_fingerprint(
    prompt: str,
    cwd: str,
    model: str,
    reasoning_effort: str,
    identity: str,
    resume_session_id: str,
    review_target: str,
    resume_review: bool,
    extra: dict[str, Any] | None = None,
) -> str:
    """Compute a safe request fingerprint for idempotency conflict detection.

    Routing fields plus a SHA-256 digest of the normalized prompt text (never
    the prompt itself).  Two calls with the same idempotency_key but different
    fingerprints indicate a conflict and must be rejected rather than silently
    returning the original task.
    """
    import hashlib

    normalized_prompt = str(prompt).replace("\r\n", "\n").replace("\r", "\n")
    payload = "|".join(
        [
            str(cwd),
            str(model.strip()),
            str(reasoning_effort.strip()),
            str(identity),
            str(resume_session_id),
            str(review_target),
            str(resume_review),
            hashlib.sha256(normalized_prompt.encode("utf-8")).hexdigest(),
            json.dumps(extra or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _captain_request_contract(**values: Any) -> dict[str, Any]:
    """Content-free canonical Captain contract used for replay decisions."""
    objective = str(values.pop("objective", "")).replace("\r\n", "\n").replace("\r", "\n")
    return {
        "schema": "tp-voyager.captain_request/v1",
        "objective_sha256": hashlib.sha256(objective.encode("utf-8")).hexdigest(),
        **values,
    }


def _note_task_activity(task: TaskState, kind: str) -> None:
    """Record an allow-listed, content-free public task activity event.

    Durable tasks additionally append an ``activity_observed`` audit event for
    lifecycle markers.  Detailed stream observations are persisted separately
    after the observation layer applies its public allow-list.
    """
    if kind not in {
        "task_accepted", "session_created", "prompt_accepted", "running",
        "final_response", "cancel_requested", "process_cancel_requested",
        "process_terminated", "stream_activity", "failed",
    }:
        return
    now = time.time()
    task.activity.append({"kind": kind, "at": now})
    # Keep public status bounded for long-running tasks.
    if len(task.activity) > 20:
        task.activity = task.activity[-20:]
    task.updated_at = now
    if task.first_activity_at is None:
        task.first_activity_at = now
    task.last_activity_at = now
    task.last_activity_kind = kind
    task.event_count += 1
    if kind != "stream_activity" and task.persisted:
        try:
            _runtime_service().append_activity(task.task_id, kind, lease=task.lease)
        except RuntimePersistenceError as exc:
            # Explicit, non-silent durability failure for diagnostics.
            task.persist_error = f"activity event failed: {exc}"


def _persist_observation_activity(task: TaskState, observed: dict[str, Any] | None) -> None:
    """Persist safe stream activity so panels can recover it after restart."""
    if not task.persisted or not isinstance(observed, dict):
        return
    kind = str(observed.get("kind") or "").strip()
    if kind not in {"tool_activity", "file_change", "status"}:
        return
    details = {
        key: observed[key]
        for key in (
            "tool", "action", "path", "phase", "status", "reason", "summary",
            "provider", "source", "currency", "input_tokens", "output_tokens",
            "duration_ms", "turns", "files_changed",
        )
        if key in observed
    }
    try:
        _runtime_service().append_activity(
            task.task_id,
            kind,
            now=float(observed.get("timestamp") or time.time()),
            details=details,
            lease=task.lease,
        )
    except RuntimePersistenceError as exc:
        # Activity is auxiliary telemetry; preserve task truth if its optional
        # durable projection cannot be written.
        task.persist_error = f"activity event failed: {exc}"


from agent_runtime.api.public_projection import (
    public_task as _public,
    result_summary as _result_summary,
)

# ---------------------------------------------------------------------------
# Durable-state bridge: SQLite rows <-> in-process TaskState views.
# ---------------------------------------------------------------------------


def _task_state_from_durable(
    durable: Task,
    service: TaskService,
) -> TaskState:
    """Rebuild a projectable task view from the durable row (restart recovery).

    The view carries no run handles (condition/client/server are inert);
    callers that need a live handle use the TASKS cache instead.
    """
    task = TaskState(
        task_id=durable.task_id,
        prompt="",
        cwd="",
    )
    task.persisted = True
    task.runtime = durable.task_type
    task.version = durable.version
    task.state = durable.status
    task.route = durable.route
    task.resumed = durable.route in {"acp_resume", "acp"}
    task.created_at = durable.created_at
    task.updated_at = durable.updated_at
    task.started_at = durable.started_at
    task.finished_at = durable.finished_at
    task.cancel_requested = durable.cancel_requested_at is not None
    task.cancel_requested_at = durable.cancel_requested_at
    task.cancel_confirmed = durable.cancel_confirmed_at is not None
    task.cancel_confirmed_at = durable.cancel_confirmed_at
    task.session_id = durable.session_id
    task.runtime_session_id = durable.session_id
    # PR4-B1.1: restart/idempotent projections must carry the durable attempt
    # identity, so backend requests rebuilt from this view never use "".
    task.current_attempt_id = durable.current_attempt_id
    task.error = durable.error_message
    task.terminal_reason = durable.terminal_reason or durable.error_code
    task.cancel_scope = durable.cancel_scope or ""
    task.cancel_initiator = durable.cancel_initiator or ""
    task.timeout_reason = durable.timeout_reason
    task.run_id = durable.run_id
    task.step_key = durable.step_key
    task.lost_at = durable.lost_at
    task.orphaned_at = durable.orphaned_at
    task.result_available = durable.result_available
    if durable.result_json:
        try:
            parsed = parse_structured_result(durable.result_json)
            task.result = dict(parsed.raw)
            task.answer = parsed.answer
            # Recover terminal_reason and timeout_reason from result for
            # completed tasks where the durable columns are empty.
            if not task.terminal_reason:
                task.terminal_reason = parsed.stop_reason or None
            if not task.timeout_reason and parsed.legacy:
                task.timeout_reason = str(
                    parsed.raw.get("timeout_reason") or ""
                ) or None
        except StructuredResultParseError:
            task.result_parse_error = True
    session = service.get_session(durable.task_id)
    if session is not None:
        task.backend_session_id = session.backend_session_id
        metadata = parse_session_metadata(session.metadata_json)
        task.cwd = str(metadata.get("cwd") or "")
        task.model = str(metadata.get("model") or "")
        task.reasoning_effort = str(metadata.get("reasoning_effort") or "")
        context_window = metadata.get("context_window_tokens")
        task.context_window_tokens = context_window if isinstance(context_window, int) and not isinstance(context_window, bool) else None
        task.resume_session_id = str(metadata.get("resume_session_id") or "")
        task.idle_timeout_seconds = float(
            metadata.get("idle_timeout_seconds") or 180.0
        )
        task.max_task_duration_seconds = float(
            metadata.get("max_task_duration_seconds") or 1800.0
        )
        task.agent_profile = str(metadata.get("agent_profile") or "") or None
        task.parent_task_id = str(metadata.get("parent_task_id") or "") or None
        task.root_task_id = str(metadata.get("root_task_id") or "") or None
        task.context_id = str(metadata.get("context_id") or "") or None
        task.execution_mode = str(metadata.get("execution_mode") or "background")
        task.verification_plan = (
            dict(metadata.get("verification_plan"))
            if isinstance(metadata.get("verification_plan"), dict) else {}
        )
        task.workspace_baseline = (
            dict(metadata.get("workspace_baseline"))
            if isinstance(metadata.get("workspace_baseline"), dict) else {}
        )
        task.source_cwd = str(metadata.get("source_cwd") or "")
        task.workspace_mode = str(metadata.get("workspace_mode") or "")
        task.workspace_base_revision = str(metadata.get("workspace_base_revision") or "")
        task.patch_policy = dict(metadata.get("patch_policy")) if isinstance(metadata.get("patch_policy"), dict) else {}
        task.routing_metadata = (
            dict(metadata.get("routing_metadata"))
            if isinstance(metadata.get("routing_metadata"), dict) else {}
        )
    lineage = service.get_lineage(durable.task_id)
    if lineage is not None:
        task.parent_task_id = lineage.parent_task_id
        task.root_task_id = lineage.root_task_id
        task.context_id = lineage.context_id
        task.agent_profile = lineage.agent_profile
        task.execution_mode = lineage.execution_mode
    task.activity = service.activity_from_events(durable.task_id)
    task.event_count = len(task.activity)
    return task


def _merge_durable(
    durable: Task,
    handle: TaskState,
    service: TaskService,
) -> TaskState:
    """Project durable state, supplemented by live in-process facts.

    Durable fields are the source of truth; the handle contributes only
    runtime facts it alone can know (activity tail, result, transport ids).
    """
    view = _task_state_from_durable(durable, service)
    view.prompt = handle.prompt
    view.answer = handle.answer or view.answer
    if handle.result is not None:
        view.result = handle.result
    view.condition = handle.condition
    # Session ID fields: runtime_session_id stays as the durable truth;
    # backend_session_id comes from the handle (live backend connection).
    view.backend_session_id = handle.backend_session_id or view.backend_session_id
    view.session_id = view.runtime_session_id
    view.timeout_reason = handle.timeout_reason
    view.first_prompt_accepted_at = handle.first_prompt_accepted_at
    view.first_activity_at = handle.first_activity_at
    view.last_activity_at = handle.last_activity_at
    view.last_activity_kind = handle.last_activity_kind
    view.idle_timeout_seconds = handle.idle_timeout_seconds
    view.max_task_duration_seconds = handle.max_task_duration_seconds
    view.runtime = handle.runtime or view.runtime
    view.parent_task_id = handle.parent_task_id or view.parent_task_id
    view.root_task_id = handle.root_task_id or view.root_task_id
    view.context_id = handle.context_id or view.context_id
    view.source_cwd = handle.source_cwd or view.source_cwd
    view.workspace_mode = handle.workspace_mode or view.workspace_mode
    view.workspace_base_revision = handle.workspace_base_revision or view.workspace_base_revision
    view.patch_policy = dict(handle.patch_policy or view.patch_policy)
    view.routing_metadata = dict(handle.routing_metadata or view.routing_metadata)
    view.agent_profile = handle.agent_profile or view.agent_profile
    view.execution_mode = handle.execution_mode or view.execution_mode
    view.verification_plan = dict(handle.verification_plan or view.verification_plan)
    view.workspace_baseline = dict(handle.workspace_baseline or view.workspace_baseline)
    view.persist_error = handle.persist_error
    if handle.activity:
        view.activity = list(handle.activity)
        view.event_count = handle.event_count
    if handle.error is not None:
        view.error = handle.error
    if handle.terminal_reason is not None:
        view.terminal_reason = handle.terminal_reason
    return view


def _load_task(task_id: str) -> TaskState | None:
    """Resolve a task view: durable state first, handle cache as fallback.

    Legacy (non-persisted) in-process tasks keep working for callers that
    constructed them directly; persisted tasks always project from SQLite.

    Raises ``RuntimePersistenceError`` if a persisted task's durable state
    cannot be read from SQLite (P0-2: no silent fallback to handle).
    """
    with TASKS_LOCK:
        handle = TASKS.get(task_id)
    # Cold-start: try lazy init so a fresh process can read existing DB.
    service = _try_runtime_service()
    if service is not None:
        try:
            durable = service.get_task(task_id)
        except RuntimePersistenceError:
            # For persisted tasks, SQLite failure is an error, not a fallback.
            if handle is not None and handle.persisted:
                raise
            durable = None
        if durable is not None:
            if handle is not None:
                return _merge_durable(durable, handle, service)
            return _task_state_from_durable(durable, service)
    if handle is not None:
        return handle
    return None


# ---------------------------------------------------------------------------
# Durable write helpers used by official Crew workers and public cancel.
# Every state change persists status + event in one transaction.
# ---------------------------------------------------------------------------


def _persist_status_change(
    task: TaskState,
    *,
    status: str,
    event_type: str,
    started_at: float | None = None,
    finished_at: float | None = None,
    session_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    terminal_reason: str | None = None,
    timeout_reason: str | None = None,
    cancel_scope: str | None = None,
    cancel_initiator: str | None = None,
) -> None:
    """Persist one status change; raises BackendError on durability failure.

    A concurrent cancel request (``task_cancel``) may bump the row
    version while the worker advances status; when the durable row already
    carries ``cancel_requested_at``, the cancel wins — the stale status
    advance is skipped and the handle version is re-synced so the pending
    backend terminal signal can be persisted without a phantom conflict.
    """
    if not task.persisted:
        return
    try:
        _runtime_service().update_status(
            task.task_id,
            status=status,
            event_type=event_type,
            version=task.version,
            started_at=started_at,
            finished_at=finished_at,
            session_id=session_id,
            error_code=error_code,
            error_message=error_message,
            terminal_reason=terminal_reason,
            timeout_reason=timeout_reason,
            cancel_scope=cancel_scope,
            cancel_initiator=cancel_initiator,
            lease=task.lease,
        )
        task.version += 1
    except TaskVersionConflictError as exc:
        # Only a version conflict may be resolved by cancel-wins: a concurrent
        # cancel request bumped the row version while the worker advanced
        # status.  When the durable row carries ``cancel_requested_at`` the
        # cancel wins — skip the stale advance and re-sync the handle version.
        durable = _durable_cancel_snapshot(task.task_id)
        if durable is not None and durable.cancel_requested_at is not None:
            task.version = durable.version
            return
        task.persist_error = str(exc)
        raise BackendError(f"runtime persistence failed: {exc}") from exc
    except RuntimePersistenceError as exc:
        # Any other persistence failure must stay explicit (no cancel-wins).
        task.persist_error = str(exc)
        raise BackendError(f"runtime persistence failed: {exc}") from exc


def _durable_cancel_snapshot(task_id: str) -> Task | None:
    """Read the durable row; None when the database is unavailable."""
    try:
        return _runtime_service().get_task(task_id)
    except RuntimePersistenceError:
        return None


def _retire_patch_workspace_before_completion(task: TaskState) -> None:
    """Retire an isolated patch worktree before terminal success is durable.

    ``completed`` is a public contract.  For patch tasks it means both the
    work product *and* the Runtime-owned isolation workspace are finished.
    Cleanup failure therefore fails the task rather than being hidden behind a
    successful terminal state.
    """
    if task.workspace_mode not in {"patch_worktree", "verification_worktree"} or not task.source_cwd or not task.cwd:
        return
    root = _get_runtime_database().path.parent / "workspaces"
    PatchWorkspaceService(root).cleanup(task.cwd, source_root=task.source_cwd)


def _retire_failed_patch_workspace_before_terminal(task: TaskState) -> str | None:
    """Best-effort synchronous retirement before a failed patch becomes visible.

    Failure finalization captures/verification work first, then calls this
    helper before persisting terminal ``failed``.  Two bounded attempts absorb
    transient cleanup failures while preserving explicit attention when the
    workspace genuinely cannot be retired.
    """
    if task.workspace_mode not in {"patch_worktree", "verification_worktree"} or not task.source_cwd or not task.cwd:
        return None
    root = _get_runtime_database().path.parent / "workspaces"
    service = PatchWorkspaceService(root)
    last_error: PatchWorkspaceCleanupError | None = None
    for _ in range(2):
        try:
            service.cleanup(task.cwd, source_root=task.source_cwd)
            return None
        except PatchWorkspaceCleanupError as exc:
            last_error = exc
    return type(last_error).__name__ if last_error is not None else "PatchWorkspaceCleanupError"


def _is_captain_read_only_task(task: TaskState) -> bool:
    """True for Captain routes that cannot own source-code mutations.

    Verification commands may create disposable build/test outputs, but those
    changes are never attributed as Crew patch artifacts.
    """
    return task.route in {"sdk_context_read_only", "acp_read_only", "sdk_verify", "acp_verify"} and task.workspace_mode != "patch_worktree"




def _trusted_instruction_roots() -> dict[str, str]:
    """Load trusted instruction aliases from the unified user config."""
    try:
        return VoyagerUserConfig.load().trusted_roots.instructions_map()
    except VoyagerUserConfigError as exc:
        raise ValueError("TP-Voyager user config is invalid") from exc


def _repository_research_captain_fingerprint(
    *,
    objective: str,
    crew: str,
    task_kind: str,
    model: str,
    model_parameters: ModelParameters | None,
    access_mode: str,
    timeout_seconds: int,
    read_scope: ReadScope,
    model_policy: ModelPolicy | None,
    worker_profile_ref: WorkerProfileRef | None,
    correlation_id: str,
    presentation_group_id: str,
    required_capabilities: list[str] | None,
    repository_research: RepositoryResearchSpec,
    repository_snapshot_ref: RepositorySnapshotRef | None = None,
    scope_segment: ScopeSegmentSpec | None = None,
) -> str:
    """Hash Captain-owned repository_research inputs for safe outer replay.

    Acquisition creates the target directory before the shared durable launcher
    reaches its normal idempotency gate.  Persisting this content-free hash in
    routing metadata lets a repeated identical Captain request replay without
    cloning again, while still rejecting a reused key with different inputs.
    """
    payload = {
        "objective": str(objective or ""),
        "crew": str(crew or "").strip().lower(),
        "task_kind": str(task_kind or "").strip().lower(),
        "model": str(model or "").strip(),
        "model_parameters": model_parameters.to_dict() if model_parameters is not None else None,
        "access_mode": str(access_mode or "read_only").strip().lower(),
        "timeout_seconds": int(timeout_seconds),
        "read_scope": read_scope.to_dict(),
        "model_policy": model_policy.to_dict() if model_policy is not None else None,
        "worker_profile_ref": worker_profile_ref.to_dict() if worker_profile_ref is not None else None,
        "correlation_id": str(correlation_id or ""),
        "presentation_group_id": str(presentation_group_id or ""),
        "required_capabilities": sorted(
            {str(item).strip() for item in (required_capabilities or []) if str(item).strip()}
        ),
        "repository_research": repository_research.to_dict(),
        "repository_snapshot_ref": repository_snapshot_ref.to_dict() if repository_snapshot_ref is not None else None,
        "scope_segment": (scope_segment or ScopeSegmentSpec()).to_dict(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _repository_research_report_declaration(
    task: TaskState, answer: str,
) -> DeclaredArtifact | None:
    """Write the Crew answer as the sole Runtime-owned read-only report artifact."""
    routing = task.routing_metadata if isinstance(task.routing_metadata, dict) else {}
    research = routing.get("repository_research")
    if not isinstance(research, dict):
        return None
    report_path = str(research.get("report_path") or "").strip().replace("\\", "/")
    if not report_path.startswith("reports/") or not report_path.lower().endswith((".md", ".txt")):
        raise BackendError("repository_research report_path became invalid before finalization")
    root = Path(task.cwd).resolve()
    target = (root / Path(*report_path.split("/"))).resolve()
    try:
        target.relative_to(root / "reports")
    except ValueError as exc:
        raise BackendError("repository_research report escaped reports directory") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    source_url = str(research.get("url") or "")
    commit = str(research.get("commit") or "")
    body = (
        "# TP-Voyager Repository Research Report\n\n"
        f"- Source: `{source_url}`\n"
        f"- Commit: `{commit}`\n"
        "- Execution: bounded static read-only Crew research\n\n"
        "---\n\n"
        f"{str(answer or '').strip()}\n"
    )
    encoded = body.encode("utf-8")
    if len(encoded) > 2 * 1024 * 1024:
        encoded = encoded[: 2 * 1024 * 1024].decode("utf-8", "ignore").encode("utf-8")
        encoded += b"\n\n[TP-Voyager report artifact truncated at 2 MiB]\n"
    target.write_bytes(encoded)
    return DeclaredArtifact(
        path=report_path,
        kind="report",
        name=target.name,
        metadata={"source": "runtime_repository_research_report", "role": "research_report"},
    )
def _observability_evidence(
    task: TaskState, attempt_id: str, observability: dict[str, Any],
) -> list[Evidence]:
    """Convert bounded backend observability facts into immutable Evidence.

    Only metadata already normalized by the backend adapters is admitted.
    Prompt/answer/reasoning/file contents are never persisted here.
    """
    now = now_epoch()
    output: list[Evidence] = []
    usage_provenance = observability.get("usage_provenance")
    if isinstance(usage_provenance, dict):
        safe = {
            "status": str(usage_provenance.get("status") or "unknown")[:80],
            "event_count": int(usage_provenance.get("event_count") or 0),
            "events": [],
        }
        raw_events = usage_provenance.get("events")
        if isinstance(raw_events, list):
            for item in raw_events[:64]:
                if not isinstance(item, dict):
                    continue
                safe["events"].append({
                    "type": str(item.get("type") or "")[:80],
                    "keys": [str(key)[:80] for key in list(item.get("keys") or [])[:32]],
                    "timestamp": item.get("timestamp"),
                    "size_bytes": item.get("size_bytes"),
                })
        output.append(Evidence(
            evidence_id=new_evidence_id(), task_id=task.task_id, attempt_id=attempt_id,
            evidence_type=EvidenceType.REVIEW.value, trust_state=TrustState.OBSERVED.value,
            origin=EvidenceOrigin.BACKEND.value, summary="Qoder usage protocol provenance observed",
            detail_json=json.dumps(safe, ensure_ascii=False, sort_keys=True),
            captured_at=now, created_at=now,
        ))
    raw_access = observability.get("file_access_events")
    if isinstance(raw_access, list):
        for item in raw_access[:256]:
            if not isinstance(item, dict):
                continue
            safe = {
                "path": str(item.get("path") or "")[:512],
                "operation": str(item.get("operation") or "")[:40],
                "allowed": bool(item.get("allowed")),
                "reason": str(item.get("reason") or "")[:160] or None,
                "timestamp": item.get("timestamp"),
                "sha256": str(item.get("sha256") or "")[:64] or None,
            }
            output.append(Evidence(
                evidence_id=new_evidence_id(), task_id=task.task_id, attempt_id=attempt_id,
                evidence_type=EvidenceType.FILE.value, trust_state=TrustState.OBSERVED.value,
                origin=EvidenceOrigin.BACKEND.value,
                summary=("Qoder file access allowed" if safe["allowed"] else "Qoder file access denied"),
                detail_json=json.dumps(safe, ensure_ascii=False, sort_keys=True),
                captured_at=now, created_at=now,
            ))
    subject = task.routing_metadata.get("verification_subject") if isinstance(task.routing_metadata, dict) else None
    if isinstance(subject, dict):
        safe_subject = {
            key: subject.get(key)
            for key in (
                "repository_identity", "base_commit", "base_tree_hash", "patch_artifact_id",
                "patch_sha256", "result_tree_hash", "apply_receipt_sha256",
                "context_id", "context_root_hash",
            )
            if subject.get(key) is not None
        }
        output.append(Evidence(
            evidence_id=new_evidence_id(), task_id=task.task_id, attempt_id=attempt_id,
            evidence_type=EvidenceType.REVIEW.value, trust_state=TrustState.OBSERVED.value,
            origin=EvidenceOrigin.RUNTIME.value, summary="Apply Receipt reconstructed as exact verification subject",
            detail_json=json.dumps(safe_subject, ensure_ascii=False, sort_keys=True),
            captured_at=now, created_at=now,
        ))
    return output


def _persist_completed(
    task: TaskState,
    result: dict[str, Any],
    *,
    backend_result: BackendResult | None = None,
) -> None:
    """Capture artifacts, run deterministic verification, then finalize once.

    Backend declarations are normalized but never trusted.  Physical artifact
    hashes and command exit codes are produced by the runtime.  Result,
    Artifact rows, Evidence rows and terminal events are committed in the
    existing single fenced transaction.
    """
    if not task.persisted:
        return
    if not _ensure_worker_lease(task):
        task.persist_error = "lease lost; terminal write refused"
        return
    output = dict(
        backend_result.result
        if backend_result is not None and isinstance(backend_result.result, dict)
        else result
    )
    backend = (
        backend_result.backend
        if backend_result is not None
        else str(result.get("backend") or task.runtime or task.route or "")
    )
    stop_reason = (
        backend_result.stop_reason
        if backend_result is not None
        else str(result.get("stopReason") or task.terminal_reason or "")
    )
    observability = (
        dict(backend_result.observability)
        if backend_result is not None
        else dict(result.get("observability") or {})
        if isinstance(result.get("observability"), dict)
        else {}
    )
    title = backend_result.title if backend_result is not None else ""
    normalized = normalize_backend_result(output)
    plan = VerificationPlan.from_dict(task.verification_plan)
    baseline = WorkspaceBaseline.from_dict(task.workspace_baseline)
    captain_read_only = _is_captain_read_only_task(task)
    # A read-only Crew cannot own workspace changes.  Ignore vendor-declared
    # file/patch metadata so pre-existing dirty source state is never
    # attributed to this Attempt.  Runtime-generated research reports are
    # added explicitly later by the repository-research contract.
    declarations = [] if captain_read_only else list(normalized.artifacts)
    research_report = (
        _repository_research_report_declaration(task, backend_result.answer if backend_result is not None else task.answer or str(result.get("answer") or ""))
        if captain_read_only else None
    )
    if research_report is not None:
        declarations.append(research_report)
    for expected in plan.expected_artifacts:
        if not any(item.path == expected for item in declarations):
            declarations.append(
                DeclaredArtifact(
                    path=expected,
                    kind="report" if expected.lower().endswith((".md", ".txt")) else "file",
                    name=Path(expected).name,
                    metadata={"source": "verification_plan"},
                )
            )

    attempt_id = task.current_attempt_id or ""
    database = _get_runtime_database()
    artifact_store = database.path.parent / "artifacts"
    effective_workspace = task.cwd if captain_read_only else (baseline.git_root or task.cwd)
    capture_risks: list[str] = []
    try:
        capture = ArtifactCaptureService(artifact_store).capture(
            task_id=task.task_id,
            attempt_id=attempt_id,
            cwd=effective_workspace,
            declarations=declarations,
            baseline=baseline,
            observe_git=not captain_read_only,
        )
    except (OSError, RuntimeError, ValueError):
        capture = ArtifactCaptureBatch(baseline_dirty=baseline.dirty)
        capture_risks.append("artifact_capture_failed")

    verification = VerificationService().verify(
        task_id=task.task_id,
        attempt_id=attempt_id,
        cwd=effective_workspace,
        plan=plan,
        capture=capture,
        baseline=baseline,
    )
    risks = list(dict.fromkeys([
        *normalized.risks,
        *capture_risks,
        *verification.risks,
    ]))
    usage = dict(output.get("usage") or {}) if isinstance(output.get("usage"), dict) else {}
    structured_result = StructuredResult(
        schema=RESULT_SCHEMA,
        attempt_id=attempt_id,
        answer=(
            backend_result.answer
            if backend_result is not None
            else task.answer or str(result.get("answer") or "")
        ),
        backend=backend,
        stop_reason=stop_reason,
        title=title,
        reasoning_effort_requested=(task.reasoning_effort or "").strip() or None,
        reasoning_effort_applied=(
            result.get("reasoning_effort_applied")
            if isinstance(result.get("reasoning_effort_applied"), bool)
            else None
        ),
        context_window_tokens_requested=task.context_window_tokens,
        context_window_tokens_applied=(
            result.get("context_window_tokens_applied")
            if isinstance(result.get("context_window_tokens_applied"), bool)
            else None
        ),
        observability=observability,
        output=output,
        changed_files=[] if captain_read_only else list(capture.changed_files or normalized.changed_files),
        tests=[*normalized.tests, *verification.tests],
        artifacts=capture.public_artifacts(),
        risks=risks,
        claims=list(normalized.claims),
        verification=verification.to_dict(),
        usage=usage,
        crew_outcome=parse_crew_outcome(
            backend_result.answer if backend_result is not None else task.answer or str(result.get("answer") or "")
        ),
    )
    # Patch completion is not externally visible until the isolated worktree
    # has been retired.  This closes the race where status/result became
    # durable before ``finally`` removed ``runtime/workspaces/patch-*``.
    try:
        _retire_patch_workspace_before_completion(task)
    except PatchWorkspaceCleanupError:
        capture.cleanup_orphans()
        raise

    extra_evidence = _observability_evidence(task, attempt_id, observability)
    try:
        _runtime_service().save_result(
            task.task_id,
            structured_result=structured_result,
            initial_evidence=[*verification.evidence, *extra_evidence],
            artifact_declarations=capture.artifacts,
            status="completed",
            version=task.version,
            terminal_reason=task.terminal_reason,
            timeout_reason=task.timeout_reason,
            lease=task.lease,
            metadata_rejected_count=(normalized.rejected_count + (len(normalized.changed_files) + len(normalized.artifacts) if captain_read_only else 0)),
        )
        task.version += 2
        task.result_available = True
        task.result = structured_result.to_dict()
    except LeaseLostError:
        capture.cleanup_orphans()
        task.persist_error = "lease lost; terminal write refused"
    except RuntimePersistenceError as exc:
        capture.cleanup_orphans()
        task.persist_error = str(exc)
        raise BackendError(f"runtime persistence failed: {exc}") from exc


def _persist_cancel_confirmed(task: TaskState) -> None:
    """Persist backend-acknowledged cancellation (never fabricated).

    PR3.1: confirmation + cancelled status land in one transaction fenced on
    the session lease; a stale worker can never confirm a cancel over a
    newer owner's truth.
    """
    if not task.persisted:
        return
    if not _ensure_worker_lease(task):
        task.persist_error = "lease lost; terminal write refused"
        return
    try:
        _runtime_service().mark_cancel_confirmed(
            task.task_id,
            status="cancelled",
            version=task.version,
            terminal_reason="cancelled",
            lease=task.lease,
        )
        task.version += 2
    except LeaseLostError:
        task.persist_error = "lease lost; terminal write refused"
    except RuntimePersistenceError as exc:
        task.persist_error = str(exc)
        raise BackendError(f"runtime persistence failed: {exc}") from exc


def _persist_failed_with_partial_artifacts(task: TaskState) -> bool:
    """Finalize a failed attempt while preserving deterministic work facts.

    Backend execution truth and work-product truth are independent.  The Task
    remains ``failed`` whenever the Backend did not produce a terminal result.
    For an idle/stream-closed timeout only, the Runtime may still execute the
    caller's *explicit* verification plan against captured workspace changes.
    A PASSED verification is persisted as evidence but never auto-promotes the
    Task to ``completed``; callers must use the read-only assessment endpoint
    and make an explicit acceptance/retry decision.

    Other failure types keep the original ``NOT_RUN_EXECUTION_FAILED``
    behavior so an arbitrary Backend exception cannot unexpectedly execute
    commands after failure.
    """
    if not task.persisted:
        return True
    if not _ensure_worker_lease(task):
        task.persist_error = "lease lost; terminal write refused"
        return True

    attempt_id = task.current_attempt_id or ""
    baseline = WorkspaceBaseline.from_dict(task.workspace_baseline)
    captain_read_only = _is_captain_read_only_task(task)
    effective_workspace = task.cwd if captain_read_only else (baseline.git_root or task.cwd)
    raw_output = task.result if isinstance(task.result, dict) else {}
    normalized = normalize_backend_result(raw_output)
    plan = VerificationPlan.from_dict(task.verification_plan)
    declarations = [] if captain_read_only else list(normalized.artifacts)
    for expected in plan.expected_artifacts:
        if not any(item.path == expected for item in declarations):
            declarations.append(
                DeclaredArtifact(
                    path=expected,
                    kind=(
                        "report"
                        if expected.lower().endswith((".md", ".txt"))
                        else "file"
                    ),
                    name=Path(expected).name,
                    metadata={"source": "verification_plan"},
                )
            )

    database = _get_runtime_database()
    artifact_store = database.path.parent / "artifacts"
    capture = ArtifactCaptureBatch(baseline_dirty=baseline.dirty)
    capture_risks: list[str] = []
    try:
        capture = ArtifactCaptureService(artifact_store).capture(
            task_id=task.task_id,
            attempt_id=attempt_id,
            cwd=effective_workspace,
            declarations=declarations,
            baseline=baseline,
            observe_git=not captain_read_only,
        )
    except (OSError, RuntimeError, ValueError):
        capture_risks.append("artifact_capture_failed")

    timeout_can_verify = task.timeout_reason in {
        "idle_timeout",
        "stream_closed_without_terminal",
    }
    post_failure_verification_run = bool(timeout_can_verify and plan.requested)
    post_failure_verification_completed = False
    verification_evidence = []
    verification_tests: list[dict[str, Any]] = []
    verification_risks: list[str] = []
    if post_failure_verification_run:
        try:
            verification_report = VerificationService().verify(
                task_id=task.task_id,
                attempt_id=attempt_id,
                cwd=effective_workspace,
                plan=plan,
                capture=capture,
                baseline=baseline,
            )
            verification = verification_report.to_dict()
            verification_evidence = verification_report.evidence
            verification_tests = verification_report.tests
            verification_risks = verification_report.risks
            post_failure_verification_completed = True
        except (OSError, RuntimeError, ValueError):
            # Failure finalization must itself remain durable.  Do not expose
            # the raw verifier exception and do not guess pass/fail when the
            # deterministic plan could not complete.
            verification = {
                "status": "NEEDS_REVIEW",
                "checks": [
                    {
                        "name": "post_failure_verification",
                        "status": "needs_review",
                        "summary": "Post-failure verification could not complete",
                    }
                ],
                "summary": {
                    "passed": 0,
                    "failed": 0,
                    "needs_review": 1,
                    "total": 1,
                },
            }
            verification_risks = ["post_failure_verification_error"]
    else:
        verification = {
            "status": "NOT_RUN_EXECUTION_FAILED",
            "checks": [],
            "summary": {
                "passed": 0,
                "failed": 0,
                "needs_review": 0,
                "total": 0,
            },
        }

    patch_cleanup_error = _retire_failed_patch_workspace_before_terminal(task)

    verification_status = str(verification.get("status") or "").upper()
    work_product_status = {
        "PASSED": "verified",
        "FAILED": "rejected",
        "NEEDS_REVIEW": "needs_review",
    }.get(verification_status, "unverified")
    outcome_risks: list[str] = ["execution_failed"]
    if task.terminal_reason == "PatchWorkspaceCleanupError":
        outcome_risks.append("patch_workspace_cleanup_failed")
    if patch_cleanup_error is not None:
        outcome_risks.append("patch_workspace_cleanup_requires_attention")
    if timeout_can_verify:
        outcome_risks.append("backend_terminal_not_observed")
        if verification_status == "PASSED":
            outcome_risks.append("backend_timeout_with_verified_work_product")
        elif verification_status == "NEEDS_REVIEW":
            outcome_risks.append("backend_timeout_work_product_needs_review")
        elif verification_status == "FAILED":
            outcome_risks.append("backend_timeout_work_product_rejected")

    risks = list(dict.fromkeys([
        *normalized.risks,
        *capture_risks,
        *verification_risks,
        *outcome_risks,
        *(["workspace_was_dirty_before_dispatch"] if baseline.dirty else []),
    ]))
    structured_result = StructuredResult(
        schema=RESULT_SCHEMA,
        attempt_id=attempt_id,
        answer=task.answer or "",
        backend=task.runtime or task.route or "",
        stop_reason=task.terminal_reason or "execution_failed",
        title="",
        reasoning_effort_requested=(task.reasoning_effort or "").strip() or None,
        reasoning_effort_applied=None,
        context_window_tokens_requested=task.context_window_tokens,
        context_window_tokens_applied=None,
        observability={},
        output={
            "partial": True,
            "failure_type": task.terminal_reason or "RuntimeError",
            "execution_outcome": "backend_timeout" if timeout_can_verify else "backend_failure",
            "timeout_reason": task.timeout_reason,
            "backend_terminal_observed": False,
            "post_failure_verification_run": post_failure_verification_run,
            "post_failure_verification_completed": post_failure_verification_completed,
            "work_product_status": work_product_status,
            # These counts are Runtime-observed facts, not Backend claims.
            # The read-only assessment endpoint uses only these values when
            # the Backend terminal response was lost, preventing claimed
            # files/tests from being mistaken for captured evidence.
            "runtime_observed_changed_file_count": len(capture.changed_files),
            "runtime_captured_artifact_count": sum(
                1
                for item in capture.artifacts
                if item.capture_state == "captured"
            ),
            "runtime_verified_test_count": len(verification_tests),
        },
        changed_files=[] if captain_read_only else list(capture.changed_files or normalized.changed_files),
        tests=[*normalized.tests, *verification_tests],
        artifacts=capture.public_artifacts(),
        risks=risks,
        claims=list(normalized.claims),
        verification=verification,
        usage={},
    )
    try:
        _runtime_service().save_result(
            task.task_id,
            structured_result=structured_result,
            initial_evidence=verification_evidence,
            artifact_declarations=capture.artifacts,
            status="failed",
            version=task.version,
            error_code=task.terminal_reason or "RuntimeError",
            error_message=task.error or "unknown runtime failure",
            terminal_reason=task.terminal_reason,
            timeout_reason=task.timeout_reason,
            lease=task.lease,
            metadata_rejected_count=(normalized.rejected_count + (len(normalized.changed_files) + len(normalized.artifacts) if captain_read_only else 0)),
        )
        task.version += 2
        task.result_available = True
        task.result = structured_result.to_dict()
        return True
    except LeaseLostError:
        capture.cleanup_orphans()
        task.persist_error = "lease lost; terminal write refused"
        return True
    except RuntimePersistenceError as exc:
        capture.cleanup_orphans()
        task.persist_error = str(exc)
        return False


def _persist_failed(task: TaskState) -> bool:
    """Persist the failed status; never re-raises (already in error handling).

    Returns ``True`` if the durable state was written successfully,
    ``False`` if the database write itself failed.  A lost lease (the
    task was reconciled away) counts as handled: the newer truth stands.
    PR3.1: the failed transition is fenced on the session lease inside the
    same transaction; ``_ensure_worker_lease`` is a fast diagnostic only.
    """
    if not task.persisted:
        return True
    if not _ensure_worker_lease(task):
        task.persist_error = "lease lost; terminal write refused"
        return True
    try:
        _runtime_service().update_status(
            task.task_id,
            status="failed",
            event_type=EventType.TASK_FAILED.value,
            version=task.version,
            finished_at=time.time(),
            error_code=task.terminal_reason or "RuntimeError",
            error_message=task.error or "unknown runtime failure",
            terminal_reason=task.terminal_reason,
            timeout_reason=task.timeout_reason,
            lease=task.lease,
        )
        task.version += 1
        return True
    except LeaseLostError:
        task.persist_error = "lease lost; terminal write refused"
        return True
    except RuntimePersistenceError as exc:
        task.persist_error = str(exc)
        return False


def _make_backend_callbacks(
    task: TaskState,
    log_event: Any,
) -> RuntimeBackendCallbacks:
    """Build the production BackendCallbacks adapter for one task.

    The backend may only send a real prompt/stream after
    ``on_dispatch_accepted`` returns; a durability failure there raises and
    aborts the dispatch inside the route.
    """

    def on_dispatch_accepted(backend_session_id: str) -> None:
        if task.persisted:
            try:
                _runtime_service().accept_backend_dispatch(
                    task.task_id,
                    backend_session_id=backend_session_id,
                    version=task.version,
                    lease=task.lease,
                )
            except RuntimePersistenceError as exc:
                task.persist_error = str(exc)
                raise BackendError(f"runtime dispatch gate failed: {exc}") from exc
        task.backend_session_id = backend_session_id
        _note_task_activity(task, "session_created")

    def on_activity(activity: BackendActivity) -> None:
        kind = activity.kind
        if kind == "prompt_accepted":
            task.first_prompt_accepted_at = time.time()
            # Prompt accepted: advance the durable lifecycle to observing so
            # Gateway and ACP follow the same sequence (running -> prompt
            # accepted -> observing -> terminal).  Never write observing
            # before the backend really accepted the dispatch.
            task.state = "observing"
            _persist_status_change(
                task,
                status="observing",
                event_type=EventType.TASK_STATUS_CHANGED.value,
            )
        _note_task_activity(task, activity.kind)
        observed = _AGENT_OBSERVATIONS.activity(task, activity)
        _persist_observation_activity(task, observed)

    def on_usage(usage: BackendUsage) -> None:
        _AGENT_OBSERVATIONS.usage(task, usage)
        if not task.persisted:
            return
        try:
            _runtime_service().append_usage_evidence(
                task.task_id,
                usage=usage.to_dict(),
                lease=task.lease,
            )
        except (LeaseLostError, RuntimePersistenceError, TaskNotFoundError, ValueError):
            # Usage is auxiliary provider evidence.  Never alter task truth,
            # patch cleanup, or terminal persistence because usage capture
            # itself could not be durably appended.
            return

    def on_result(result: Any) -> None:
        # The backend produced its final result; the runtime is now
        # finalizing (history sync + terminal persistence).  Mark the
        # execution finished so cancels are rejected from this point on.
        # PR3.5: the finalization flags publish under the SAME ownership
        # lock the cancel path uses, so "finalizing already published" and
        # "cancel already accepted" are linearized — a cancel that already
        # observed finalizing=false can never race ahead of a concurrent
        # on_result that publishes first.
        with task.ownership_lock:
            task.execution_finished = True
            task.finalizing = True

    return RuntimeBackendCallbacks(
        on_dispatch_accepted=on_dispatch_accepted,
        on_activity=on_activity,
        on_usage=on_usage,
        on_result=on_result,
        on_raw_event=log_event,
    )


def _run_official_cli_task(
    task: TaskState,
    timeout_seconds: float,
    *,
    backend_name: str,
    backend_factory: Any,
    cancel_scope_for_route: Any,
) -> None:
    """Run an official CLI backend through the proven durable Task lifecycle.

    This is execution plumbing only: vendor transport/session behavior remains
    inside the backend adapter.  Keeping one lifecycle prevents CodeBuddy and
    Qoder from growing parallel Task state machines.
    """
    del timeout_seconds  # duration truth is already carried on TaskState
    log_path = LOG_DIR / f"{task.task_id}.jsonl"
    activity_logger = ActivityLogger(log_path, task.cwd, task_id=task.task_id)
    heartbeat: LeaseHeartbeat | None = None

    def log_event(event: dict[str, Any]) -> None:
        activity_logger.feed(event)
        task.updated_at = time.time()

    try:
        backend = backend_factory()
        if task.persisted:
            with task.ownership_lock:
                task.lease = _runtime_lease_service().acquire(task.task_id)
                task.lease_acquire_finished = True
            if task.lease is None:
                task.persist_error = "session lease held by another instance"
                return

            def on_lost() -> None:
                try:
                    backend.cancel(
                        BackendCancelRequest(
                            task_id=task.task_id,
                            attempt_id=task.current_attempt_id or "",
                            backend_session_id=task.backend_session_id or "",
                            cancel_scope="lease_lost",
                        )
                    )
                except Exception:
                    pass

            def on_heartbeat_error(error_type: str) -> None:
                task.persist_error = f"lease heartbeat failed: {error_type}"
                on_lost()

            heartbeat = LeaseHeartbeat(
                _runtime_lease_service(),
                task.task_id,
                task.lease,
                on_lost=on_lost,
                on_error=on_heartbeat_error,
            )
            heartbeat.start()

        task.state = "connecting"
        _persist_status_change(
            task,
            status="connecting",
            event_type=EventType.TASK_STATUS_CHANGED.value,
        )
        _note_task_activity(task, "running")
        task.state = "running"
        task.started_at = time.time()
        _persist_status_change(
            task,
            status="running",
            event_type=EventType.TASK_STARTED.value,
            started_at=task.started_at,
        )
        _AGENT_OBSERVATIONS.started(task, timestamp=task.started_at)
        callbacks = _make_backend_callbacks(task, log_event)
        metadata = {
            "route": task.route,
            "access_mode": (
                "verification" if "verify" in task.route
                else "patch" if "patch" in task.route
                else "read_only" if "read_only" in task.route
                else "legacy"
            ),
            "verification_plan": dict(task.verification_plan or {}),
            "patch_policy": dict(task.patch_policy or {}),
            "routing_metadata": dict(task.routing_metadata or {}),
        }
        if task.resume_session_id:
            request = BackendResumeRequest(
                task_id=task.task_id,
                attempt_id=task.current_attempt_id or "",
                runtime_session_id=task.runtime_session_id or "",
                prompt=task.prompt,
                cwd=task.cwd,
                model=task.model,
                reasoning_effort=task.reasoning_effort,
                context_window_tokens=task.context_window_tokens,
                resume_session_id=task.resume_session_id,
                idle_timeout_seconds=task.idle_timeout_seconds,
                max_task_duration_seconds=task.max_task_duration_seconds,
                metadata=metadata,
            )
            backend_result = backend.resume(request, callbacks)
        else:
            request = BackendStartRequest(
                task_id=task.task_id,
                attempt_id=task.current_attempt_id or "",
                runtime_session_id=task.runtime_session_id or "",
                prompt=task.prompt,
                cwd=task.cwd,
                model=task.model,
                reasoning_effort=task.reasoning_effort,
                context_window_tokens=task.context_window_tokens,
                idle_timeout_seconds=task.idle_timeout_seconds,
                max_task_duration_seconds=task.max_task_duration_seconds,
                metadata=metadata,
            )
            backend_result = backend.start(request, callbacks)

        task.backend_session_id = backend_result.backend_session_id or task.backend_session_id
        task.answer = backend_result.answer
        result_backend = backend_result.backend or backend_name
        task.result = {
            **(backend_result.result or {}),
            "backend": result_backend,
            "stopReason": backend_result.stop_reason,
            "reasoning_effort_requested": task.reasoning_effort or None,
            "context_window_tokens_requested": task.context_window_tokens,
            "reasoning_effort_applied": (backend_result.result or {}).get(
                "reasoning_effort_applied"
            ),
        }
        task.terminal_reason = backend_result.stop_reason or "end_turn"
        _persist_completed(task, task.result, backend_result=backend_result)
        task.state = "completed"
        _note_task_activity(task, "final_response")
        _AGENT_OBSERVATIONS.completed(task, answer=task.answer)
    except BackendCancelledError:
        task.state = "cancelled"
        task.error = f"{backend_name} execution was cancelled"
        task.terminal_reason = "cancelled"
        task.cancel_confirmed = True
        task.cancel_confirmed_at = time.time()
        if not task.cancel_scope:
            task.cancel_scope = str(cancel_scope_for_route(task.route))
        _persist_cancel_confirmed(task)
        _note_task_activity(task, "process_terminated")
        _AGENT_OBSERVATIONS.cancelled(task)
    except Exception as exc:
        task.state = "failed"
        task.error = str(exc)
        task.terminal_reason = type(exc).__name__
        if isinstance(exc, BackendTimeoutError):
            task.timeout_reason = exc.timeout_reason
        if not _persist_failed_with_partial_artifacts(task):
            _persist_failed(task)
        _note_task_activity(task, "failed")
        failure_phase = getattr(exc, "phase", None)
        _AGENT_OBSERVATIONS.failed(task, reason=type(exc).__name__, phase=failure_phase)
        activity_logger.terminal(
            f"{backend_name} task failed",
            status=type(exc).__name__,
            session_id=task.backend_session_id or task.runtime_session_id,
        )
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        if task.lease is not None:
            try:
                _runtime_lease_service().release(task.task_id, task.lease)
            except RuntimePersistenceError:
                pass
        _cleanup_terminal_patch_workspace(task)
        activity_logger.close()
        task.updated_at = time.time()
        task.finished_at = task.updated_at
        with task.condition:
            task.condition.notify_all()


def _cleanup_terminal_patch_workspace(task: TaskState) -> None:
    """Best-effort retirement for failed patch tasks only.

    Successful patch tasks are retired synchronously *before* ``completed`` is
    persisted by ``_persist_completed``.  This finally-path exists only to
    clean failed tasks after their evidence has been persisted.
    Cancelled/lost/orphaned worktrees stay available for operator inspection.
    """
    if task.workspace_mode not in {"patch_worktree", "verification_worktree"} or not task.source_cwd or not task.cwd:
        return
    if task.state != "failed" or task.persist_error:
        return
    try:
        root = _get_runtime_database().path.parent / "workspaces"
        PatchWorkspaceService(root).cleanup(task.cwd, source_root=task.source_cwd)
    except PatchWorkspaceCleanupError as exc:
        task.persist_error = f"isolated workspace cleanup requires attention: {type(exc).__name__}"


def _run_qoder(task: TaskState, timeout_seconds: float) -> None:
    _run_official_cli_task(
        task,
        timeout_seconds,
        backend_name="qoder",
        backend_factory=_create_qoder_backend,
        cancel_scope_for_route=(
            lambda route: "qoder_print" if route == "print" else "qoder_acp"
        ),
    )


def _run_codebuddy(task: TaskState, timeout_seconds: float) -> None:
    _run_official_cli_task(
        task,
        timeout_seconds,
        backend_name="codebuddy",
        backend_factory=_create_codebuddy_backend,
        cancel_scope_for_route=lambda route: "codebuddy_acp" if route.startswith("acp_") else "codebuddy_sdk",
    )

def _durable_cli_start(
    *,
    runtime: str,
    task_type: str,
    route: str,
    resumable_routes: frozenset[str],
    worker_target: Any,
    prompt: str,
    cwd: str = "",
    timeout_seconds: int = 300,
    model: str = "",
    reasoning_effort: str = "",
    context_window_tokens: int | None = None,
    resume_task_id: str = "",
    idempotency_key: str = "",
    idle_timeout_seconds: int = 180,
    max_task_duration_seconds: int | None = None,
    parent_task_id: str = "",
    context_id: str = "",
    agent_profile: str = "",
    execution_mode: str = "background",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    verification_commands: list[str] | None = None,
    verification_command_specs: list[CommandSpec] | None = None,
    expected_artifacts: list[str] | None = None,
    max_changed_files: int = 0,
    max_diff_lines: int = 0,
    verification_timeout_seconds: int = 900,
    require_patch: bool = False,
    source_cwd: str = "",
    workspace_mode: str = "",
    workspace_base_revision: str = "",
    patch_policy: dict[str, Any] | None = None,
    routing_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared durable launcher for official CLI adapters.

    Vendor wrappers validate their public routes first, then reuse this exact
    Task/Session/Attempt lifecycle.  This keeps CodeBuddy and Qoder as Crew
    adapters, not separate runtime implementations.
    """
    working_dir = Path(cwd or Path.cwd()).resolve()
    if not working_dir.is_dir():
        return {"ok": False, "error": f"cwd is not a directory: {working_dir}"}
    if not prompt.strip():
        return {"ok": False, "error": "prompt must not be empty"}
    effective_max = (
        max_task_duration_seconds
        if max_task_duration_seconds is not None
        else timeout_seconds
    )
    if idle_timeout_seconds <= 0 or effective_max <= 0:
        return {"ok": False, "error": "timeouts must be positive"}
    try:
        plan = _verification_plan_from_args(
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            verification_commands=verification_commands,
            verification_command_specs=verification_command_specs,
            expected_artifacts=expected_artifacts,
            max_changed_files=max_changed_files,
            max_diff_lines=max_diff_lines,
            verification_timeout_seconds=verification_timeout_seconds,
            require_patch=require_patch,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    routing = dict(routing_metadata or {})
    allowed_routing_keys = {"read_scope", "worker_profile_ref", "worker_skill_refs", "input_artifact_refs", "trusted_instruction_refs", "captain_request_contract", "effective_model_policy", "correlation_id", "presentation_group_id", "model_policy", "model_parameters", "repository_research", "repository_snapshot_ref", "scope_segment", "run_control", "step_key", "apply_receipt", "verification_policy", "verification_subject", "context_delivery", "workspace_strategy"}
    if set(routing) - allowed_routing_keys:
        return {"ok": False, "error": "routing_metadata contains unsupported keys"}
    try:
        encoded_routing = json.dumps(routing, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return {"ok": False, "error": "routing_metadata must be JSON serializable"}
    if len(encoded_routing.encode("utf-8")) > 32 * 1024:
        return {"ok": False, "error": "routing_metadata exceeds 32 KiB"}

    run_spec: RunControlSpec | None = None
    raw_run = routing.get("run_control")
    if raw_run is not None:
        try:
            run_spec = RunControlSpec.from_dict(raw_run)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "reason_code": "RUN_CONTROL_INVALID", "error": str(exc)}
    routing_step_key = str(routing.get("step_key") or "").strip()
    if (run_spec is None) != (not routing_step_key):
        return {"ok": False, "reason_code": "RUN_STEP_INVALID", "error": "run_control and step_key must be supplied together"}

    try:
        service = _runtime_service()
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}

    resume_session_id = ""
    canonical_resume_task_id = resume_task_id.strip()
    if canonical_resume_task_id:
        if route not in resumable_routes:
            return {
                "ok": False,
                "error": f"{runtime} route {route} does not support resume",
            }
        prior = service.get_task(canonical_resume_task_id)
        prior_session = (
            service.get_session(canonical_resume_task_id)
            if prior is not None
            else None
        )
        if (
            prior is None
            or prior.task_type != task_type
            or prior_session is None
            or prior_session.backend != runtime
            or not prior_session.backend_session_id
        ):
            return {
                "ok": False,
                "error": f"resume_task_id is not a resumable {runtime} task",
            }
        resume_session_id = prior_session.backend_session_id

    baseline = capture_workspace_baseline(working_dir)
    if workspace_mode.strip() == "verification_worktree":
        # The reconstructed patch is the trusted verification subject, not a
        # pre-existing dirty Passenger baseline.  Keep Git identity/head for
        # command-stability checks but do not attribute the staged patch as a
        # Crew mutation.
        baseline = WorkspaceBaseline(
            git_root=baseline.git_root, head=baseline.head, dirty=False,
            status_sha256=baseline.status_sha256, changed_files=(),
        )
    canonical_key = idempotency_key.strip()
    if len(canonical_key) > 128:
        return {"ok": False, "error": "idempotency_key must be at most 128 characters"}
    fingerprint = _request_fingerprint(
        prompt,
        str(working_dir),
        model,
        reasoning_effort,
        "",
        resume_session_id,
        "",
        False,
        {
            "runtime": runtime,
            "route": route,
            "resume_task_id": canonical_resume_task_id,
            "parent_task_id": parent_task_id.strip(),
            "context_id": context_id.strip(),
            "agent_profile": agent_profile.strip(),
            "execution_mode": execution_mode.strip().lower(),
            "context_window_tokens": context_window_tokens,
            "verification_plan": plan.to_dict(),
            "routing_metadata": {key: value for key, value in routing.items() if key != "effective_model_policy"},
        },
    )
    if canonical_key:
        pair = service.resolve_idempotent(canonical_key)
        if pair is not None:
            stored_fingerprint, stored_task_id = pair
            if stored_fingerprint != fingerprint:
                return {"ok": False, "error": "idempotency_key conflict"}
            durable = service.get_task(stored_task_id)
            if durable is not None:
                return {
                    "ok": True,
                    "replayed": True,
                    **_public(_task_state_from_durable(durable, service)),
                }

    task_id = new_task_id()
    runtime_session_id = new_runtime_session_id()
    now = now_epoch()
    try:
        lineage = _build_lineage(
            service,
            task_id=task_id,
            parent_task_id=parent_task_id,
            context_id=context_id,
            agent_profile=agent_profile,
            execution_mode=execution_mode,
            now=now,
        )
    except (ValueError, RuntimePersistenceError) as exc:
        return {"ok": False, "error": str(exc)}
    metadata = {
        "cwd": str(working_dir),
        "model": model.strip(),
        "reasoning_effort": reasoning_effort.strip(),
        "context_window_tokens": context_window_tokens,
        "resume_session_id": resume_session_id,
        "idle_timeout_seconds": float(idle_timeout_seconds),
        "max_task_duration_seconds": float(effective_max),
        "runtime": runtime,
        "agent_profile": lineage.agent_profile,
        "parent_task_id": lineage.parent_task_id,
        "root_task_id": lineage.root_task_id,
        "context_id": lineage.context_id,
        "execution_mode": lineage.execution_mode,
        "verification_plan": plan.to_dict(),
        "workspace_baseline": baseline.to_dict(),
        "source_cwd": source_cwd.strip(),
        "workspace_mode": workspace_mode.strip(),
        "workspace_base_revision": workspace_base_revision.strip(),
        "patch_policy": dict(patch_policy or {}),
        "routing_metadata": routing,
    }
    durable_task = Task(
        task_id=task_id,
        task_type=task_type,
        status="queued",
        route=route,
        created_at=now,
        updated_at=now,
        session_id=runtime_session_id,
        run_id=(run_spec.run_id if run_spec is not None else None),
        step_key=(routing_step_key or None),
    )
    session = Session(
        session_id=runtime_session_id,
        task_id=task_id,
        backend=runtime,
        route=route,
        created_at=now,
        updated_at=now,
    )
    try:
        slot_acquired = _try_acquire_crew_worker_slot(runtime)
    except VoyagerUserConfigError as exc:
        return {"ok": False, "reason_code": "CONFIG_INVALID", "error": str(exc)}
    if not slot_acquired:
        return {
            "ok": False,
            "reason_code": "RUNTIME_BUSY",
            "error": f"{runtime} max_concurrent_tasks limit reached",
        }
    try:
        created = service.create_task(
            task=durable_task,
            session=session,
            metadata=metadata,
            idempotency_key=canonical_key,
            request_fingerprint=fingerprint,
            lineage=lineage,
            run_control=run_spec,
            requested_runtime_seconds=float(effective_max),
            now=now,
        )
    except RuntimePersistenceError as exc:
        _release_crew_worker_slot(runtime)
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    if created.outcome == "replayed":
        _release_crew_worker_slot(runtime)
        durable = service.get_task(created.task_id or "")
        return {
            "ok": True,
            "replayed": True,
            **(
                _public(_task_state_from_durable(durable, service))
                if durable
                else {"task_id": created.task_id}
            ),
        }
    if created.outcome in {"budget_rejected", "step_conflict"}:
        _release_crew_worker_slot(runtime)
        return {"ok": False, "reason_code": created.reason_code, "error": created.error}
    if created.outcome == "conflict":
        _release_crew_worker_slot(runtime)
        return {"ok": False, "reason_code": "IDEMPOTENCY_CONFLICT", "error": created.error}

    task = TaskState(
        task_id=task_id,
        prompt=prompt,
        cwd=str(working_dir),
        runtime=runtime,
        model=model.strip(),
        reasoning_effort=reasoning_effort.strip(),
        context_window_tokens=context_window_tokens,
        resume_session_id=resume_session_id,
        resumed=bool(resume_session_id),
        idempotency_key=canonical_key,
        request_fingerprint=fingerprint,
        idle_timeout_seconds=float(idle_timeout_seconds),
        max_task_duration_seconds=float(effective_max),
        session_id=runtime_session_id,
        runtime_session_id=runtime_session_id,
        route=route,
        created_at=now,
        updated_at=now,
        persisted=True,
        version=1,
        current_attempt_id=created.attempt_id,
        parent_task_id=lineage.parent_task_id,
        root_task_id=lineage.root_task_id,
        context_id=lineage.context_id,
        agent_profile=lineage.agent_profile,
        execution_mode=lineage.execution_mode,
        verification_plan=plan.to_dict(),
        workspace_baseline=baseline.to_dict(),
        source_cwd=source_cwd.strip(),
        workspace_mode=workspace_mode.strip(),
        workspace_base_revision=workspace_base_revision.strip(),
        patch_policy=dict(patch_policy or {}),
        routing_metadata=routing,
        run_id=(run_spec.run_id if run_spec is not None else None),
        step_key=(routing_step_key or None),
    )
    with TASKS_LOCK:
        TASKS[task_id] = task
        if canonical_key:
            IDEMPOTENCY_TASKS[canonical_key] = task_id
    _note_task_activity(task, "task_accepted")
    thread = threading.Thread(
        target=_run_worker_with_crew_slot,
        args=(worker_target, task, float(timeout_seconds), runtime),
        daemon=True,
    )
    try:
        _start_worker_thread(thread)
    except RuntimeError as exc:
        _release_crew_worker_slot(runtime)
        task.error = str(exc)
        task.terminal_reason = "ThreadStartError"
        task.state = "failed"
        _persist_failed(task)
        return {"ok": False, "error": f"Worker thread could not start: {exc}"}
    return {"ok": True, "task_id": task_id, "replayed": False, **_public(task)}


def _qoder_start(
    *,
    prompt: str,
    cwd: str = "",
    timeout_seconds: int = 300,
    model: str = "",
    reasoning_effort: str = "",
    context_window_tokens: int | None = None,
    route: str = "acp_read_only",
    resume_task_id: str = "",
    idempotency_key: str = "",
    idle_timeout_seconds: int = 180,
    max_task_duration_seconds: int | None = None,
    parent_task_id: str = "",
    context_id: str = "",
    agent_profile: str = "",
    execution_mode: str = "background",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    verification_commands: list[str] | None = None,
    verification_command_specs: list[CommandSpec] | None = None,
    expected_artifacts: list[str] | None = None,
    max_changed_files: int = 0,
    max_diff_lines: int = 0,
    verification_timeout_seconds: int = 900,
    require_patch: bool = False,
    source_cwd: str = "",
    workspace_mode: str = "",
    workspace_base_revision: str = "",
    patch_policy: dict[str, Any] | None = None,
    routing_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_route = route.strip().lower() or "acp_read_only"
    if canonical_route not in {"acp_read_only", "acp_patch", "acp_verify"}:
        return {"ok": False, "error": "Qoder route must be acp_read_only, acp_patch or acp_verify"}
    effective_max = (
        max_task_duration_seconds
        if max_task_duration_seconds is not None
        else timeout_seconds
    )
    if idle_timeout_seconds >= effective_max:
        return {
            "ok": False,
            "error": "idle_timeout_seconds must be less than max_task_duration_seconds",
        }
    return _durable_cli_start(
        runtime="qoder",
        task_type="qoder",
        route=canonical_route,
        resumable_routes=frozenset({"acp_read_only", "acp_patch", "acp_verify"}),
        worker_target=_run_qoder,
        prompt=prompt,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        model=model,
        reasoning_effort=reasoning_effort,
        context_window_tokens=context_window_tokens,
        resume_task_id=resume_task_id,
        idempotency_key=idempotency_key,
        idle_timeout_seconds=idle_timeout_seconds,
        max_task_duration_seconds=max_task_duration_seconds,
        parent_task_id=parent_task_id,
        context_id=context_id,
        agent_profile=agent_profile,
        execution_mode=execution_mode,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        verification_commands=verification_commands,
        verification_command_specs=verification_command_specs,
        expected_artifacts=expected_artifacts,
        max_changed_files=max_changed_files,
        max_diff_lines=max_diff_lines,
        verification_timeout_seconds=verification_timeout_seconds,
        require_patch=require_patch,
        source_cwd=source_cwd,
        workspace_mode=workspace_mode,
        workspace_base_revision=workspace_base_revision,
        patch_policy=patch_policy,
        routing_metadata=routing_metadata,
    )


def _codebuddy_start(
    *,
    prompt: str,
    cwd: str = "",
    timeout_seconds: int = 300,
    model: str = "",
    reasoning_effort: str = "",
    context_window_tokens: int | None = None,
    route: str = "acp_read_only",
    resume_task_id: str = "",
    idempotency_key: str = "",
    idle_timeout_seconds: int = 180,
    max_task_duration_seconds: int | None = None,
    parent_task_id: str = "",
    context_id: str = "",
    agent_profile: str = "",
    execution_mode: str = "background",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    verification_commands: list[str] | None = None,
    verification_command_specs: list[CommandSpec] | None = None,
    expected_artifacts: list[str] | None = None,
    max_changed_files: int = 0,
    max_diff_lines: int = 0,
    verification_timeout_seconds: int = 900,
    require_patch: bool = False,
    source_cwd: str = "",
    workspace_mode: str = "",
    workspace_base_revision: str = "",
    patch_policy: dict[str, Any] | None = None,
    routing_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_route = route.strip().lower() or "acp_read_only"
    if canonical_route not in {"acp_read_only", "acp_patch", "acp_verify", "sdk_context_read_only", "sdk_patch", "sdk_verify"}:
        return {
            "ok": False,
            "error": "CodeBuddy route must be acp_read_only, acp_patch, acp_verify, sdk_context_read_only, sdk_patch or sdk_verify",
        }
    effective_max = (
        max_task_duration_seconds
        if max_task_duration_seconds is not None
        else timeout_seconds
    )
    if idle_timeout_seconds >= effective_max:
        return {
            "ok": False,
            "error": "idle_timeout_seconds must be less than max_task_duration_seconds",
        }
    if context_window_tokens is not None:
        return {
            "ok": False,
            "error": "CodeBuddy controlled route does not expose context_window_tokens",
        }
    if reasoning_effort.strip().lower() not in {"", "low", "medium", "high", "xhigh"}:
        return {"ok": False, "error": "CodeBuddy reasoning_effort must be low, medium, high or xhigh"}
    return _durable_cli_start(
        runtime="codebuddy",
        task_type="codebuddy",
        route=canonical_route,
        resumable_routes=frozenset({"acp_read_only", "acp_patch", "acp_verify", "sdk_context_read_only", "sdk_patch", "sdk_verify"}),
        worker_target=_run_codebuddy,
        prompt=prompt,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        model=model,
        reasoning_effort=reasoning_effort,
        resume_task_id=resume_task_id,
        idempotency_key=idempotency_key,
        idle_timeout_seconds=idle_timeout_seconds,
        max_task_duration_seconds=max_task_duration_seconds,
        parent_task_id=parent_task_id,
        context_id=context_id,
        agent_profile=agent_profile,
        execution_mode=execution_mode,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        verification_commands=verification_commands,
        verification_command_specs=verification_command_specs,
        expected_artifacts=expected_artifacts,
        max_changed_files=max_changed_files,
        max_diff_lines=max_diff_lines,
        verification_timeout_seconds=verification_timeout_seconds,
        require_patch=require_patch,
        source_cwd=source_cwd,
        workspace_mode=workspace_mode,
        workspace_base_revision=workspace_base_revision,
        patch_policy=patch_policy,
        routing_metadata=routing_metadata,
    )

def _safe_relative_values(values: list[str] | None, *, field_name: str) -> tuple[str, ...]:
    output: list[str] = []
    for raw in values or []:
        original = str(raw).strip().replace("\\", "/")
        if (
            original.startswith("/")
            or original.startswith("//")
            or (len(original) >= 2 and original[1] == ":")
        ):
            raise ValueError(f"{field_name} contains an unsafe relative path")
        value = original.strip("/")
        parts = [part for part in value.split("/") if part]
        if not value or any(part in {".", ".."} for part in parts):
            raise ValueError(f"{field_name} contains an unsafe relative path")
        if len(value) > 512:
            raise ValueError(f"{field_name} path is too long")
        if value not in output:
            output.append(value)
    return tuple(output)


def _verification_plan_from_args(
    *,
    allowed_paths: list[str] | None,
    forbidden_paths: list[str] | None,
    verification_commands: list[str] | None,
    verification_command_specs: list[CommandSpec] | None = None,
    expected_artifacts: list[str] | None,
    max_changed_files: int,
    max_diff_lines: int = 0,
    verification_timeout_seconds: int,
    require_patch: bool,
) -> VerificationPlan:
    commands = tuple(str(item).strip() for item in (verification_commands or []) if str(item).strip())
    if len(commands) > 32 or any(len(item) > 4_000 for item in commands):
        raise ValueError("verification_commands exceeds the bounded V1 contract")
    command_specs = tuple(verification_command_specs or ())
    if len(command_specs) > 16:
        raise ValueError("verification_command_specs exceeds the bounded TP-Voyager contract")
    if max_changed_files < 0:
        raise ValueError("max_changed_files must be non-negative")
    if max_diff_lines < 0:
        raise ValueError("max_diff_lines must be non-negative")
    if verification_timeout_seconds <= 0:
        raise ValueError("verification_timeout_seconds must be positive")
    return VerificationPlan(
        allowed_paths=_safe_relative_values(allowed_paths, field_name="allowed_paths"),
        forbidden_paths=(
            _safe_relative_values(forbidden_paths, field_name="forbidden_paths")
            if forbidden_paths is not None else (".git",)
        ),
        commands=commands,
        command_specs=command_specs,
        expected_artifacts=_safe_relative_values(expected_artifacts, field_name="expected_artifacts"),
        max_changed_files=max_changed_files,
        max_diff_lines=max_diff_lines,
        command_timeout_seconds=float(verification_timeout_seconds),
        require_patch=bool(require_patch),
    )


def _build_lineage(
    service: TaskService,
    *,
    task_id: str,
    parent_task_id: str,
    context_id: str,
    agent_profile: str,
    execution_mode: str,
    now: float,
) -> TaskLineage:
    mode = execution_mode.strip().lower() or "background"
    if mode not in {"background", "detached"}:
        raise ValueError("execution_mode must be background or detached")
    parent_id = parent_task_id.strip() or None
    root_id = task_id
    inherited_context: str | None = None
    if parent_id:
        if service.get_task(parent_id) is None:
            raise ValueError(f"Unknown parent_task_id: {parent_id}")
        parent_lineage = service.get_lineage(parent_id)
        root_id = parent_lineage.root_task_id if parent_lineage else parent_id
        inherited_context = parent_lineage.context_id if parent_lineage else None
    return TaskLineage(
        child_task_id=task_id,
        parent_task_id=parent_id,
        root_task_id=root_id,
        context_id=context_id.strip() or inherited_context,
        agent_profile=agent_profile.strip() or None,
        execution_mode=mode,
        created_at=now,
    )


def _task_wait(task_id: str, timeout_seconds: int = 55) -> dict[str, Any]:
    """Wait briefly for a durable Crew task; returns current state on timeout.

    Every terminal status (including LOST/ORPHANED) returns immediately:
    they are already final states, never waited on.
    """
    try:
        task = _load_task(task_id)
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    if not task:
        return {"ok": False, "error": f"Unknown task_id: {task_id}"}
    wait_timed_out = False
    if task.state not in TERMINAL_STATUS_VALUES:
        with TASKS_LOCK:
            handle = TASKS.get(task_id)
        if handle is not None:
            # Live handle: wait on the real condition, then re-read durable state.
            with handle.condition:
                handle.condition.wait(timeout=max(0, min(timeout_seconds, 55)))
            task = _load_task(task_id)
            wait_timed_out = task.state not in TERMINAL_STATUS_VALUES
        else:
            # Restart-recovered task: poll the durable state.
            deadline = time.monotonic() + max(0, min(timeout_seconds, 55))
            while time.monotonic() < deadline:
                task = _load_task(task_id)
                if task.state in TERMINAL_STATUS_VALUES:
                    break
                time.sleep(0.2)
            wait_timed_out = task.state not in TERMINAL_STATUS_VALUES
    return {"ok": True, "wait_timed_out": wait_timed_out, **_public(task)}


def _usage_evidence_for_task(task_id: str) -> dict[str, Any]:
    try:
        return _runtime_service().latest_usage_evidence(task_id)
    except (TaskNotFoundError, ValueError, RuntimePersistenceError):
        return {}


def _usage_projection(
    evidence: dict[str, Any], *, observability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project only provider-observed/explicitly-derived Token and Credit facts."""
    provenance = (observability or {}).get("usage_provenance") if isinstance(observability, dict) else None
    provenance_status = str(provenance.get("status") or "") if isinstance(provenance, dict) else ""
    usage = evidence.get("usage") if isinstance(evidence, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    output: dict[str, Any] = {}
    for field in (
        "total_tokens", "input_tokens", "cache_read_tokens", "cache_miss_tokens",
        "cache_write_tokens", "output_tokens", "reasoning_tokens", "answer_tokens",
        "credits", "session_credits", "original_credits",
    ):
        value = usage.get(field)
        if value is None and field == "credits":
            value = usage.get("credits_used")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            output[field] = value
    if isinstance(usage.get("billable"), bool):
        output["billable"] = usage["billable"]
    derived = usage.get("derived_fields")
    if isinstance(derived, list):
        output["derived_fields"] = [str(item)[:80] for item in derived[:16] if str(item).strip()]
    if output:
        status = "observed"
    elif provenance_status in {"provider_omitted", "protocol_unrecognized"}:
        status = provenance_status
    elif isinstance(evidence.get("provider_usage"), dict) and evidence.get("provider_usage"):
        status = "protocol_unrecognized"
    else:
        status = "provider_omitted"
    result: dict[str, Any] = {"status": status}
    if isinstance(evidence, dict):
        for field in ("provider", "scope", "model", "source"):
            value = evidence.get(field)
            if isinstance(value, str) and value.strip():
                result[field] = value.strip()[:160]
    result.update(output)
    return result


def _routing_projection(task: TaskState) -> dict[str, Any]:
    routing = task.routing_metadata if isinstance(task.routing_metadata, dict) else {}
    output: dict[str, Any] = {}
    model_parameters = routing.get("model_parameters")
    if isinstance(model_parameters, dict):
        output["model_parameters"] = {
            key: model_parameters.get(key)
            for key in ("reasoning_effort", "context_window_tokens")
            if model_parameters.get(key) is not None
        }
    correlation_id = routing.get("correlation_id")
    if isinstance(correlation_id, str) and correlation_id:
        output["correlation_id"] = correlation_id
    presentation_group_id = routing.get("presentation_group_id")
    if isinstance(presentation_group_id, str) and presentation_group_id:
        output["presentation_group_id"] = presentation_group_id
    profile = routing.get("worker_profile_ref")
    if isinstance(profile, dict):
        output["worker_profile_ref"] = dict(profile)
    policy = routing.get("model_policy")
    if isinstance(policy, dict):
        output["model_policy"] = dict(policy)
    research = routing.get("repository_research")
    if isinstance(research, dict):
        output["repository_research"] = {
            key: research.get(key)
            for key in (
                "url", "source_subdirectory", "report_path", "repository_size_bytes",
                "commit", "acquisition", "scope_manifest_id", "scope_root_hash",
                "scope_file_count", "scope_total_bytes", "scope_segment_index",
                "scope_segment_count", "scope_segment_context_id",
                "scope_segment_root_hash", "scope_segment_file_count",
                "scope_segment_total_bytes", "snapshot_source_task_id",
            )
            if research.get(key) is not None
        }
    snapshot_ref = routing.get("repository_snapshot_ref")
    if isinstance(snapshot_ref, dict):
        output["repository_snapshot_ref"] = {
            key: snapshot_ref.get(key)
            for key in ("source_task_id", "commit", "scope_manifest_id", "scope_root_hash")
            if snapshot_ref.get(key) is not None
        }
    segment = routing.get("scope_segment")
    if isinstance(segment, dict):
        output["scope_segment"] = {"index": segment.get("index")}
    scope = routing.get("read_scope")
    if isinstance(scope, dict):
        resolved = scope.get("resolved_files")
        output["read_scope"] = {
            "files": list(scope.get("files") or []),
            "directories": list(scope.get("directories") or []),
            "globs": list(scope.get("globs") or []),
            "max_files": scope.get("max_files"),
            "max_bytes": scope.get("max_bytes"),
            "resolved_file_count": len(resolved) if isinstance(resolved, list) else 0,
        }

    run = routing.get("run_control")
    step_key = str(routing.get("step_key") or task.step_key or "").strip()
    run_id = str(run.get("run_id") or task.run_id or "").strip() if isinstance(run, dict) else str(task.run_id or "").strip()
    if run_id:
        try:
            snapshot = _runtime_service().get_run_control(run_id)
            output["run_control"] = snapshot.to_dict() if snapshot is not None else {"run_id": run_id, "status": "unknown"}
        except RuntimePersistenceError:
            output["run_control"] = {"run_id": run_id, "status": "unknown"}

    captain_contract = routing.get("captain_request_contract")
    effective_policy = routing.get("effective_model_policy")
    instructions = routing.get("trusted_instruction_refs")
    input_artifacts = routing.get("input_artifact_refs")
    verification_subject = routing.get("verification_subject")
    provenance: dict[str, Any] = {}
    if run_id:
        provenance["run_id"] = run_id
    if step_key:
        provenance["step_key"] = step_key
    if isinstance(captain_contract, dict):
        objective_hash = captain_contract.get("objective_sha256")
        if isinstance(objective_hash, str):
            provenance["captain_request_sha256"] = objective_hash
    if isinstance(effective_policy, dict):
        policy_hash = effective_policy.get("policy_sha256")
        if isinstance(policy_hash, str) and policy_hash:
            provenance["policy_sha256"] = policy_hash
    if isinstance(research, dict) and research.get("scope_manifest_id"):
        provenance["scope_manifest_id"] = research.get("scope_manifest_id")
        provenance["scope_root_hash"] = research.get("scope_root_hash")
        provenance["scope_segment_context_id"] = research.get("scope_segment_context_id")
        provenance["scope_segment_root_hash"] = research.get("scope_segment_root_hash")
        provenance["scope_segment_index"] = research.get("scope_segment_index")
    elif task.context_id:
        provenance["scope_manifest_id"] = task.context_id
        try:
            manifest = _context_service().get(task.context_id)
            root_hash = manifest.get("root_hash") if isinstance(manifest, dict) else None
            if isinstance(root_hash, str) and root_hash:
                provenance["scope_root_hash"] = root_hash
        except (ContextError, RuntimePersistenceError):
            pass
    if isinstance(instructions, list):
        provenance["instruction_refs"] = [
            {key: item.get(key) for key in ("root_alias", "path", "sha256") if item.get(key) is not None}
            for item in instructions[:8] if isinstance(item, dict)
        ]
    if isinstance(input_artifacts, list):
        provenance["input_artifact_refs"] = [
            {key: item.get(key) for key in ("source_task_id", "artifact_id", "sha256", "byte_size") if item.get(key) is not None}
            for item in input_artifacts[:8] if isinstance(item, dict)
        ]
    receipt = routing.get("apply_receipt")
    if isinstance(receipt, dict):
        provenance["apply_receipt_sha256"] = receipt.get("receipt_sha256")
    if isinstance(verification_subject, dict):
        provenance["verification_subject"] = {
            key: verification_subject.get(key)
            for key in ("repository_identity", "base_commit", "patch_artifact_id", "patch_sha256", "result_tree_hash", "apply_receipt_sha256", "context_id", "context_root_hash")
            if verification_subject.get(key) is not None
        }
    if provenance:
        output["provenance"] = provenance
    return output


def _task_result_response(task_id: str) -> dict[str, Any]:
    """Return final task material only after a task completed successfully.

    PR3.1: a reconciled ``completed`` whose final Result payload could not
    be recovered is refused (``ok=false``) instead of returning
    ``ok=true`` with an empty answer.  Status and wait responses
    intentionally remain content-free.  Callers authorized to consume
    delegated work use this explicit terminal endpoint.
    """
    try:
        task = _load_task(task_id)
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    if not task:
        return {"ok": False, "error": f"Unknown task_id: {task_id}"}
    if task.state != "completed":
        return {
            "ok": False,
            "error": "Final subagent material is not available for this task state",
            **_public(task),
            **_routing_projection(task),
            "usage": _usage_projection(_usage_evidence_for_task(task_id)),
        }
    if task.result_parse_error:
        return {
            "ok": False,
            "state": "completed",
            **_public(task),
            **_routing_projection(task),
            "usage": _usage_projection(_usage_evidence_for_task(task_id)),
            "error": "Task completed, but persisted final material is unreadable",
        }
    if not (
        task.result_available
        or task.answer
        or (task.result is not None and isinstance(task.result, dict))
    ):
        return {
            "ok": False,
            "state": "completed",
            **_public(task),
            **_routing_projection(task),
            "usage": _usage_projection(_usage_evidence_for_task(task_id)),
            "error": "Task completed, but final subagent material could not be recovered",
        }
    parsed = None
    try:
        durable = _runtime_service().get_task(task_id)
        if durable is not None and durable.result_json:
            parsed = parse_structured_result(durable.result_json)
    except (RuntimePersistenceError, StructuredResultParseError):
        parsed = None
    if parsed is None and isinstance(task.result, dict):
        try:
            parsed = parse_structured_result(json.dumps(task.result, ensure_ascii=False))
        except StructuredResultParseError:
            parsed = None
    elapsed_seconds = None
    if task.started_at is not None and task.finished_at is not None:
        elapsed_seconds = round(max(0.0, float(task.finished_at - task.started_at)), 3)
    return {
        "ok": True,
        "task_id": task.task_id,
        "state": task.state,
        "runtime": task.runtime,
        "model": task.model or None,
        "execution_budget": {
            "max_task_duration_seconds": task.max_task_duration_seconds,
            "elapsed_seconds": elapsed_seconds,
            "timeout_reason": task.timeout_reason,
        },
        "agent_profile": task.agent_profile,
        "parent_task_id": task.parent_task_id,
        "root_task_id": task.root_task_id or task.task_id,
        "context_id": task.context_id,
        "execution_mode": task.execution_mode,
        **_routing_projection(task),
        "answer": parsed.answer if parsed is not None else task.answer,
        "changed_files": parsed.changed_files if parsed is not None else [],
        "tests": parsed.tests if parsed is not None else [],
        "artifacts": parsed.artifacts if parsed is not None else [],
        "risks": parsed.risks if parsed is not None else [],
        "claims": parsed.claims if parsed is not None else [],
        "verification": parsed.verification if parsed is not None else {},
        "usage": _usage_projection(
            _usage_evidence_for_task(task_id) or (parsed.usage if parsed is not None else {}),
            observability=(parsed.observability if parsed is not None else {}),
        ),
        "crew_outcome": (parsed.crew_outcome if parsed is not None else {}),
        "result_summary": _result_summary(task),
    }




def _task_assessment(task_id: str) -> dict[str, Any]:
    """Assess execution truth and verified work-product truth separately.

    The projection is content-free and read-only.  A failed terminal-loss task
    may surface ``review_for_acceptance`` when deterministic verification
    passed, but this tool never changes the durable Task state and never
    reports that timeout as success.
    """
    try:
        durable = _runtime_service().get_task(task_id)
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    if durable is None:
        return {"ok": False, "error": f"Unknown task_id: {task_id}"}
    return {
        "ok": True,
        **assess_task_result(
            task_id=durable.task_id,
            execution_status=durable.status,
            terminal_reason=durable.terminal_reason,
            timeout_reason=durable.timeout_reason,
            result_available=durable.result_available,
            result_json=durable.result_json,
        ),
    }


def _task_evidence(task_id: str, attempt_id: str = "") -> dict[str, Any]:
    """Return the safe Evidence projection for one resolved Attempt."""
    try:
        service = _runtime_service()
        attempt = service.resolve_attempt(task_id, attempt_id or None)
        evidence = service.list_evidence(task_id, attempt.attempt_id)
    except TaskNotFoundError:
        return {"ok": False, "error": "Task not found"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    return {
        "ok": True,
        "task_id": task_id,
        "attempt_id": attempt.attempt_id,
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "attempt_id": item.attempt_id,
                "type": item.evidence_type,
                "trust_state": item.trust_state,
                "origin": item.origin,
                "summary": item.summary,
                "captured_at": item.captured_at,
                "subject_evidence_id": item.subject_evidence_id,
                "artifact_id": item.artifact_id,
            }
            for item in evidence
        ],
    }


def _task_artifacts(task_id: str, attempt_id: str = "") -> dict[str, Any]:
    """Return the safe Artifact Declaration projection for one Attempt."""
    try:
        service = _runtime_service()
        attempt = service.resolve_attempt(task_id, attempt_id or None)
        artifacts = service.list_artifacts(task_id, attempt.attempt_id)
    except TaskNotFoundError:
        return {"ok": False, "error": "Task not found"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    return {
        "ok": True,
        "task_id": task_id,
        "attempt_id": attempt.attempt_id,
        "artifacts": [
            {
                "artifact_id": item.artifact_id,
                "attempt_id": item.attempt_id,
                "kind": item.kind,
                "name": item.name,
                "capture_state": item.capture_state,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in artifacts
        ],
    }


def _cancel_scope_for(task: TaskState) -> str:
    if task.runtime == "qoder":
        return "qoder_print" if task.route == "print" else "qoder_acp"
    if task.runtime == "codebuddy":
        return "codebuddy_acp" if task.route.startswith("acp_") else "codebuddy_sdk"
    return "backend_execution"


def _task_cancel(task_id: str) -> dict[str, Any]:
    """Request cancellation of a running durable Crew task.

    Sets ``cancel_requested`` immediately and ``cancel_scope`` to indicate what
    was targeted for the selected Crew route.  ``cancel_confirmed`` is
    set later by the worker when the backend/process acknowledges termination.
    For durable tasks the request is persisted (``cancel_requested_at`` +
    event) before the backend transport call.

    Critical: this operates on the real ``TASKS`` handle, not a projection
    copy from ``_load_task``, so the worker sees the cancellation flags.
    """
    try:
        task = _load_task(task_id)
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    if not task:
        return {"ok": False, "error": f"Unknown task_id: {task_id}"}
    if task.state in TERMINAL_STATUS_VALUES:
        return {"ok": False, "state": task.state, "error": "Task already finished"}
    # Get the real live handle (not the projection) for mutable operations.
    with TASKS_LOCK:
        handle = TASKS.get(task_id)
    if handle is None:
        return {
            "ok": False,
            "state": task.state,
            "error": "Task is not cancellable right now (no live handle)",
        }
    # Finalization window: the backend already produced its final result
    # and the runtime is committing it (history sync + terminal
    # persistence) — there is nothing left to cancel.  Rejecting here
    # keeps the durable row free of a cancel_requested that would
    # contradict the upcoming terminal state.
    if handle.execution_finished or handle.finalizing:
        return {
            "ok": False,
            "state": task.state,
            "error": "Task execution already finished and is being finalized",
        }
    # Persist cancel request as a unified durable transaction, fenced on
    # the live handle's session lease (PR3.2): a stale worker whose lease
    # was taken over by reconciliation can never write cancelling.  A
    # persisted handle that has not acquired its lease yet (pre-acquire
    # early cancel) may only cancel an unowned/expired session (PR3.3).
    # PR3.4: the lease read and the durable cancel decision run under the
    # handle's ownership lock, so the worker's acquire/publish and this
    # cancel are serialized — an immediate cancel never mistakes the
    # worker's own publish window for a foreign owner.
    if task.persisted:
        with handle.ownership_lock:
            # PR3.5: re-check the finalization window INSIDE the lock — the
            # outer check above is only a fast path.  on_result publishes
            # finalizing under this same lock, so a cancel that already
            # observed finalizing=false still loses to a concurrent
            # on_result that published first.
            if handle.execution_finished or handle.finalizing:
                return {
                    "ok": False,
                    "state": task.state,
                    "error": "Task execution already finished and is being finalized",
                }
            current_lease = handle.lease
            try:
                cancel_result = _runtime_service().request_cancel(
                    task_id,
                    cancel_scope=_cancel_scope_for(handle),
                    cancel_initiator="user",
                    lease=current_lease,
                    allow_if_unowned=current_lease is None,
                )
            except LeaseLostError:
                # The reconciler owns this task now: never touch durable
                # state, never send a backend cancel.
                return {
                    **_public(handle),
                    "ok": False,
                    "state": task.state,
                    "error": "Task lease was lost; durable recovery owns this task",
                }
            except RuntimePersistenceError as exc:
                return {
                    "ok": False,
                    "state": task.state,
                    "error": f"runtime persistence failed: {exc}",
                }
            # PR3.2: a durable terminal outcome (concurrent reconciliation
            # won) overrides the in-memory handle: never revive it, never
            # cancel.
            if cancel_result.status in TERMINAL_STATUS_VALUES:
                try:
                    durable_view = _load_task(task_id) or handle
                except RuntimePersistenceError:
                    durable_view = handle
                return {
                    **_public(durable_view),
                    "ok": False,
                    "state": cancel_result.status,
                    "error": "Task already finished",
                }
            # Sync the live handle with the durable state after the
            # transaction, so that _run() sees the correct version for
            # subsequent writes.
            handle.version = cancel_result.version
            handle.state = cancel_result.status
            if not cancel_result.created:
                # Durable cancel request already exists.  Only replay when
                # the cancel transport was already sent successfully; a
                # failed first attempt must allow the caller to retry the
                # transport.
                if handle.cancel_transport_requested:
                    return {"ok": True, "cancel_replayed": True, **_public(handle)}
                # First attempt failed (or never ran): fall through and retry.
                handle.cancel_requested = True
                handle.cancel_scope = _cancel_scope_for(handle)
                handle.cancel_initiator = "user"
                handle.cancel_requested_at = time.time()
                handle.state = "cancelling"
    # Modify the real live handle, not the projection.
    handle.cancel_requested = True
    handle.cancel_scope = _cancel_scope_for(handle)
    handle.cancel_initiator = "user"
    handle.cancel_requested_at = time.time()
    handle.state = "cancelling"
    # Unified cancellation: one backend contract path for persisted tasks
    # (and any task with a live registry entry).  The backend reaches the
    # exact host/client via its live execution registry; when no execution
    # is registered yet (pre-registration window), the registry records a
    # pending cancel that the route applies before dispatching anything.
    _note_task_activity(
        handle,
        "cancel_requested",
    )
    try:
        cancel_result = _backend_for_runtime(handle.runtime).cancel(
            BackendCancelRequest(
                task_id=task_id,
                attempt_id=handle.current_attempt_id or "",
                backend_session_id=(
                    handle.backend_session_id or ""
                ),
                cancel_scope=handle.cancel_scope,
            )
        )
    except BackendError as exc:
        return {
            "ok": False,
            **_public(handle),
            "error": "cancellation transport could not be confirmed; inspect the local TP-Voyager activity log using task_id",
        }
    if not cancel_result.ok:
        return {
            "ok": False,
            **_public(handle),
            "error": "cancellation transport could not be confirmed; inspect the local TP-Voyager activity log using task_id",
        }
    # The cancel transport was sent successfully: later calls are replays
    # and never re-send.  A failure never sets this flag, so the caller
    # may retry the transport on a subsequent call.
    if cancel_result.transport_requested:
        handle.cancel_transport_requested = True
    return {
        "ok": True,
        "cancel_transport_requested": cancel_result.transport_requested,
        "cancel_execution_found": cancel_result.active_execution_found,
        **_public(handle),
    }


def _task_launch_service() -> TaskLaunchService:
    """Compose the transport-neutral launch use-case at the MCP boundary."""
    return TaskLaunchService(
        {
            "qoder": _qoder_start,
            "codebuddy": _codebuddy_start,
        }
    )


_PLAN_EXECUTION_SERVICE: PlanExecutionService | None = None
_PLAN_EXECUTION_SERVICE_LOCK = threading.Lock()


def _plan_execution_service() -> PlanExecutionService:
    """Return the process-local controller bound to the durable Runtime DB.

    Only transient prompt/query material lives on this object.  Resetting the
    configured database intentionally drops that cache; SQLite remains truth.
    """
    global _PLAN_EXECUTION_SERVICE
    if _PLAN_EXECUTION_SERVICE is None:
        with _PLAN_EXECUTION_SERVICE_LOCK:
            if _PLAN_EXECUTION_SERVICE is None:
                _PLAN_EXECUTION_SERVICE = PlanExecutionService(
                    _get_runtime_database(),
                    _task_launch_service(),
                    task_canceller=lambda task_id: subagent_cancel(
                        task_id, propagate_to_children=False
                    ),
                )
    return _PLAN_EXECUTION_SERVICE


@_mcp_tool()
def subagent_start(
    prompt: str,
    runtime: str = "",
    cwd: str = "",
    timeout_seconds: int = 300,
    model: str = "",
    reasoning_effort: str = "",
    route: str = "",
    resume_task_id: str = "",
    idempotency_key: str = "",
    idle_timeout_seconds: int = 180,
    max_task_duration_seconds: int | None = None,
    parent_task_id: str = "",
    context_id: str = "",
    agent_profile: str = "",
    execution_mode: str = "background",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    verification_commands: list[str] | None = None,
    expected_artifacts: list[str] | None = None,
    max_changed_files: int = 0,
    verification_timeout_seconds: int = 900,
    require_patch: bool = False,
) -> dict[str, Any]:
    """Start a durable task through the shared application launch boundary."""
    return _task_launch_service().start(
        TaskLaunchRequest(
            prompt=prompt,
            runtime=runtime,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
            route=route,
            resume_task_id=resume_task_id,
            idempotency_key=idempotency_key,
            idle_timeout_seconds=idle_timeout_seconds,
            max_task_duration_seconds=max_task_duration_seconds,
            parent_task_id=parent_task_id,
            context_id=context_id,
            agent_profile=agent_profile,
            execution_mode=execution_mode,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            verification_commands=verification_commands,
            expected_artifacts=expected_artifacts,
            max_changed_files=max_changed_files,
            verification_timeout_seconds=verification_timeout_seconds,
            require_patch=require_patch,
        )
    )


@_mcp_tool()
def crew_catalog(probe: bool = False, include_models: bool = False) -> dict[str, Any]:
    """Return normalized CodeBuddy/Qoder Crew capabilities for the Captain."""
    return {"ok": True, **_crew_registry_service().catalog(probe=probe, include_models=include_models)}


@_mcp_tool()
def crew_health(backend: str, probe: bool = True, model: str = "") -> dict[str, Any]:
    """Return Crew health and optional model facts without selecting anything."""
    try:
        registry = _crew_registry_service()
        output = {"ok": True, **registry.health(backend, probe=probe).to_dict()}
        model_id = str(model or "").strip()
        if model_id:
            snapshot = registry.model_catalog(backend)
            descriptor = next(
                (item for item in snapshot["models"] if str(item.get("model_id") or "") == model_id),
                None,
            )
            output["model"] = descriptor or {
                "backend": str(backend or "").strip().lower(),
                "model_id": model_id,
                "available": None,
                "source": "not_observed_in_catalog",
                "history": registry.model_history(backend, model_id),
                "usage": registry.model_usage_summary(backend, model_id),
            }
        return output
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


@_mcp_tool()
def crew_recommend(
    task_kind: str,
    required_capabilities: list[str] | None = None,
    probe: bool = False,
) -> dict[str, Any]:
    """Rank compatible Crew without selecting or dispatching on the Captain's behalf."""
    try:
        return {
            "ok": True,
            **_crew_registry_service().recommend(
                task_kind,
                required_capabilities=required_capabilities,
                probe=probe,
            ),
        }
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def _runtime_profile_display_path(value: str | Path, config_home: Path) -> str:
    """Present user-owned paths without exposing an absolute host path."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        candidate = Path(text).expanduser().resolve()
        home = config_home.expanduser().resolve()
        relative = candidate.relative_to(home)
        return "~/.tp-voyager" if str(relative) == "." else f"~/.tp-voyager/{relative.as_posix()}"
    except (OSError, RuntimeError, ValueError):
        name = Path(text).name
        return f"<external>/{name}" if name else "<external>"


def _runtime_profile_config_projection(config: VoyagerUserConfig) -> dict[str, Any]:
    """Project all typed user config fields into safe, display-ready values."""
    home = config.home
    return {
        "schema": config.schema,
        "home": _runtime_profile_display_path(home, home),
        "config_path": _runtime_profile_display_path(config.path, home),
        "crew": {
            "qoder": {
                "enabled": config.crew.qoder.enabled,
                "cli_path": _runtime_profile_display_path(config.crew.qoder.cli_path, home),
                "max_concurrent_tasks": config.crew.qoder.max_concurrent_tasks,
            },
            "codebuddy": {
                "enabled": config.crew.codebuddy.enabled,
                "cli_path": _runtime_profile_display_path(config.crew.codebuddy.cli_path, home),
                "internet_environment": config.crew.codebuddy.internet_environment,
                "max_concurrent_tasks": config.crew.codebuddy.max_concurrent_tasks,
            },
        },
        "dispatch": {
            "allowed_models": list(config.dispatch.allowed_models),
            "preferred_models": list(config.dispatch.preferred_models),
            "task_kind_allowed_models": {
                kind: list(models)
                for kind, models in config.dispatch.task_kind_allowed_models
            },
        },
        "trusted_roots": {
            "model_evidence": {
                alias: _runtime_profile_display_path(path, home)
                for alias, path in config.trusted_roots.model_evidence
            },
            "instructions": {
                alias: _runtime_profile_display_path(path, home)
                for alias, path in config.trusted_roots.instructions
            },
        },
        "resources": {
            "worker_profiles_root": _runtime_profile_display_path(
                config.resources.worker_profiles_root, home
            ),
            "worker_skills_root": _runtime_profile_display_path(
                config.resources.worker_skills_root, home
            ),
        },
    }


def _runtime_profile_quota(detail: Any) -> tuple[str, str | None]:
    """Keep account quota separate from task/session Credit facts."""
    source = detail if isinstance(detail, dict) else {}
    for key in ("account_usage", "user_quota", "quota"):
        quota = source.get(key)
        if not isinstance(quota, dict):
            continue
        total = quota.get("total")
        used = quota.get("used")
        remaining = quota.get("remaining")
        if any(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (total, used, remaining)):
            unit = str(quota.get("unit") or "credits").strip() or "credits"
            parts = []
            if isinstance(total, (int, float)) and not isinstance(total, bool):
                parts.append(f"总 {total:g}")
            if isinstance(used, (int, float)) and not isinstance(used, bool):
                parts.append(f"已用 {used:g}")
            if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
                parts.append(f"剩余 {remaining:g}")
            return "observed", f"{' · '.join(parts)} {unit}"
    return "not_observed", None


def _runtime_profile_account_state(
    *,
    backend: str,
    health: dict[str, Any],
    catalog_meta: dict[str, Any],
    refresh_profile: bool,
) -> tuple[str, str, str | None]:
    """Return auth and quota facts without treating task Usage as account quota."""
    auth_status = str(health.get("auth_status") or "not_probed")
    quota_status, quota_summary = _runtime_profile_quota(health.get("detail"))
    if not refresh_profile:
        return auth_status, quota_status, quota_summary

    if backend == "qoder":
        snapshot = collect_qoder_account_snapshot()
        if snapshot.get("status") != "observed":
            return "unknown", "unknown", "暂时无法读取账户额度"
        auth_status = str(snapshot.get("auth_status") or "unknown")
        quota_status, quota_summary = _runtime_profile_quota({
            "user_quota": snapshot.get("user_quota"),
        })
        if quota_status != "observed":
            quota_summary = "Provider 未返回账户额度"
        return auth_status, quota_status, quota_summary

    if backend == "codebuddy":
        # CodeBuddy's current ACP live account model catalogue proves that the
        # local CLI can access the account, but it has no documented balance
        # endpoint.  Do not infer a balance from task/session Credits.
        if (
            str(catalog_meta.get("source") or "") == "codebuddy_acp_account_live"
            and str(catalog_meta.get("status") or "") == "complete"
        ):
            auth_status = "verified"
        return auth_status, "not_supported", "官方 CLI/ACP 未提供余额接口"

    return auth_status, quota_status, quota_summary


def _runtime_profile_projection(*, refresh_profile: bool = False) -> dict[str, Any]:
    """Compose one read-only snapshot for the Runtime Profile MCP card."""
    config = VoyagerUserConfig.load()
    registry = _crew_registry_service()
    catalog = registry.catalog(
        probe=bool(refresh_profile), include_models=bool(refresh_profile)
    )
    models: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []
    for crew in list(catalog.get("crew") or []):
        if not isinstance(crew, dict):
            continue
        backend = str(crew.get("backend") or "").strip().lower()
        if not backend:
            continue
        health = crew.get("health") if isinstance(crew.get("health"), dict) else {}
        catalog_meta = crew.get("model_catalog") if isinstance(crew.get("model_catalog"), dict) else {}
        auth_status, quota_status, quota_summary = _runtime_profile_account_state(
            backend=backend,
            health=health,
            catalog_meta=catalog_meta,
            refresh_profile=refresh_profile,
        )
        accounts.append({
            "backend": backend,
            "display_name": str(crew.get("display_name") or backend),
            "availability": str(health.get("availability") or "unknown"),
            "version": health.get("version"),
            "auth_status": auth_status,
            "model_catalog_status": str(catalog_meta.get("status") or health.get("model_catalog_status") or "unknown"),
            "model_catalog_source": str(catalog_meta.get("source") or "unknown"),
            "last_successful_model": health.get("last_successful_model"),
            "quota_status": quota_status,
            "quota_summary": quota_summary,
        })
        model_rows = list(crew.get("models") or []) if refresh_profile else []
        for item in model_rows:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            reasoning = item.get("reasoning") if isinstance(item.get("reasoning"), dict) else {}
            billing = metadata.get("billing") if isinstance(metadata.get("billing"), dict) else {}
            reference_multiplier = item.get("reference_multiplier")
            if not isinstance(reference_multiplier, (int, float)) or isinstance(reference_multiplier, bool) or reference_multiplier < 0:
                reference_multiplier = billing.get("multiplier", billing.get("price_factor"))
            if not isinstance(reference_multiplier, (int, float)) or isinstance(reference_multiplier, bool) or reference_multiplier < 0:
                reference_multiplier = None
            models.append({
                "backend": backend,
                "model_id": str(item.get("model_id") or ""),
                "display_name": str(item.get("display_name") or item.get("model_id") or ""),
                "available": item.get("available") if isinstance(item.get("available"), bool) else None,
                "routable": item.get("routable") if isinstance(item.get("routable"), bool) else None,
                "routability_status": str(item.get("routability_status") or "unknown"),
                "reference_multiplier": float(reference_multiplier) if reference_multiplier is not None else None,
                "context_window_tokens": item.get("context_window_tokens"),
                "supported_efforts": list(reasoning.get("supported_efforts") or [])[:16],
                "source": str(item.get("source") or "unknown"),
            })
    return {
        "schema": "tp-voyager.runtime_profile/v1",
        "scope": "current_user_configuration",
        "refresh_mode": "live" if refresh_profile else "catalog",
        "observed_at": catalog.get("updated_at") or time.time(),
        "config": _runtime_profile_config_projection(config),
        "models": models,
        "accounts": accounts,
    }


@_mcp_tool(
    meta={
        "ui": {"resourceUri": VOYAGER_RUNTIME_PROFILE_URI},
        "openai/outputTemplate": VOYAGER_RUNTIME_PROFILE_URI,
        "openai/toolInvocation/invoking": "正在加载 TP-Voyager 运行与账户…",
        "openai/toolInvocation/invoked": "TP-Voyager 运行与账户已就绪",
    },
    structured_output=True,
)
def voyager_overview(
    limit: int = 5,
    include_profile: bool = False,
    refresh_profile: bool = False,
) -> dict[str, Any]:
    """Return compact voyage progress and an optional read-only Runtime Profile."""
    try:
        response = {"ok": True, **_voyage_overview_service().overview(limit=limit)}
        if include_profile:
            response["runtime_profile"] = _runtime_profile_projection(
                refresh_profile=refresh_profile
            )
        return response
    except (RuntimePersistenceError, VoyagerUserConfigError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__}


@_mcp_tool(
    meta={
        "ui": {"resourceUri": VOYAGER_PANEL_URI},
        "openai/outputTemplate": VOYAGER_PANEL_URI,
        "openai/toolInvocation/invoking": "正在加载 TP-Voyager 任务…",
        "openai/toolInvocation/invoked": "TP-Voyager 任务已就绪",
    },
    structured_output=True,
)
def render_voyager_panel(
    task_id: str = "",
    presentation_group_id: str = "",
    task_ids: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Render an exact, read-only Agent snapshot for the MCP Apps panel.

    Selection is explicit: one ``task_id``, one ``presentation_group_id``, or
    an explicit bounded ``task_ids`` list. No recent/global/correlation-based
    heuristic is allowed to choose tasks for the current conversation.
    """
    try:
        bounded_limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        return {
            "ok": False, "schema": "tp-voyager.agent_panel/v1",
            "reason_code": "INVALID_LIMIT",
            "error": {"message": "limit must be an integer between 1 and 1000"},
        }
    canonical_task_id = str(task_id or "").strip()
    canonical_group_id = str(presentation_group_id or "").strip()
    explicit_task_ids = list(task_ids or [])
    selector_count = int(bool(canonical_task_id)) + int(bool(canonical_group_id)) + int(bool(explicit_task_ids))
    if selector_count > 1:
        return {
            "ok": False, "schema": "tp-voyager.agent_panel/v1",
            "reason_code": "AMBIGUOUS_PANEL_SELECTOR",
            "error": {"message": "pass exactly one of task_id, presentation_group_id, or task_ids"},
        }
    if len(explicit_task_ids) > 16:
        return {
            "ok": False, "schema": "tp-voyager.agent_panel/v1",
            "reason_code": "INVALID_PANEL_SELECTOR",
            "error": {"message": "task_ids may contain at most 16 explicit task ids"},
        }
    try:
        projection = _voyage_agent_projection()
        if canonical_task_id:
            detail = projection.detail(canonical_task_id, limit=bounded_limit)
            if not bool(detail.get("ok")):
                return {
                    "ok": False, "schema": "tp-voyager.agent_panel/v1",
                    "reason_code": str(detail.get("reason_code") or "TASK_NOT_FOUND"),
                    "task_id": detail.get("task_id") or canonical_task_id,
                    "error": {"message": "TP-Voyager task was not found."},
                }
            return {**detail, "schema": "tp-voyager.agent_panel/v1", "mode": "detail"}
        if canonical_group_id or explicit_task_ids:
            grouped = projection.group(
                presentation_group_id=canonical_group_id,
                task_ids=explicit_task_ids or None,
                limit=bounded_limit,
            )
            if not bool(grouped.get("ok")):
                return {
                    **grouped, "schema": "tp-voyager.agent_panel/v1", "mode": "group",
                    "error": {"message": "TP-Voyager explicit task group was not found."},
                }
            return {**grouped, "schema": "tp-voyager.agent_panel/v1", "mode": "group"}
        return {
            "ok": True, "schema": "tp-voyager.agent_panel/v1", "mode": "empty",
            "scope": "current_conversation", "tasks": [], "conversation": [],
            "timeline": [], "files": [], "usage": {}, "error": None,
        }
    except (RuntimePersistenceError, ValueError, TypeError):
        return {
            "ok": False, "schema": "tp-voyager.agent_panel/v1",
            "reason_code": "OBSERVABILITY_UNAVAILABLE",
            "error": {"message": "TP-Voyager observability data is unavailable."},
        }


@_mcp_tool(
    meta={
        "ui": {"resourceUri": VOYAGER_PANEL_URI},
        "openai/outputTemplate": VOYAGER_PANEL_URI,
        "openai/toolInvocation/invoking": "正在启动 TP-Voyager 任务…",
        "openai/toolInvocation/invoked": "TP-Voyager 任务已启动",
    },
    structured_output=True,
)
def task_dispatch(
    objective: str,
    crew: str,
    task_kind: str,
    cwd: str = "",
    model: str = "",
    model_parameters: dict[str, Any] | None = None,
    access_mode: str = "read_only",
    idempotency_key: str = "",
    context_id: str = "",
    context_files: list[str] | None = None,
    read_scope: dict[str, Any] | None = None,
    model_policy: dict[str, Any] | None = None,
    worker_profile_ref: dict[str, Any] | None = None,
    worker_skill_refs: list[dict[str, Any]] | None = None,
    input_artifact_refs: list[dict[str, Any]] | None = None,
    trusted_instruction_refs: list[dict[str, Any]] | None = None,
    run_control: dict[str, Any] | None = None,
    step_key: str = "",
    apply_receipt: dict[str, Any] | None = None,
    verification_policy: dict[str, Any] | None = None,
    correlation_id: str = "",
    presentation_group_id: str = "",
    timeout_seconds: int = 300,
    required_capabilities: list[str] | None = None,
    patch_policy: dict[str, Any] | None = None,
    repository_research: dict[str, Any] | None = None,
    repository_snapshot_ref: dict[str, Any] | None = None,
    scope_segment: dict[str, Any] | None = None,
    workspace_strategy: str = "isolated_patch",
) -> dict[str, Any]:
    """Dispatch one explicit Captain-selected Crew task under bounded policy.

    ``read_scope`` is the vendor-neutral read-only contract.  Legacy
    ``context_files`` remains accepted for CodeBuddy Context Manifest
    compatibility.  TP-Voyager never chooses a
    model or resolves an unverified Worker profile on the Crew's behalf.
    ``repository_research`` is a separate bounded GitHub acquisition contract;
    it never widens normal ``read_only`` or ``small_patch`` semantics.
    """
    normalized_crew = str(crew or "").strip().lower()
    normalized_kind = str(task_kind or "").strip().lower()
    normalized_mode = str(access_mode or "read_only").strip().lower()

    def reject(reason_code: str, detail: str) -> dict[str, Any]:
        return {
            "ok": False,
            "schema": "tp-voyager.dispatch/v1",
            "reason_code": reason_code,
            "detail": detail,
            "crew": normalized_crew or None,
            "task_kind": normalized_kind or None,
            "selection_performed": False,
            "dispatch_performed": False,
        }

    normalized_workspace_strategy = str(workspace_strategy or "isolated_patch").strip().lower()
    if normalized_workspace_strategy not in set(WORKSPACE_STRATEGIES):
        return reject("INVALID_WORKSPACE_STRATEGY", "workspace_strategy must be one of model_only/live_readonly/frozen_context/isolated_patch")

    # Workspace policy is an explicit dispatch contract.  Do not prepare an
    # isolation workspace for model checks or read-only inspection.  The
    # strategy only controls workspace preparation; it never changes the
    # durable task_result source of truth.
    if normalized_workspace_strategy == "model_only":
        cwd = ""
        repository_snapshot_ref = None
        repository_research = None
    elif normalized_workspace_strategy == "live_readonly":
        patch_policy = None

    parsed_policy: PatchPolicy | None = None
    if patch_policy is not None:
        try:
            parsed_policy = PatchPolicy.from_dict(patch_policy)
        except (TypeError, ValueError) as exc:
            return reject("INVALID_PATCH_POLICY", str(exc))

    parsed_model_policy: ModelPolicy | None = None
    if model_policy is not None:
        try:
            parsed_model_policy = ModelPolicy.from_dict(model_policy)
        except (TypeError, ValueError) as exc:
            return reject("INVALID_MODEL_POLICY", str(exc))

    parsed_model_parameters: ModelParameters | None = None
    if model_parameters is not None:
        try:
            parsed_model_parameters = ModelParameters.from_dict(model_parameters)
        except (TypeError, ValueError) as exc:
            return reject("INVALID_MODEL_PARAMETERS", str(exc))
        if not str(model or "").strip():
            return reject("MODEL_PARAMETERS_MODEL_REQUIRED", "model_parameters requires an explicit model")

    parsed_research: RepositoryResearchSpec | None = None
    if repository_research is not None:
        try:
            parsed_research = RepositoryResearchSpec.from_dict(repository_research)
        except (TypeError, ValueError) as exc:
            return reject("INVALID_REPOSITORY_RESEARCH", str(exc))

    parsed_snapshot_ref: RepositorySnapshotRef | None = None
    if repository_snapshot_ref is not None:
        try:
            parsed_snapshot_ref = RepositorySnapshotRef.from_dict(repository_snapshot_ref)
        except (TypeError, ValueError) as exc:
            return reject("INVALID_REPOSITORY_SNAPSHOT_REF", str(exc))
    try:
        parsed_scope_segment = ScopeSegmentSpec.from_dict(scope_segment)
    except (TypeError, ValueError) as exc:
        return reject("INVALID_SCOPE_SEGMENT", str(exc))

    if normalized_kind == "repository_research":
        if parsed_research is None:
            return reject("REPOSITORY_RESEARCH_REQUIRED", "repository_research contract is required")
        if normalized_mode != "read_only":
            return reject("REPOSITORY_RESEARCH_READ_ONLY", "repository_research only supports read_only")
        if str(cwd or "").strip():
            return reject("REPOSITORY_RESEARCH_CWD_CONFLICT", "cwd must be empty; target_directory owns the research workspace")
        if context_id or context_files:
            return reject("REPOSITORY_RESEARCH_CONTEXT_CONFLICT", "repository_research uses read_scope over the acquired source")
    elif parsed_research is not None or parsed_snapshot_ref is not None or scope_segment is not None:
        return reject("REPOSITORY_RESEARCH_NOT_APPLICABLE", "repository research snapshot/segment contracts are only valid for repository_research task_kind")

    parsed_scope: ReadScope | None = None
    supplied_files = list(context_files or [])
    if supplied_files and read_scope is not None:
        return reject("INVALID_READ_SCOPE", "pass read_scope or legacy context_files, not both")
    if read_scope is not None:
        try:
            parsed_scope = ReadScope.from_dict(read_scope)
        except (TypeError, ValueError) as exc:
            return reject("INVALID_READ_SCOPE", str(exc))
    elif supplied_files:
        if normalized_crew != "codebuddy" or normalized_mode != "read_only":
            return reject(
                "CONTEXT_FILES_NOT_APPLICABLE",
                "legacy context_files is only accepted for CodeBuddy read-only dispatch; use read_scope",
            )

    if normalized_kind == "repository_research" and parsed_scope is None:
        return reject("REPOSITORY_RESEARCH_SCOPE_REQUIRED", "repository_research requires explicit read_scope")

    effective_cwd = str(cwd or "")
    effective_context_id = str(context_id or "").strip()
    if supplied_files and effective_context_id:
        return reject("INVALID_CONTEXT_REQUEST", "pass context_id or context_files, not both")
    if parsed_scope is not None and effective_context_id:
        return reject("INVALID_CONTEXT_REQUEST", "pass context_id or read_scope, not both")
    if parsed_scope is not None and normalized_mode not in {"read_only", "verification"}:
        return reject("READ_SCOPE_NOT_APPLICABLE", "read_scope is only accepted for read_only or verification access_mode")
    if effective_context_id and normalized_crew == "qoder":
        return reject("CONTEXT_ID_NOT_APPLICABLE", "Qoder Captain dispatch uses read_scope, not Context Manifest ids")

    resolved_files: tuple[str, ...] = ()
    context_auto_created = False
    if supplied_files and normalized_workspace_strategy != "model_only":
        try:
            registered = _context_service().register(effective_cwd, supplied_files)
            effective_context_id = str(registered.manifest.get("context_id") or "")
            context_auto_created = True
        except (ValueError, TypeError, ContextError) as exc:
            return reject("CONTEXT_INVALID", str(exc))
        except RuntimePersistenceError:
            return reject("RUNTIME_UNAVAILABLE", "runtime database unavailable")
    elif parsed_scope is not None and normalized_kind != "repository_research" and normalized_mode != "verification" and normalized_workspace_strategy != "model_only":
        try:
            resolved_files = tuple(_context_service().resolve_read_scope(effective_cwd, parsed_scope))
            # ContextManifest is the provider-neutral Scope Manifest truth.
            # CodeBuddy consumes it directly; Qoder keeps using ACP allowed_paths
            # but shares the same hashable scope provenance.
            registered = _context_service().register(effective_cwd, resolved_files)
            effective_context_id = str(registered.manifest.get("context_id") or "")
            context_auto_created = True
        except (ValueError, TypeError, ContextError) as exc:
            return reject("READ_SCOPE_INVALID", str(exc))
        except RuntimePersistenceError:
            return reject("RUNTIME_UNAVAILABLE", "runtime database unavailable")

    parsed_profile: WorkerProfileRef | None = None
    profile_content = ""
    if worker_profile_ref is not None:
        try:
            parsed_profile = WorkerProfileRef.from_dict(worker_profile_ref)
        except (TypeError, ValueError) as exc:
            return reject("WORKER_PROFILE_INVALID", str(exc))

    parsed_skills: tuple[WorkerSkillRef, ...] = ()
    skill_content: tuple[str, ...] = ()
    if worker_skill_refs is not None:
        try:
            if len(worker_skill_refs) > 8:
                raise ValueError("worker_skill_refs exceeds 8 entries")
            parsed_skills = tuple(WorkerSkillRef.from_dict(item) for item in worker_skill_refs)
        except (TypeError, ValueError) as exc:
            return reject("WORKER_SKILL_INVALID", str(exc))
    try:
        parsed_artifacts = canonical_input_artifact_refs(input_artifact_refs)
    except (TypeError, ValueError) as exc:
        return reject("INPUT_ARTIFACT_INVALID", str(exc))
    artifact_content: tuple[str, ...] = ()

    parsed_instructions: tuple[TrustedInstructionRef, ...] = ()
    instruction_content: tuple[str, ...] = ()
    if trusted_instruction_refs is not None:
        try:
            if not isinstance(trusted_instruction_refs, list) or len(trusted_instruction_refs) > 8:
                raise ValueError("trusted_instruction_refs must contain at most 8 entries")
            parsed_instructions = tuple(TrustedInstructionRef.from_dict(item) for item in trusted_instruction_refs)
        except (TypeError, ValueError) as exc:
            return reject("TRUSTED_INSTRUCTION_INVALID", str(exc))

    parsed_run_control: RunControlSpec | None = None
    if run_control is not None:
        try:
            parsed_run_control = RunControlSpec.from_dict(run_control)
        except (TypeError, ValueError) as exc:
            return reject("RUN_CONTROL_INVALID", str(exc))
    canonical_step_key = str(step_key or "").strip()
    if canonical_step_key:
        if len(canonical_step_key) > 160 or "\x00" in canonical_step_key or any(ord(ch) < 32 for ch in canonical_step_key):
            return reject("STEP_KEY_INVALID", "step_key must be printable and at most 160 characters")
        if parsed_run_control is None:
            return reject("RUN_CONTROL_REQUIRED", "step_key requires run_control")
    elif parsed_run_control is not None:
        return reject("STEP_KEY_REQUIRED", "run_control requires an explicit step_key")

    parsed_apply_receipt: ApplyReceipt | None = None
    if apply_receipt is not None:
        try:
            parsed_apply_receipt = ApplyReceipt.from_dict(apply_receipt)
        except (TypeError, ValueError) as exc:
            return reject("APPLY_RECEIPT_INVALID", str(exc))

    parsed_verification_policy: VerificationPolicy | None = None
    if verification_policy is not None:
        try:
            parsed_verification_policy = VerificationPolicy.from_dict(verification_policy)
        except (TypeError, ValueError) as exc:
            return reject("VERIFICATION_POLICY_INVALID", str(exc))
    if normalized_mode == "verification":
        if normalized_kind != "verify_only":
            return reject("ACCESS_MODE_TASK_MISMATCH", "verification access_mode is only valid for verify_only")
        if parsed_apply_receipt is None:
            return reject("APPLY_RECEIPT_REQUIRED", "verification requires apply_receipt")
        if parsed_verification_policy is None:
            return reject("VERIFICATION_POLICY_REQUIRED", "verification requires verification_policy")
        if parsed_scope is None:
            return reject("VERIFICATION_SCOPE_REQUIRED", "verification requires read_scope")
        if not str(cwd or "").strip():
            return reject("VERIFICATION_PASSENGER_CWD_REQUIRED", "verification requires Passenger workspace cwd")
    elif parsed_apply_receipt is not None or parsed_verification_policy is not None:
        return reject("VERIFICATION_CONTRACT_NOT_APPLICABLE", "apply_receipt and verification_policy are only valid for verification access_mode")

    external_correlation_id = str(correlation_id or "").strip()
    if external_correlation_id:
        if (
            len(external_correlation_id) > 160
            or "\x00" in external_correlation_id
            or any(ord(ch) < 32 for ch in external_correlation_id)
        ):
            return reject("INVALID_CORRELATION_ID", "correlation_id must be printable and at most 160 characters")

    external_presentation_group_id = str(presentation_group_id or "").strip()
    if external_presentation_group_id:
        if (
            len(external_presentation_group_id) > 160
            or not external_presentation_group_id.isascii()
            or not external_presentation_group_id[0].isalnum()
            or any(not (ch.isalnum() or ch in "._-") for ch in external_presentation_group_id)
        ):
            return reject(
                "INVALID_PRESENTATION_GROUP_ID",
                "presentation_group_id must use only letters, digits, dot, underscore, or hyphen and be at most 160 characters",
            )

    context_root_hash = ""
    if effective_context_id:
        try:
            context_root_hash = str(_context_service().get(effective_context_id).get("root_hash") or "")
        except (ContextError, RuntimePersistenceError) as exc:
            return reject("CONTEXT_INVALID", str(exc))
    captain_contract = _captain_request_contract(
        objective=objective,
        crew=normalized_crew,
        task_kind=normalized_kind,
        cwd=str(cwd or ""),
        model=str(model or "").strip(),
        model_parameters=(parsed_model_parameters.to_dict() if parsed_model_parameters is not None else None),
        access_mode=normalized_mode,
        context_id=str(context_id or "").strip(),
        context_files=sorted(set(supplied_files)),
        context_root_hash=context_root_hash,
        resolved_read_files=list(resolved_files),
        timeout_seconds=int(timeout_seconds),
        required_capabilities=sorted(set(required_capabilities or ())),
        patch_policy=parsed_policy.to_dict() if parsed_policy is not None else None,
        model_policy=parsed_model_policy.to_dict() if parsed_model_policy is not None else None,
        read_scope=parsed_scope.to_dict() if parsed_scope is not None else None,
        worker_profile_ref=parsed_profile.to_dict() if parsed_profile is not None else None,
        worker_skill_refs=[item.to_dict() for item in parsed_skills],
        input_artifact_refs=[item.to_dict() for item in parsed_artifacts],
        trusted_instruction_refs=[item.to_dict() for item in parsed_instructions],
        run_control=parsed_run_control.to_dict() if parsed_run_control is not None else None,
        step_key=canonical_step_key,
        apply_receipt=parsed_apply_receipt.to_dict() if parsed_apply_receipt is not None else None,
        verification_policy=parsed_verification_policy.to_dict() if parsed_verification_policy is not None else None,
        repository_research=parsed_research.to_dict() if parsed_research is not None else None,
        repository_snapshot_ref=parsed_snapshot_ref.to_dict() if parsed_snapshot_ref is not None else None,
        scope_segment=parsed_scope_segment.to_dict(),
        correlation_id=external_correlation_id,
        presentation_group_id=external_presentation_group_id,
        workspace_strategy=normalized_workspace_strategy,
    )
    canonical_key = str(idempotency_key or "").strip()
    if canonical_key and parsed_research is None:
        if len(canonical_key) > 128:
            return reject("INVALID_REQUEST", "idempotency_key must be at most 128 characters")
        try:
            runtime = _runtime_service()
            pair = runtime.resolve_idempotent(canonical_key)
        except RuntimePersistenceError:
            return reject("RUNTIME_UNAVAILABLE", "runtime database unavailable")
        if pair is not None:
            _, stored_task_id = pair
            durable = runtime.get_task(stored_task_id)
            session = runtime.get_session(stored_task_id) if durable is not None else None
            metadata = parse_session_metadata(session.metadata_json) if session is not None else {}
            routing = metadata.get("routing_metadata") if isinstance(metadata.get("routing_metadata"), dict) else {}
            stored_contract = routing.get("captain_request_contract") if isinstance(routing, dict) else None
            if stored_contract is not None:
                if stored_contract != captain_contract:
                    return reject("IDEMPOTENCY_CONFLICT", "idempotency_key is already bound to a different request or Artifact snapshot")
                stored_policy = routing.get("effective_model_policy") if isinstance(routing, dict) else None
                return {
                    "ok": True,
                    "schema": "tp-voyager.dispatch/v1",
                    "crew": normalized_crew,
                    "task_kind": normalized_kind,
                    "selection_performed": False,
                    "dispatch_performed": False,
                    "replayed": True,
                    "effective_model_policy": dict(stored_policy) if isinstance(stored_policy, dict) else {},
                    **_public(_task_state_from_durable(durable, runtime)),
                    **({"presentation_group_id": external_presentation_group_id} if external_presentation_group_id else {}),
                }

    if parsed_profile is not None:
        try:
            profile_content = _worker_profile_resolver().resolve(parsed_profile).content
        except (WorkerProfileError, RuntimePersistenceError) as exc:
            return reject("WORKER_PROFILE_INVALID", str(exc))
    if parsed_skills:
        try:
            skill_resolver = _worker_skill_resolver()
            skill_content = tuple(skill_resolver.resolve(item).content for item in parsed_skills)
        except (ValueError, WorkerProfileError) as exc:
            return reject("WORKER_SKILL_INVALID", str(exc))
    if parsed_instructions:
        try:
            instruction_content = resolve_trusted_instruction_refs(parsed_instructions, _trusted_instruction_roots())
        except (ValueError, TrustedTextError, RuntimePersistenceError) as exc:
            return reject("TRUSTED_INSTRUCTION_INVALID", str(exc))

    verification_workspace = None
    verification_subject: dict[str, Any] = {}
    verification_source_cwd = ""
    verification_base_revision = ""
    if normalized_mode == "verification":
        try:
            subject_service = VerificationSubjectService(
                _get_runtime_database(),
                _get_runtime_database().path.parent / "workspaces",
            )
            verification_workspace = subject_service.prepare(parsed_apply_receipt, str(cwd or ""))  # type: ignore[arg-type]
            verification_source_cwd = verification_workspace.source_root
            verification_base_revision = verification_workspace.base_revision
            effective_cwd = verification_workspace.worktree_root
            resolved_files = tuple(_context_service().resolve_read_scope(effective_cwd, parsed_scope))  # type: ignore[arg-type]
            registered = _context_service().register(effective_cwd, resolved_files)
            effective_context_id = str(registered.manifest.get("context_id") or "")
            context_auto_created = True
            verification_subject = {
                "schema": "tp-voyager.verification_subject/v1",
                "repository_identity": parsed_apply_receipt.repository_identity,  # type: ignore[union-attr]
                "base_commit": parsed_apply_receipt.base_commit,  # type: ignore[union-attr]
                "base_tree_hash": parsed_apply_receipt.base_tree_hash,  # type: ignore[union-attr]
                "patch_artifact_id": parsed_apply_receipt.patch_artifact_id,  # type: ignore[union-attr]
                "patch_sha256": parsed_apply_receipt.patch_sha256,  # type: ignore[union-attr]
                "result_tree_hash": parsed_apply_receipt.result_tree_hash,  # type: ignore[union-attr]
                "apply_receipt_sha256": parsed_apply_receipt.receipt_sha256,  # type: ignore[union-attr]
                "context_id": effective_context_id,
                "context_root_hash": str(registered.manifest.get("root_hash") or ""),
            }
        except VerificationSubjectError as exc:
            return reject(exc.code, exc.detail)
        except (ContextError, RuntimePersistenceError, OSError, ValueError) as exc:
            if verification_workspace is not None:
                try:
                    PatchWorkspaceService(_get_runtime_database().path.parent / "workspaces").cleanup(verification_workspace)
                except PatchWorkspaceCleanupError:
                    pass
            return reject("VERIFICATION_WORKSPACE_FAILED", str(exc))

    research_workspace = None
    research_routing: dict[str, Any] | None = None
    research_request_fingerprint: str | None = None
    if parsed_research is not None:
        # ``parsed_scope`` is guaranteed above for repository_research.
        research_request_fingerprint = _repository_research_captain_fingerprint(
            objective=objective, crew=crew, task_kind=task_kind, model=model,
            model_parameters=parsed_model_parameters,
            access_mode=access_mode, timeout_seconds=timeout_seconds,
            read_scope=parsed_scope,  # type: ignore[arg-type]
            model_policy=parsed_model_policy, worker_profile_ref=parsed_profile,
            correlation_id=external_correlation_id,
            presentation_group_id=external_presentation_group_id,
            required_capabilities=required_capabilities,
            repository_research=parsed_research,
            repository_snapshot_ref=parsed_snapshot_ref,
            scope_segment=parsed_scope_segment,
        )
        canonical_key = str(idempotency_key or "").strip()
        if canonical_key:
            if len(canonical_key) > 128:
                return reject("INVALID_REQUEST", "idempotency_key must be at most 128 characters")
            try:
                runtime = _runtime_service()
                pair = runtime.resolve_idempotent(canonical_key)
            except RuntimePersistenceError:
                return reject("RUNTIME_UNAVAILABLE", "runtime database unavailable")
            if pair is not None:
                _, stored_task_id = pair
                durable = runtime.get_task(stored_task_id)
                session = runtime.get_session(stored_task_id) if durable is not None else None
                metadata = parse_session_metadata(session.metadata_json) if session is not None else {}
                routing = metadata.get("routing_metadata") if isinstance(metadata.get("routing_metadata"), dict) else {}
                stored_research = routing.get("repository_research") if isinstance(routing, dict) else None
                stored_fingerprint = (
                    str(stored_research.get("captain_request_fingerprint") or "")
                    if isinstance(stored_research, dict) else ""
                )
                if not stored_fingerprint or stored_fingerprint != research_request_fingerprint:
                    return reject("IDEMPOTENCY_CONFLICT", "idempotency_key is already bound to a different request")
                replay = {
                    "ok": True,
                    "schema": "tp-voyager.dispatch/v1",
                    "crew": normalized_crew,
                    "task_kind": normalized_kind,
                    "selection_performed": False,
                    "dispatch_performed": False,
                    "replayed": True,
                    **_public(_task_state_from_durable(durable, runtime)),
                    "repository_research": {
                        "target_directory": str(metadata.get("cwd") or parsed_research.target_directory),
                        "source_url": str(stored_research.get("url") or parsed_research.url),
                        "commit": str(stored_research.get("commit") or ""),
                        "repository_size_bytes": stored_research.get("repository_size_bytes"),
                        "report_path": str(stored_research.get("report_path") or parsed_research.report_path),
                        "scope_segment_index": stored_research.get("scope_segment_index"),
                        "scope_segment_count": stored_research.get("scope_segment_count"),
                        "scope_segment_context_id": stored_research.get("scope_segment_context_id"),
                    },
                    "repository_snapshot_ref": {
                        "source_task_id": str(stored_research.get("snapshot_source_task_id") or stored_task_id),
                        "commit": str(stored_research.get("commit") or ""),
                        "scope_manifest_id": str(stored_research.get("scope_manifest_id") or ""),
                        "scope_root_hash": str(stored_research.get("scope_root_hash") or ""),
                    },
                }
                if external_correlation_id:
                    replay["correlation_id"] = external_correlation_id
                if external_presentation_group_id:
                    replay["presentation_group_id"] = external_presentation_group_id
                if parsed_profile is not None:
                    replay["worker_profile_ref"] = parsed_profile.to_dict()
                return replay

        service = RepositoryResearchService()
        try:
            snapshot_source_task_id = ""
            if parsed_snapshot_ref is None:
                research_workspace = service.prepare(parsed_research)
                effective_cwd = research_workspace.root
                prefixed_scope = service.prefix_read_scope(parsed_scope)  # type: ignore[arg-type]
                manifest_files = _context_service().resolve_scope_manifest(effective_cwd, prefixed_scope)
                full_manifest_result = _context_service().register_scope_manifest(effective_cwd, manifest_files)
                full_manifest = full_manifest_result.manifest
            else:
                runtime = _runtime_service()
                source_task = runtime.get_task(parsed_snapshot_ref.source_task_id)
                source_session = runtime.get_session(parsed_snapshot_ref.source_task_id) if source_task is not None else None
                if source_task is None or source_session is None or source_task.status != "completed":
                    raise RepositoryResearchError("repository snapshot source task is unavailable or not completed")
                source_meta = parse_session_metadata(source_session.metadata_json)
                source_routing = source_meta.get("routing_metadata") if isinstance(source_meta.get("routing_metadata"), dict) else {}
                source_research = source_routing.get("repository_research") if isinstance(source_routing, dict) else None
                if not isinstance(source_research, dict):
                    raise RepositoryResearchError("repository snapshot source task has no research provenance")
                if str(source_research.get("url") or "") != parsed_research.url:
                    raise RepositoryResearchError("repository snapshot URL does not match Captain contract")
                if str(source_research.get("commit") or "") != parsed_snapshot_ref.commit:
                    raise RepositoryResearchError("repository snapshot commit does not match source task")
                root = str(source_meta.get("cwd") or "")
                if not root:
                    raise RepositoryResearchError("repository snapshot source workspace is unavailable")
                research_workspace = service.reuse(
                    root=root, expected_url=parsed_research.url, commit=parsed_snapshot_ref.commit,
                    report_path=parsed_research.report_path, max_size_bytes=parsed_research.max_size_bytes,
                )
                effective_cwd = research_workspace.root
                manifest_check = _context_service().verify(parsed_snapshot_ref.scope_manifest_id, effective_cwd)
                if not bool(manifest_check.get("valid")):
                    raise RepositoryResearchError("repository snapshot scope manifest drift")
                full_manifest = _context_service().get(parsed_snapshot_ref.scope_manifest_id)
                if str(full_manifest.get("root_hash") or "") != parsed_snapshot_ref.scope_root_hash:
                    raise RepositoryResearchError("repository snapshot scope manifest drift")
                if str(manifest_check.get("current_root_hash") or "") != parsed_snapshot_ref.scope_root_hash:
                    raise RepositoryResearchError("repository snapshot scope manifest drift")
                snapshot_source_task_id = parsed_snapshot_ref.source_task_id
                prefixed_scope = service.prefix_read_scope(parsed_scope)  # type: ignore[arg-type]
                # Scope selectors are re-evaluated only to prove the Captain is
                # requesting the same bounded source set, never to create a new
                # hidden snapshot truth.
                requested_files = _context_service().resolve_scope_manifest(effective_cwd, prefixed_scope)
                if requested_files != [str(item.get("relpath") or "") for item in full_manifest.get("entries", [])]:
                    raise RepositoryResearchError("repository snapshot read_scope differs from source manifest")

            segment_max_files = min(int(parsed_scope.max_files), 256)  # type: ignore[union-attr]
            segment_max_bytes = int(parsed_scope.max_bytes)  # type: ignore[union-attr]
            if normalized_crew == "codebuddy":
                # CodeBuddy's immutable Context rendering is deliberately
                # bounded more tightly than the generic ContextManifest store.
                segment_max_bytes = min(segment_max_bytes, 256 * 1024)
            segments = _context_service().scope_segments(
                str(full_manifest.get("context_id") or ""),
                max_files=segment_max_files, max_bytes=segment_max_bytes,
            )
            if parsed_scope_segment.index >= len(segments):
                raise RepositoryResearchError(
                    f"scope segment index must be between 0 and {max(0, len(segments)-1)}"
                )
            segment_result = _context_service().register_scope_segment(
                effective_cwd, str(full_manifest.get("context_id") or ""),
                index=parsed_scope_segment.index, max_files=segment_max_files, max_bytes=segment_max_bytes,
            )
            segment_manifest = segment_result.manifest
            effective_context_id = str(segment_manifest.get("context_id") or "")
            resolved_files = tuple(str(item.get("relpath") or "") for item in segment_manifest.get("entries", []))
            context_auto_created = True
            parsed_scope = prefixed_scope
            research_routing = {
                **research_workspace.routing_metadata(),
                "acquisition": ("runtime_snapshot_reuse" if parsed_snapshot_ref is not None else research_workspace.routing_metadata().get("acquisition")),
                "captain_request_fingerprint": research_request_fingerprint,
                "scope_manifest_id": str(full_manifest.get("context_id") or ""),
                "scope_root_hash": str(full_manifest.get("root_hash") or ""),
                "scope_file_count": int(full_manifest.get("file_count") or 0),
                "scope_total_bytes": int(full_manifest.get("total_bytes") or 0),
                "scope_segment_index": parsed_scope_segment.index,
                "scope_segment_count": len(segments),
                "scope_segment_context_id": effective_context_id,
                "scope_segment_root_hash": str(segment_manifest.get("root_hash") or ""),
                "scope_segment_file_count": int(segment_manifest.get("file_count") or 0),
                "scope_segment_total_bytes": int(segment_manifest.get("total_bytes") or 0),
                "scope_segment_max_files": segment_max_files,
                "scope_segment_max_bytes": segment_max_bytes,
            }
            if snapshot_source_task_id:
                research_routing["snapshot_source_task_id"] = snapshot_source_task_id
        except (RepositoryResearchError, ContextError, RuntimePersistenceError, OSError, ValueError) as exc:
            if research_workspace is not None and parsed_snapshot_ref is None:
                service.cleanup(research_workspace)
            return reject("REPOSITORY_RESEARCH_PREPARE_FAILED", str(exc))

    result = _captain_dispatch_service().dispatch(
        CaptainDispatchRequest(
            objective=objective,
            crew=crew,
            task_kind=task_kind,
            cwd=effective_cwd,
            model=model,
            model_parameters=parsed_model_parameters,
            access_mode=access_mode,
            idempotency_key=idempotency_key,
            context_id=effective_context_id,
            timeout_seconds=timeout_seconds,
            required_capabilities=tuple(required_capabilities or ()),
            patch_policy=parsed_policy,
            model_policy=parsed_model_policy,
            read_scope=parsed_scope,
            resolved_read_files=resolved_files,
            worker_profile_ref=parsed_profile,
            worker_profile_content=profile_content,
            worker_skill_refs=parsed_skills,
            worker_skill_content=skill_content,
            input_artifact_refs=parsed_artifacts,
            input_artifact_content=artifact_content,
            trusted_instruction_refs=parsed_instructions,
            trusted_instruction_content=instruction_content,
            run_control=parsed_run_control,
            step_key=canonical_step_key,
            apply_receipt=parsed_apply_receipt,
            verification_policy=parsed_verification_policy,
            verification_subject=verification_subject,
            workspace_source_cwd=verification_source_cwd,
            workspace_mode=("verification_worktree" if verification_workspace is not None else ""),
            workspace_base_revision=verification_base_revision,
            captain_request_contract=captain_contract,
            repository_research=research_routing,
            repository_snapshot_ref=parsed_snapshot_ref,
            scope_segment=parsed_scope_segment,
            correlation_id=external_correlation_id,
            presentation_group_id=external_presentation_group_id,
            workspace_strategy=normalized_workspace_strategy,
        )
    )
    if verification_workspace is not None and (not result.get("ok") or bool(result.get("replayed"))):
        try:
            PatchWorkspaceService(_get_runtime_database().path.parent / "workspaces").cleanup(verification_workspace)
        except PatchWorkspaceCleanupError:
            if result.get("ok"):
                result = {"ok": False, "schema": "tp-voyager.dispatch/v1", "reason_code": "VERIFICATION_WORKSPACE_CLEANUP_FAILED", "detail": "verification replay workspace cleanup failed", "dispatch_performed": False}
    if research_workspace is not None and not result.get("ok") and parsed_snapshot_ref is None:
        RepositoryResearchService.cleanup(research_workspace)
    if research_workspace is not None and result.get("ok"):
        source_task_id = (
            parsed_snapshot_ref.source_task_id
            if parsed_snapshot_ref is not None
            else str(result.get("task_id") or "")
        )
        result = {
            **result,
            "repository_research": {
                "target_directory": research_workspace.root,
                "source_url": research_workspace.source_url,
                "commit": research_workspace.commit,
                "repository_size_bytes": research_workspace.checkout_size_bytes,
                "report_path": research_workspace.report_path,
                "scope_segment_index": parsed_scope_segment.index,
                "scope_segment_count": (research_routing or {}).get("scope_segment_count"),
                "scope_segment_context_id": effective_context_id,
            },
            "repository_snapshot_ref": {
                "source_task_id": source_task_id,
                "commit": research_workspace.commit,
                "scope_manifest_id": str((research_routing or {}).get("scope_manifest_id") or ""),
                "scope_root_hash": str((research_routing or {}).get("scope_root_hash") or ""),
            },
        }

    if context_auto_created:
        result = {
            **result,
            "context_id": effective_context_id,
            "context_auto_created": True,
            "workspace_strategy": normalized_workspace_strategy,
        }
        if parsed_scope is not None:
            result = {**result, "read_scope_resolved_file_count": len(resolved_files)}
    elif parsed_scope is not None:
        result = {**result, "read_scope_resolved_file_count": len(resolved_files)}
    if external_correlation_id:
        result = {**result, "correlation_id": external_correlation_id}
    if external_presentation_group_id:
        result = {**result, "presentation_group_id": external_presentation_group_id}
    if parsed_run_control is not None:
        result = {**result, "run_id": parsed_run_control.run_id, "step_key": canonical_step_key}
    if parsed_profile is not None:
        result = {**result, "worker_profile_ref": parsed_profile.to_dict()}
    return result


@_mcp_tool()
def task_result(task_id: str = "", run_id: str = "", step_key: str = "") -> dict[str, Any]:
    """Return explicit terminal material by task_id or durable run_id + step_key."""
    canonical_task = str(task_id or "").strip()
    canonical_run = str(run_id or "").strip()
    canonical_step = str(step_key or "").strip()
    if canonical_task:
        if canonical_run or canonical_step:
            return {"ok": False, "error": "pass task_id or run_id + step_key, not both"}
        return _task_result_response(canonical_task)
    if not canonical_run or not canonical_step:
        return {"ok": False, "error": "task_id or run_id + step_key is required"}
    try:
        durable = _runtime_service().get_task_by_run_step(canonical_run, canonical_step)
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    if durable is None:
        return {"ok": False, "error": "Unknown run_id + step_key"}
    return _task_result_response(durable.task_id)


@_mcp_tool()
def subagent_status(task_id: str = "", runtime: str = "") -> dict[str, Any]:
    """Inspect one task, one backend, or all registered backend capabilities."""
    if task_id:
        try:
            task = _load_task(task_id)
        except RuntimePersistenceError as exc:
            return {"ok": False, "error": f"runtime database unavailable: {exc}"}
        if task is None:
            return {"ok": False, "error": f"Unknown task_id: {task_id}"}
        return {"ok": True, **_public(task)}
    selected = runtime.strip().lower()
    names = [selected] if selected else _BACKENDS.names()
    backends: list[dict[str, Any]] = []
    for name in names:
        try:
            backend = _backend_for_runtime(name)
            probe = backend.probe()
            capabilities = backend.capabilities().to_dict()
            backends.append({"runtime": name, "ok": True, **probe, "capabilities": capabilities})
        except Exception as exc:
            # Public health reports only the error category, never paths or raw
            # command lines that a backend exception may contain.
            backends.append({"runtime": name, "ok": False, "error": type(exc).__name__})
    return {"ok": all(item.get("ok") for item in backends), "backends": backends}


@_mcp_tool()
def subagent_wait(task_id: str, timeout_seconds: int = 55) -> dict[str, Any]:
    return _task_wait(task_id, timeout_seconds)


@_mcp_tool()
def subagent_result(task_id: str) -> dict[str, Any]:
    return _task_result_response(task_id)


@_mcp_tool()
def subagent_assessment(task_id: str) -> dict[str, Any]:
    return _task_assessment(task_id)


@_mcp_tool()
def subagent_evidence(task_id: str, attempt_id: str = "") -> dict[str, Any]:
    return _task_evidence(task_id, attempt_id)


@_mcp_tool()
def subagent_artifacts(task_id: str, attempt_id: str = "") -> dict[str, Any]:
    return _task_artifacts(task_id, attempt_id)


@_mcp_tool()
def subagent_cancel(task_id: str, propagate_to_children: bool = False) -> dict[str, Any]:
    """Cancel one task; optionally propagate explicitly to its descendants."""
    if not propagate_to_children:
        return _task_cancel(task_id)
    try:
        service = _runtime_service()
        if service.get_task(task_id) is None:
            return {"ok": False, "error": f"Unknown task_id: {task_id}"}
        lineage = service.list_tree(task_id)
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    by_parent: dict[str, list[Any]] = {}
    for item in lineage:
        if item.parent_task_id:
            by_parent.setdefault(item.parent_task_id, []).append(item)
    descendants: list[Any] = []
    stack = list(by_parent.get(task_id, []))
    while stack:
        item = stack.pop()
        descendants.append(item)
        stack.extend(by_parent.get(item.child_task_id, []))
    # Child-first avoids leaving a child running after its parent is cancelled.
    outcomes: list[dict[str, Any]] = []
    for item in reversed(descendants):
        outcome = _task_cancel(item.child_task_id)
        outcomes.append({"task_id": item.child_task_id, **outcome})
    root_outcome = _task_cancel(task_id)
    return {
        **root_outcome,
        "propagate_to_children": True,
        "children": outcomes,
    }


@_mcp_tool()
def subagent_list(runtime: str = "", parent_task_id: str = "") -> dict[str, Any]:
    result = _task_list()
    if not result.get("ok"):
        return result
    selected = runtime.strip().lower()
    parent = parent_task_id.strip()
    tasks = [
        item for item in result.get("tasks", [])
        if (not selected or item.get("runtime") == selected)
        and (not parent or item.get("parent_task_id") == parent)
    ]
    return {"ok": True, "tasks": tasks}


@_mcp_tool()
def subagent_children(task_id: str) -> dict[str, Any]:
    try:
        service = _runtime_service()
        task = service.get_task(task_id)
        if task is None:
            return {"ok": False, "error": f"Unknown task_id: {task_id}"}
        children = service.list_children(task_id)
        return {
            "ok": True,
            "task_id": task_id,
            "children": [
                {
                    "task_id": item.child_task_id,
                    "parent_task_id": item.parent_task_id,
                    "root_task_id": item.root_task_id,
                    "context_id": item.context_id,
                    "agent_profile": item.agent_profile,
                    "execution_mode": item.execution_mode,
                }
                for item in children
            ],
        }
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def subagent_tree(task_id: str) -> dict[str, Any]:
    try:
        service = _runtime_service()
        if service.get_task(task_id) is None:
            return {"ok": False, "error": f"Unknown task_id: {task_id}"}
        rows = service.list_tree(task_id)
        return {
            "ok": True,
            "task_id": task_id,
            "tasks": [
                {
                    "task_id": item.child_task_id,
                    "parent_task_id": item.parent_task_id,
                    "root_task_id": item.root_task_id,
                    "context_id": item.context_id,
                    "agent_profile": item.agent_profile,
                    "execution_mode": item.execution_mode,
                }
                for item in rows
            ],
        }
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}




def _workflow_specs_from_input(stages: list[dict[str, Any]] | None) -> list[WorkflowStageSpec]:
    if not isinstance(stages, list) or not stages:
        raise ValueError("stages must be a non-empty list")
    allowed = {
        "stage_key", "title", "approval_required", "runtime", "agent_profile",
    }
    result: list[WorkflowStageSpec] = []
    for index, raw in enumerate(stages, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"stages[{index}] must be an object")
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(
                f"stages[{index}] has unsupported fields: {', '.join(unknown)}"
            )
        result.append(
            WorkflowStageSpec(
                stage_key=str(raw.get("stage_key") or ""),
                title=str(raw.get("title") or ""),
                approval_required=bool(raw.get("approval_required", False)),
                runtime=(str(raw.get("runtime") or "").strip() or None),
                agent_profile=(
                    str(raw.get("agent_profile") or "").strip() or None
                ),
            )
        )
    return result


@_mcp_tool()
def workflow_create(
    name: str,
    stages: list[dict[str, Any]],
    context_id: str = "",
) -> dict[str, Any]:
    """Create an explicit linear workflow; no task is dispatched automatically."""
    try:
        workflow = _workflow_service().create_workflow(
            name=name,
            stages=_workflow_specs_from_input(stages),
            context_id=context_id,
        )
        return {"ok": True, **workflow}
    except (ValueError, TypeError, WorkflowError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def workflow_bind_task(
    workflow_id: str,
    stage_key: str,
    task_id: str,
) -> dict[str, Any]:
    """Bind one existing durable task to the currently-ready workflow stage."""
    try:
        result = _workflow_service().bind_task(workflow_id, stage_key, task_id)
        return {"ok": True, "replayed": result.replayed, **result.workflow}
    except (ValueError, WorkflowError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def workflow_status(
    workflow_id: str,
    refresh: bool = True,
) -> dict[str, Any]:
    """Read a workflow, optionally reconciling stage state from bound tasks."""
    try:
        workflow = _workflow_service().get_workflow(
            workflow_id, refresh=bool(refresh),
        )
        return {"ok": True, **workflow}
    except (ValueError, WorkflowError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def workflow_list(status: str = "") -> dict[str, Any]:
    """List workflow control-plane records without dispatching or refreshing tasks."""
    try:
        return {
            "ok": True,
            "workflows": _workflow_service().list_workflows(status=status),
        }
    except (ValueError, WorkflowError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def workflow_approve(
    workflow_id: str,
    stage_key: str,
    decision: str = "approved",
    actor: str = "operator",
    reason_code: str = "",
) -> dict[str, Any]:
    """Resolve an optional local operator checkpoint for one completed stage."""
    try:
        result = _workflow_service().record_approval(
            workflow_id,
            stage_key,
            decision=decision,
            actor=actor,
            reason_code=reason_code,
        )
        return {"ok": True, "replayed": result.replayed, **result.workflow}
    except (ValueError, WorkflowError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def subagent_replay(task_id: str) -> dict[str, Any]:
    """Reduce one task's append-only event stream without mutating durable state."""
    try:
        return {"ok": True, **_replay_service().replay_task(task_id)}
    except (ValueError, ReplayNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def context_register(
    cwd: str,
    files: list[str],
    context_id: str = "",
    allow_external_symlinks: bool = False,
) -> dict[str, Any]:
    """Persist a content-free manifest for an explicit bounded file list."""
    try:
        result = _context_service().register(
            cwd,
            files,
            context_id=context_id,
            allow_external_symlinks=allow_external_symlinks,
        )
        return {"ok": True, "replayed": result.replayed, **result.manifest}
    except (ValueError, TypeError, ContextError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def context_status(context_id: str) -> dict[str, Any]:
    """Read context manifest metadata; no file content or cwd is returned."""
    try:
        return {"ok": True, **_context_service().get(context_id)}
    except (ValueError, ContextError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def context_verify(
    context_id: str,
    cwd: str,
    allow_external_symlinks: bool = False,
) -> dict[str, Any]:
    """Rehash an explicit context against a caller-supplied project root."""
    try:
        return {
            "ok": True,
            **_context_service().verify(
                context_id,
                cwd,
                allow_external_symlinks=allow_external_symlinks,
            ),
        }
    except (ValueError, ContextError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def context_render(
    context_id: str,
    cwd: str,
    allow_external_symlinks: bool = False,
    max_total_bytes: int = 2 * 1024 * 1024,
) -> dict[str, Any]:
    """Explicitly return verified UTF-8 context content; never auto-inject it."""
    try:
        return {
            "ok": True,
            **_context_service().render(
                context_id,
                cwd,
                allow_external_symlinks=allow_external_symlinks,
                max_total_bytes=max_total_bytes,
            ),
        }
    except (ValueError, ContextError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def knowledge_register(
    cwd: str,
    files: list[str],
    knowledge_id: str = "",
    name: str = "",
    source_kinds: dict[str, str] | None = None,
    allow_external_symlinks: bool = False,
) -> dict[str, Any]:
    """Register immutable knowledge metadata over explicit project files."""
    try:
        result = _knowledge_service().register(
            cwd,
            files,
            knowledge_id=knowledge_id,
            name=name,
            source_kinds=source_kinds,
            allow_external_symlinks=allow_external_symlinks,
        )
        return {"ok": True, "replayed": result.replayed, **result.collection}
    except (ValueError, KnowledgeError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def knowledge_status(knowledge_id: str) -> dict[str, Any]:
    """Read content-free knowledge collection metadata and source identities."""
    try:
        return {"ok": True, **_knowledge_service().status(knowledge_id)}
    except (ValueError, KnowledgeError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def knowledge_list(limit: int = 100) -> dict[str, Any]:
    """List registered knowledge collections without returning source content."""
    try:
        return _knowledge_service().list(limit=limit)
    except (ValueError, KnowledgeError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def knowledge_verify(
    knowledge_id: str,
    cwd: str,
    allow_external_symlinks: bool = False,
) -> dict[str, Any]:
    """Rehash all registered sources against a caller-supplied project root."""
    try:
        return _knowledge_service().verify(
            knowledge_id,
            cwd,
            allow_external_symlinks=allow_external_symlinks,
        )
    except (ValueError, KnowledgeError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def knowledge_search(
    knowledge_id: str,
    cwd: str,
    query: str,
    kind: str = "",
    max_results: int = 10,
    max_snippet_chars: int = 800,
    allow_external_symlinks: bool = False,
    task_id: str = "",
) -> dict[str, Any]:
    """Explicitly search verified UTF-8 knowledge sources and return citations."""
    try:
        return _knowledge_service().search(
            knowledge_id,
            cwd,
            query,
            kind=kind,
            max_results=max_results,
            max_snippet_chars=max_snippet_chars,
            allow_external_symlinks=allow_external_symlinks,
            task_id=task_id,
        )
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def knowledge_bundle(
    knowledge_id: str,
    cwd: str,
    query: str,
    kind: str = "",
    max_sources: int = 10,
    max_snippet_chars: int = 800,
    max_total_bytes: int = 128 * 1024,
    allow_external_symlinks: bool = False,
    task_id: str = "",
) -> dict[str, Any]:
    """Explicitly compose a bounded cited Markdown knowledge bundle."""
    try:
        return _knowledge_service().bundle(
            knowledge_id,
            cwd,
            query,
            kind=kind,
            max_sources=max_sources,
            max_snippet_chars=max_snippet_chars,
            max_total_bytes=max_total_bytes,
            allow_external_symlinks=allow_external_symlinks,
            task_id=task_id,
        )
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def knowledge_history(
    knowledge_id: str = "",
    operation: str = "",
    status: str = "",
    task_id: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Read content-free knowledge resolution audit metadata."""
    try:
        return _knowledge_service().history(
            knowledge_id=knowledge_id,
            operation=operation,
            status=status,
            task_id=task_id,
            limit=limit,
        )
    except (ValueError, KnowledgeError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def knowledge_resolution(resolution_id: str) -> dict[str, Any]:
    """Read one content-free knowledge resolution audit record."""
    try:
        return _knowledge_service().get_resolution(resolution_id)
    except (ValueError, KnowledgeError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def planner_create(
    name: str,
    requirement: str,
    task_kind: str = "implementation",
    complexity: str = "medium",
    acceptance_criteria: list[str] | None = None,
    knowledge_id: str = "",
    context_id: str = "",
    runtime: str = "",
    agent_profile: str = "",
    require_approval: bool = False,
    verification_required: bool = True,
    plan_id: str = "",
) -> dict[str, Any]:
    """Create a deterministic draft plan; never select or dispatch a backend."""
    try:
        return _planner_service().create(
            name=name,
            requirement=requirement,
            task_kind=task_kind,
            complexity=complexity,
            acceptance_criteria=acceptance_criteria,
            knowledge_id=knowledge_id or None,
            context_id=context_id or None,
            runtime=runtime or None,
            agent_profile=agent_profile or None,
            require_approval=require_approval,
            verification_required=verification_required,
            plan_id=plan_id or None,
        )
    except (ValueError, PlannerError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def planner_validate(plan_id: str) -> dict[str, Any]:
    """Validate a draft plan's linear structure and explicit references."""
    try:
        return _planner_service().validate(plan_id)
    except (ValueError, PlannerError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def planner_prepare(plan_id: str) -> dict[str, Any]:
    """Prepare a Workflow-compatible spec without creating or dispatching it."""
    try:
        return _planner_service().prepare(plan_id)
    except (ValueError, PlannerError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def planner_status(plan_id: str) -> dict[str, Any]:
    """Read one content-free deterministic plan projection."""
    try:
        return _planner_service().status(plan_id)
    except (ValueError, PlannerError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def planner_list(status: str = "", limit: int = 100) -> dict[str, Any]:
    """List content-free plans without creating tasks or selecting backends."""
    try:
        return _planner_service().list(status=status, limit=limit)
    except (ValueError, PlannerError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def planner_history(plan_id: str = "", limit: int = 100) -> dict[str, Any]:
    """Read append-only, content-free planner lifecycle events."""
    try:
        return _planner_service().history(plan_id=plan_id, limit=limit)
    except (ValueError, PlannerError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


def _plan_step_material_from_api(value: dict[str, Any]) -> PlanStepMaterial:
    if not isinstance(value, dict):
        raise ValueError("step_materials entries must be objects")

    def _text(name: str, default: str = "") -> str:
        raw = value.get(name, default)
        if raw is None:
            return default
        if not isinstance(raw, str):
            raise ValueError(f"{name} must be a string")
        return raw

    def _strings(name: str) -> tuple[str, ...]:
        raw = value.get(name)
        if raw is None:
            return ()
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError(f"{name} must be a list of strings")
        return tuple(raw)

    def _integer(name: str, default: int, *, nullable: bool = False) -> int | None:
        raw = value.get(name)
        if raw is None:
            return None if nullable else default
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"{name} must be an integer")
        return raw

    require_patch = value.get("require_patch", False)
    if not isinstance(require_patch, bool):
        raise ValueError("require_patch must be a boolean")

    return PlanStepMaterial(
        step_key=_text("step_key"),
        prompt=_text("prompt"),
        runtime=_text("runtime"),
        route=_text("route"),
        model=_text("model"),
        reasoning_effort=_text("reasoning_effort"),
        agent_profile=_text("agent_profile"),
        context_id=_text("context_id"),
        knowledge_id=_text("knowledge_id"),
        knowledge_query=_text("knowledge_query"),
        execution_mode=_text("execution_mode", "background"),
        allowed_paths=_strings("allowed_paths"),
        forbidden_paths=_strings("forbidden_paths"),
        verification_commands=_strings("verification_commands"),
        expected_artifacts=_strings("expected_artifacts"),
        max_changed_files=int(_integer("max_changed_files", 0)),
        verification_timeout_seconds=int(
            _integer("verification_timeout_seconds", 900)
        ),
        require_patch=require_patch,
        timeout_seconds=int(_integer("timeout_seconds", 300)),
        idle_timeout_seconds=int(_integer("idle_timeout_seconds", 180)),
        max_task_duration_seconds=_integer(
            "max_task_duration_seconds", 0, nullable=True
        ),
    )


@_mcp_tool()
def plan_execution_start(
    plan_id: str,
    cwd: str,
    step_materials: list[dict[str, Any]],
    execution_id: str = "",
) -> dict[str, Any]:
    """Start one prepared Plan through the durable V2 execution controller."""
    try:
        if not isinstance(step_materials, list):
            raise ValueError("step_materials must be a list")
        return _plan_execution_service().start(
            plan_id,
            cwd=cwd,
            step_materials=[
                _plan_step_material_from_api(item) for item in step_materials
            ],
            execution_id=execution_id or None,
        )
    except (TypeError, ValueError, PlanExecutionError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def plan_execution_status(
    execution_id: str, refresh: bool = True
) -> dict[str, Any]:
    """Read content-free status; refresh also advances from durable Task truth."""
    try:
        service = _plan_execution_service()
        return service.pump(execution_id) if refresh else service.status(execution_id)
    except (TypeError, ValueError, PlanExecutionError, WorkflowError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def plan_execution_resume(
    execution_id: str,
    cwd: str,
    step_materials: list[dict[str, Any]],
) -> dict[str, Any]:
    """Re-submit hash-matching input for undispatched stages after restart."""
    try:
        if not isinstance(step_materials, list):
            raise ValueError("step_materials must be a list")
        return _plan_execution_service().resume(
            execution_id,
            cwd=cwd,
            step_materials=[
                _plan_step_material_from_api(item) for item in step_materials
            ],
        )
    except (TypeError, ValueError, PlanExecutionError, WorkflowError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def plan_execution_cancel(
    execution_id: str, cancel_current_task: bool = False
) -> dict[str, Any]:
    """Cancel Plan progression; current Task cancellation remains explicit."""
    try:
        return _plan_execution_service().cancel(
            execution_id, cancel_current_task=cancel_current_task
        )
    except (TypeError, ValueError, PlanExecutionError, WorkflowError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def plan_execution_result(execution_id: str) -> dict[str, Any]:
    """Read the Plan-level result; non-terminal executions return final=false."""
    try:
        return _plan_execution_service().result(execution_id)
    except (TypeError, ValueError, PlanExecutionError, WorkflowError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def plan_execution_history(
    execution_id: str, limit: int = 100
) -> dict[str, Any]:
    """Read append-only, content-free PlanExecution audit events."""
    try:
        return _plan_execution_service().history(execution_id, limit=limit)
    except (TypeError, ValueError, PlanExecutionError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def subagent_capabilities(
    runtime: str = "",
    route: str = "",
    require_resume: bool = False,
    require_streaming: bool = False,
    require_cancel: bool = False,
    require_reasoning_effort: bool = False,
) -> dict[str, Any]:
    """List and match declared backend capabilities; never probe or dispatch."""
    try:
        result = BackendCapabilityService(_BACKENDS).query(
            CapabilityRequirements(
                runtime=runtime,
                route=route,
                require_resume=require_resume,
                require_streaming=require_streaming,
                require_cancel=require_cancel,
                require_reasoning_effort=require_reasoning_effort,
            )
        )
        return {"ok": True, **result}
    except (ValueError, CapabilityQueryError) as exc:
        return {"ok": False, "error": str(exc)}


@_mcp_tool()
def runtime_tool_catalog(
    category: str = "",
    name: str = "",
) -> dict[str, Any]:
    """List the fixed V1.4 read-only Tool Runtime catalog; never dispatch."""
    try:
        return _tool_runtime_service().catalog(category=category, name=name)
    except (ValueError, ToolRuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def runtime_tool_invoke(
    tool_name: str,
    cwd: str,
    arguments: dict[str, Any] | None = None,
    task_id: str = "",
    context_id: str = "",
) -> dict[str, Any]:
    """Explicitly invoke one bounded read-only Runtime tool and audit it."""
    try:
        return _tool_runtime_service().invoke(
            tool_name,
            cwd,
            arguments=arguments,
            task_id=task_id,
            context_id=context_id,
        )
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def runtime_tool_history(
    tool_name: str = "",
    status: str = "",
    task_id: str = "",
    context_id: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Read content-free invocation audit metadata; raw input/output is absent."""
    try:
        return _tool_runtime_service().history(
            tool_name=tool_name,
            status=status,
            task_id=task_id,
            context_id=context_id,
            limit=limit,
        )
    except (ValueError, ToolRuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def runtime_tool_invocation(invocation_id: str) -> dict[str, Any]:
    """Read one content-free Tool Runtime invocation audit record."""
    try:
        return _tool_runtime_service().get_invocation(invocation_id)
    except (ValueError, ToolRuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


@_mcp_tool()
def workflow_replay(workflow_id: str) -> dict[str, Any]:
    """Reduce one workflow event stream and compare it with durable rows."""
    try:
        return {"ok": True, **_replay_service().replay_workflow(workflow_id)}
    except (ValueError, ReplayNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}


# Qoder-specific names are compatibility aliases only.  They own no state.
@_mcp_tool()
def qoder_start(
    prompt: str,
    cwd: str = "",
    timeout_seconds: int = 300,
    model: str = "",
    reasoning_effort: str = "",
    route: str = "acp",
    resume_task_id: str = "",
    idempotency_key: str = "",
    idle_timeout_seconds: int = 180,
    max_task_duration_seconds: int | None = None,
) -> dict[str, Any]:
    return _task_launch_service().start(
        TaskLaunchRequest(
            prompt=prompt,
            runtime="qoder",
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
            route=route,
            resume_task_id=resume_task_id,
            idempotency_key=idempotency_key,
            idle_timeout_seconds=idle_timeout_seconds,
            max_task_duration_seconds=max_task_duration_seconds,
        )
    )


@_mcp_tool()
def qoder_status(task_id: str = "") -> dict[str, Any]:
    return subagent_status(task_id=task_id, runtime="qoder")


@_mcp_tool()
def qoder_wait(task_id: str, timeout_seconds: int = 55) -> dict[str, Any]:
    return subagent_wait(task_id, timeout_seconds)


@_mcp_tool()
def qoder_result(task_id: str) -> dict[str, Any]:
    return subagent_result(task_id)


@_mcp_tool()
def qoder_assessment(task_id: str) -> dict[str, Any]:
    return subagent_assessment(task_id)


@_mcp_tool()
def qoder_cancel(task_id: str) -> dict[str, Any]:
    return subagent_cancel(task_id)


@_mcp_tool()
def qoder_list() -> dict[str, Any]:
    return subagent_list(runtime="qoder")


def _task_list() -> dict[str, Any]:
    """List tasks known to this Runtime process (durable state first)."""
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Cold-start: try lazy init so a fresh process can list existing DB.
    try:
        service = _try_runtime_service()
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    if service is not None:
        for durable in service.list_tasks():
            tasks.append(_public(_task_state_from_durable(durable, service)))
            seen.add(durable.task_id)
    with TASKS_LOCK:
        handles = list(TASKS.values())
    for handle in handles:
        if handle.task_id not in seen:
            tasks.append(_public(handle))
    return {"ok": True, "tasks": tasks}


def run_startup_reconciliation() -> list[dict[str, Any]]:
    """PR3: classify stale non-terminal tasks after a Runtime restart.

    Takes the session lease per task (fencing stale workers), asks the
    durable session's registered backend to classify backend truth, and records
    completed/failed/cancelled (definitive signals) or LOST/ORPHANED.
    Never re-dispatches a prompt and never performs backend failover.
    Returns public-safe reconcile reports.
    """
    try:
        service = ReconciliationService(_get_runtime_database())
    except RuntimePersistenceError as exc:
        return [{"outcome": "error", "detail": str(exc)}]
    reports = service.reconcile_all(_backend_for_runtime)
    return [
        {
            "task_id": report.task_id,
            "outcome": report.outcome,
            "detail": report.detail,
        }
        for report in reports
    ]


def run_startup_plan_reconciliation() -> list[dict[str, Any]]:
    """V2: project reconciled Task truth into durable Plan executions.

    This must run only after Task reconciliation.  It never dispatches a new
    backend task without still-present transient material; after a real restart
    future undispatched stages therefore fail closed to ``needs_review``.
    """
    try:
        return _plan_execution_service().reconcile_all()
    except RuntimePersistenceError as exc:
        return [{"status": "error", "reason_code": str(exc)}]


def main() -> None:
    """Start stdio after Task reconciliation, then Plan reconciliation."""
    try:
        run_startup_reconciliation()
    except Exception:
        # Startup reconciliation is best-effort: it must never block the
        # MCP server from coming up.
        pass
    try:
        run_startup_plan_reconciliation()
    except Exception:
        pass
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
