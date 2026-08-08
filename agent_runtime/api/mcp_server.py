from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_runtime.activity_log import ActivityLogger
from agent_runtime.runtime.backend_callbacks import RuntimeBackendCallbacks
from agent_runtime.api.schemas import CAPTAIN_TOOL_NAMES
from agent_runtime.domain.enums import (
    TERMINAL_STATUS_VALUES,
    EventType,
    EventVisibility,
    TaskRoute,
    TaskType,
)
from agent_runtime.domain.event import TaskEvent
from agent_runtime.domain.ids import new_runtime_session_id, new_task_id
from agent_runtime.domain.lineage import TaskLineage
from agent_runtime.domain.workflow import WorkflowStageSpec
from agent_runtime.domain.session import Session
from agent_runtime.domain.structured_result import (
    RESULT_SCHEMA,
    StructuredResult,
    StructuredResultParseError,
    parse_structured_result,
)
from agent_runtime.domain.task import Task
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
from agent_runtime.persistence.runtime_paths import runtime_database_path
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
from agent_runtime.application.dispatch import CaptainDispatchService
from agent_runtime.application.dispatch.profiles import (
    WorkerProfileError,
    WorkerProfileResolver,
)
from agent_runtime.application.voyage import VoyageOverviewService
from agent_runtime.domain.dispatch import (
    CaptainDispatchRequest,
    CommandSpec,
    ModelPolicy,
    PatchPolicy,
    ReadScope,
    WorkerProfileRef,
)
from agent_runtime.backends.codebuddy import CodeBuddyBackend
from agent_runtime.backends.codebuddy.captain_dispatch import CodeBuddyContextReadOnlyDispatcher
from agent_runtime.backends.codebuddy.capability import descriptor as codebuddy_crew_descriptor
from agent_runtime.backends.codebuddy.process import probe_codebuddy_cli
from agent_runtime.backends.qoder.capability import descriptor as qoder_crew_descriptor
from agent_runtime.backends.qoder.model_catalog import list_qoder_models
from agent_runtime.backends.qoder.process import probe_qoder_cli
from agent_runtime.backends.qoder.captain_dispatch import QoderReadOnlyDispatcher
from agent_runtime.application.dispatch.workspace import (
    PatchWorkspaceCleanupError,
    PatchWorkspaceService,
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
LOG_DIR = ROOT / "work" / "agent-runtime-logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

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


def _mcp_tool():
    """Register only Captain tools by default; keep legacy tools callable internally.

    ``TP_VOYAGER_MCP_SURFACE=diagnostic`` restores the complete compatibility
    surface for maintenance.  This is one Runtime with two visibility profiles,
    not a second control plane or state machine.
    """
    def decorator(func):
        if _mcp_surface() == "diagnostic" or func.__name__ in _CAPTAIN_TOOL_NAMES:
            return mcp.tool()(func)
        return func
    return decorator


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


def _workflow_service() -> WorkflowService:
    return WorkflowService(_get_runtime_database())


def _replay_service() -> ReplayService:
    return ReplayService(_get_runtime_database())


def _context_service() -> ProjectContextService:
    return ProjectContextService(_get_runtime_database())


def _worker_profile_resolver() -> WorkerProfileResolver:
    configured = str(os.getenv("TP_VOYAGER_WORKER_PROFILE_ROOT") or "").strip()
    root = Path(configured).expanduser() if configured else ROOT / "skills" / "tp-voyager-captain" / "worker-profiles"
    return WorkerProfileResolver(root)


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
                models=None,
            ),
            "qoder": CrewProvider(
                descriptor=qoder_crew_descriptor(),
                probe=probe_qoder_cli,
                models=list_qoder_models,
            ),
        },
        task_service=tasks,
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


def _note_task_activity(task: TaskState, kind: str) -> None:
    """Record an allow-listed, content-free public task activity event.

    Durable tasks additionally append an ``activity_observed`` audit event.
    Streaming activity stays in-process only (PR1): the durable audit trail
    covers lifecycle activities, not every stream event.
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
            _runtime_service().append_activity(task.task_id, kind)
        except RuntimePersistenceError as exc:
            # Explicit, non-silent durability failure for diagnostics.
            task.persist_error = f"activity event failed: {exc}"


from agent_runtime.api.public_projection import (
    public_task as _public,
    result_summary as _result_summary,
    safe_public_error as _safe_public_error,
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
    if task.workspace_mode != "patch_worktree" or not task.source_cwd or not task.cwd:
        return
    root = _get_runtime_database().path.parent / "workspaces"
    PatchWorkspaceService(root).cleanup(task.cwd, source_root=task.source_cwd)


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
    declarations = list(normalized.artifacts)
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
    effective_workspace = baseline.git_root or task.cwd
    capture_risks: list[str] = []
    try:
        capture = ArtifactCaptureService(artifact_store).capture(
            task_id=task.task_id,
            attempt_id=attempt_id,
            cwd=effective_workspace,
            declarations=declarations,
            baseline=baseline,
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
        observability=observability,
        output=output,
        changed_files=list(capture.changed_files or normalized.changed_files),
        tests=[*normalized.tests, *verification.tests],
        artifacts=capture.public_artifacts(),
        risks=risks,
        claims=list(normalized.claims),
        verification=verification.to_dict(),
        usage=usage,
    )
    # Patch completion is not externally visible until the isolated worktree
    # has been retired.  This closes the race where status/result became
    # durable before ``finally`` removed ``runtime/workspaces/patch-*``.
    try:
        _retire_patch_workspace_before_completion(task)
    except PatchWorkspaceCleanupError:
        capture.cleanup_orphans()
        raise

    try:
        _runtime_service().save_result(
            task.task_id,
            structured_result=structured_result,
            initial_evidence=verification.evidence,
            artifact_declarations=capture.artifacts,
            status="completed",
            version=task.version,
            terminal_reason=task.terminal_reason,
            timeout_reason=task.timeout_reason,
            lease=task.lease,
            metadata_rejected_count=normalized.rejected_count,
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
    effective_workspace = baseline.git_root or task.cwd
    raw_output = task.result if isinstance(task.result, dict) else {}
    normalized = normalize_backend_result(raw_output)
    plan = VerificationPlan.from_dict(task.verification_plan)
    declarations = list(normalized.artifacts)
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

    verification_status = str(verification.get("status") or "").upper()
    work_product_status = {
        "PASSED": "verified",
        "FAILED": "rejected",
        "NEEDS_REVIEW": "needs_review",
    }.get(verification_status, "unverified")
    outcome_risks: list[str] = ["execution_failed"]
    if task.terminal_reason == "PatchWorkspaceCleanupError":
        outcome_risks.append("patch_workspace_cleanup_failed")
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
        changed_files=list(capture.changed_files or normalized.changed_files),
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
            metadata_rejected_count=normalized.rejected_count,
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


def _persist_backend_session(task: TaskState, backend_session_id: str) -> None:
    """Record the private backend session id (never projected publicly)."""
    if not task.persisted:
        return
    try:
        _runtime_service().set_backend_session(
            task.task_id,
            backend_session_id=backend_session_id,
        )
    except RuntimePersistenceError as exc:
        task.persist_error = str(exc)
        raise BackendError(f"runtime persistence failed: {exc}") from exc


def _persist_dispatch_accepted(task: TaskState, backend_session_id: str) -> None:
    """Record dispatch acceptance without projecting the private Crew session id."""
    if not task.persisted:
        return
    try:
        with _get_runtime_database().transaction() as connection:
            TaskService(_get_runtime_database()).events.append(
                connection,
                TaskEvent(
                    event_id=uuid.uuid4().hex,
                    task_id=task.task_id,
                    event_type=EventType.BACKEND_DISPATCH_ACCEPTED.value,
                    event_time=time.time(),
                    payload_json=json.dumps({"backend_session_id": backend_session_id}, ensure_ascii=False),
                    visibility=EventVisibility.INTERNAL.value,
                ),
            )
    except RuntimePersistenceError as exc:
        task.persist_error = str(exc)
        raise BackendError(f"runtime persistence failed: {exc}") from exc


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
        task.backend_session_id = backend_session_id
        # Current official Crew routes know their backend session identity
        # before/at dispatch acceptance. Persist it before further activity.
        _persist_backend_session(task, backend_session_id)
        _persist_dispatch_accepted(task, backend_session_id)
        _note_task_activity(task, "session_created")

    def on_activity(kind: str) -> None:
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
        _note_task_activity(task, kind)

    def on_usage(usage: BackendUsage) -> None:
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
        callbacks = _make_backend_callbacks(task, log_event)
        metadata = {
            "route": task.route,
            "access_mode": (
                "patch" if "patch" in task.route
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
                idle_timeout_seconds=task.idle_timeout_seconds,
                max_task_duration_seconds=task.max_task_duration_seconds,
                metadata=metadata,
            )
            backend_result = backend.start(request, callbacks)

        task.backend_session_id = backend_result.backend_session_id or task.backend_session_id
        if task.backend_session_id:
            _persist_backend_session(task, task.backend_session_id)
        task.answer = backend_result.answer
        result_backend = backend_result.backend or backend_name
        task.result = {
            **(backend_result.result or {}),
            "backend": result_backend,
            "stopReason": backend_result.stop_reason,
            "reasoning_effort_requested": task.reasoning_effort or None,
            "reasoning_effort_applied": (backend_result.result or {}).get(
                "reasoning_effort_applied"
            ),
        }
        task.terminal_reason = backend_result.stop_reason or "end_turn"
        _persist_completed(task, task.result, backend_result=backend_result)
        task.state = "completed"
        _note_task_activity(task, "final_response")
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
    except Exception as exc:
        task.state = "failed"
        task.error = str(exc)
        task.terminal_reason = type(exc).__name__
        if isinstance(exc, BackendTimeoutError):
            task.timeout_reason = exc.timeout_reason
        if not _persist_failed_with_partial_artifacts(task):
            _persist_failed(task)
        _note_task_activity(task, "failed")
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
    if task.workspace_mode != "patch_worktree" or not task.source_cwd or not task.cwd:
        return
    if task.state != "failed" or task.persist_error:
        return
    try:
        root = _get_runtime_database().path.parent / "workspaces"
        PatchWorkspaceService(root).cleanup(task.cwd, source_root=task.source_cwd)
    except PatchWorkspaceCleanupError as exc:
        task.persist_error = f"patch workspace cleanup requires attention: {type(exc).__name__}"


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
        cancel_scope_for_route=lambda route: "codebuddy_sdk",
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
    allowed_routing_keys = {"read_scope", "worker_profile_ref", "correlation_id", "model_policy"}
    if set(routing) - allowed_routing_keys:
        return {"ok": False, "error": "routing_metadata contains unsupported keys"}
    try:
        encoded_routing = json.dumps(routing, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return {"ok": False, "error": "routing_metadata must be JSON serializable"}
    if len(encoded_routing.encode("utf-8")) > 32 * 1024:
        return {"ok": False, "error": "routing_metadata exceeds 32 KiB"}

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
            "verification_plan": plan.to_dict(),
            "routing_metadata": routing,
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
        created = service.create_task(
            task=durable_task,
            session=session,
            metadata=metadata,
            idempotency_key=canonical_key,
            request_fingerprint=fingerprint,
            lineage=lineage,
            now=now,
        )
    except RuntimePersistenceError as exc:
        return {"ok": False, "error": f"runtime database unavailable: {exc}"}
    if created.outcome == "replayed":
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
    if created.outcome == "conflict":
        return {"ok": False, "error": created.error}

    task = TaskState(
        task_id=task_id,
        prompt=prompt,
        cwd=str(working_dir),
        runtime=runtime,
        model=model.strip(),
        reasoning_effort=reasoning_effort.strip(),
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
    )
    with TASKS_LOCK:
        TASKS[task_id] = task
        if canonical_key:
            IDEMPOTENCY_TASKS[canonical_key] = task_id
    _note_task_activity(task, "task_accepted")
    thread = threading.Thread(
        target=worker_target,
        args=(task, float(timeout_seconds)),
        daemon=True,
    )
    try:
        _start_worker_thread(thread)
    except RuntimeError as exc:
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
    if canonical_route not in {"acp_read_only", "acp_patch"}:
        return {"ok": False, "error": "Qoder route must be acp_read_only or acp_patch"}
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
        resumable_routes=frozenset({"acp_read_only", "acp_patch"}),
        worker_target=_run_qoder,
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


def _codebuddy_start(
    *,
    prompt: str,
    cwd: str = "",
    timeout_seconds: int = 300,
    model: str = "",
    reasoning_effort: str = "",
    route: str = "sdk_context_read_only",
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
    canonical_route = route.strip().lower() or "sdk_context_read_only"
    if canonical_route not in {"sdk_context_read_only", "sdk_patch"}:
        return {
            "ok": False,
            "error": "CodeBuddy route must be sdk_context_read_only or sdk_patch",
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
    if reasoning_effort.strip():
        return {
            "ok": False,
            "error": "CodeBuddy controlled route does not accept reasoning_effort yet",
        }
    return _durable_cli_start(
        runtime="codebuddy",
        task_type="codebuddy",
        route=canonical_route,
        resumable_routes=frozenset({"sdk_context_read_only", "sdk_patch"}),
        worker_target=_run_codebuddy,
        prompt=prompt,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        model=model,
        reasoning_effort="",
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


def _routing_projection(task: TaskState) -> dict[str, Any]:
    routing = task.routing_metadata if isinstance(task.routing_metadata, dict) else {}
    output: dict[str, Any] = {}
    correlation_id = routing.get("correlation_id")
    if isinstance(correlation_id, str) and correlation_id:
        output["correlation_id"] = correlation_id
    profile = routing.get("worker_profile_ref")
    if isinstance(profile, dict):
        output["worker_profile_ref"] = dict(profile)
    policy = routing.get("model_policy")
    if isinstance(policy, dict):
        output["model_policy"] = dict(policy)
    scope = routing.get("read_scope")
    if isinstance(scope, dict):
        resolved = scope.get("resolved_files")
        output["read_scope"] = {
            "files": list(scope.get("files") or []),
            "directories": list(scope.get("directories") or []),
            "globs": list(scope.get("globs") or []),
            "resolved_file_count": len(resolved) if isinstance(resolved, list) else 0,
        }
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
            "usage": _usage_evidence_for_task(task_id),
        }
    if task.result_parse_error:
        return {
            "ok": False,
            "state": "completed",
            **_public(task),
            **_routing_projection(task),
            "usage": _usage_evidence_for_task(task_id),
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
            "usage": _usage_evidence_for_task(task_id),
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
        "usage": (
            _usage_evidence_for_task(task_id)
            or (parsed.usage if parsed is not None else {})
        ),
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
        return "codebuddy_sdk"
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
def crew_health(backend: str, probe: bool = True) -> dict[str, Any]:
    """Return one Crew health projection; raw probe errors/paths stay private."""
    try:
        return {"ok": True, **_crew_registry_service().health(backend, probe=probe).to_dict()}
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


@_mcp_tool()
def voyager_overview(limit: int = 5) -> dict[str, Any]:
    """Return a compact, content-free progress view for the Captain."""
    try:
        return {"ok": True, **_voyage_overview_service().overview(limit=limit)}
    except (RuntimePersistenceError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__}


@_mcp_tool()
def task_dispatch(
    objective: str,
    crew: str,
    task_kind: str,
    cwd: str = "",
    model: str = "",
    access_mode: str = "read_only",
    idempotency_key: str = "",
    context_id: str = "",
    context_files: list[str] | None = None,
    read_scope: dict[str, Any] | None = None,
    model_policy: dict[str, Any] | None = None,
    worker_profile_ref: dict[str, Any] | None = None,
    correlation_id: str = "",
    timeout_seconds: int = 300,
    required_capabilities: list[str] | None = None,
    patch_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch one explicit Captain-selected Crew task under bounded policy.

    ``read_scope`` is the vendor-neutral read-only contract.  Legacy
    ``context_files`` remains accepted for CodeBuddy Context Manifest
    compatibility.  TP-Voyager never chooses a
    model or resolves an unverified Worker profile on the Crew's behalf.
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

    effective_context_id = str(context_id or "").strip()
    if supplied_files and effective_context_id:
        return reject("INVALID_CONTEXT_REQUEST", "pass context_id or context_files, not both")
    if parsed_scope is not None and effective_context_id:
        return reject("INVALID_CONTEXT_REQUEST", "pass context_id or read_scope, not both")
    if parsed_scope is not None and normalized_mode != "read_only":
        return reject("READ_SCOPE_NOT_APPLICABLE", "read_scope is only accepted for read_only access_mode")
    if effective_context_id and normalized_crew == "qoder":
        return reject("CONTEXT_ID_NOT_APPLICABLE", "Qoder Captain dispatch uses read_scope, not Context Manifest ids")

    resolved_files: tuple[str, ...] = ()
    context_auto_created = False
    if supplied_files:
        try:
            registered = _context_service().register(cwd, supplied_files)
            effective_context_id = str(registered.manifest.get("context_id") or "")
            context_auto_created = True
        except (ValueError, TypeError, ContextError) as exc:
            return reject("CONTEXT_INVALID", str(exc))
        except RuntimePersistenceError:
            return reject("RUNTIME_UNAVAILABLE", "runtime database unavailable")
    elif parsed_scope is not None:
        try:
            resolved_files = tuple(_context_service().resolve_read_scope(cwd, parsed_scope))
            if normalized_crew == "codebuddy":
                registered = _context_service().register(cwd, resolved_files)
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
            profile_content = _worker_profile_resolver().resolve(parsed_profile).content
        except (TypeError, ValueError, WorkerProfileError) as exc:
            return reject("WORKER_PROFILE_INVALID", str(exc))

    external_correlation_id = str(correlation_id or "").strip()
    if external_correlation_id:
        if (
            len(external_correlation_id) > 160
            or "\x00" in external_correlation_id
            or any(ord(ch) < 32 for ch in external_correlation_id)
        ):
            return reject("INVALID_CORRELATION_ID", "correlation_id must be printable and at most 160 characters")

    result = _captain_dispatch_service().dispatch(
        CaptainDispatchRequest(
            objective=objective,
            crew=crew,
            task_kind=task_kind,
            cwd=cwd,
            model=model,
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
            correlation_id=external_correlation_id,
        )
    )
    if context_auto_created:
        result = {
            **result,
            "context_id": effective_context_id,
            "context_auto_created": True,
        }
        if parsed_scope is not None:
            result = {**result, "read_scope_resolved_file_count": len(resolved_files)}
    elif parsed_scope is not None:
        result = {**result, "read_scope_resolved_file_count": len(resolved_files)}
    if external_correlation_id:
        result = {**result, "correlation_id": external_correlation_id}
    if parsed_profile is not None:
        result = {**result, "worker_profile_ref": parsed_profile.to_dict()}
    return result


@_mcp_tool()
def task_result(task_id: str) -> dict[str, Any]:
    """Return explicit terminal material; status/overview remain content-free."""
    return _task_result_response(task_id)


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
