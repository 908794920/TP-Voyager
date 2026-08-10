"""Schema migrations for the durable runtime database.

Migrations are versioned by ``PRAGMA user_version`` and applied in order.
Each migration runs inside its own transaction; ``migrate`` is idempotent and
safe to run repeatedly (including across process restarts).
"""

from __future__ import annotations

import sqlite3
from typing import Sequence

from agent_runtime.persistence.errors import RuntimePersistenceError

SCHEMA_VERSION = 13

_MIGRATIONS: dict[int, Sequence[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL,
            route TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            cancel_requested_at REAL,
            cancel_confirmed_at REAL,
            session_id TEXT,
            current_attempt_id TEXT,
            result_available INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            error_code TEXT,
            error_message TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            terminal_reason TEXT,
            cancel_scope TEXT,
            cancel_initiator TEXT,
            timeout_reason TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at)",
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            backend TEXT NOT NULL,
            route TEXT NOT NULL,
            backend_session_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_sessions_task_id ON sessions(task_id)",
        """
        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            attempt_no INTEGER NOT NULL,
            backend TEXT NOT NULL,
            route TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            error_code TEXT,
            error_message TEXT,
            UNIQUE(task_id, attempt_no)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            session_id TEXT,
            attempt_id TEXT,
            event_type TEXT NOT NULL,
            event_time REAL NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            visibility TEXT NOT NULL DEFAULT 'public'
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_events_task_seq ON events(task_id, seq)",
        """
        CREATE TABLE IF NOT EXISTS idempotency (
            idempotency_key TEXT PRIMARY KEY,
            request_fingerprint TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            created_at REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_idempotency_task_id ON idempotency(task_id)",
    ],
    2: [
        # Add observability columns for databases created at v1 (pre-PR1.1).
        # Each ALTER TABLE is wrapped in the migration loop with duplicate-column
        # tolerance for databases already at the updated v1 schema.
        "ALTER TABLE tasks ADD COLUMN terminal_reason TEXT",
        "ALTER TABLE tasks ADD COLUMN cancel_scope TEXT",
        "ALTER TABLE tasks ADD COLUMN cancel_initiator TEXT",
        "ALTER TABLE tasks ADD COLUMN timeout_reason TEXT",
    ],
    3: [
        # PR3 lease/fencing on sessions: bridge instance ownership with a
        # generation counter and an expiry timestamp.  Reconciliation bumps
        # the generation when taking over, so stale owners cannot write.
        "ALTER TABLE sessions ADD COLUMN owner_instance_id TEXT",
        "ALTER TABLE sessions ADD COLUMN owner_generation INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sessions ADD COLUMN lease_expires_at REAL",
        # PR3 reconciliation bookkeeping on tasks: LOST / ORPHANED moments.
        "ALTER TABLE tasks ADD COLUMN lost_at REAL",
        "ALTER TABLE tasks ADD COLUMN orphaned_at REAL",
    ],
    4: [
        # PR4-B1: Evidence/Artifact are strictly bound to one Attempt, so the
        # composite unique index below is the schema-level guarantee that
        # (task_id, attempt_id) always identifies a real attempt row.
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_task_attempt "
        "    ON attempts(task_id, attempt_id)",
        # PR4-B1: backfill tasks.current_attempt_id for databases created
        # before PR4, where the task row never learned the attempt id.  The
        # highest attempt_no is the durable truth (PR4 design ch.7.2).
        """
        UPDATE tasks
        SET current_attempt_id = (
            SELECT a.attempt_id
            FROM attempts a
            WHERE a.task_id = tasks.task_id
            ORDER BY a.attempt_no DESC
            LIMIT 1
        )
        WHERE current_attempt_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM attempts a
              WHERE a.task_id = tasks.task_id
          )
        """,
        # Artifact Declaration Registry (PR4-B: declaration only, no file
        # capture — capture_state=captured is produced only by PR4-D).
        # Created BEFORE evidences because evidences.artifact_id references it.
        # PR4-B1.1: the composite UNIQUE (task_id, attempt_id, artifact_id)
        # exists so evidences can reference an artifact through a composite
        # FK that proves the referenced row lives in the SAME attempt.
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id       TEXT PRIMARY KEY,
            task_id           TEXT NOT NULL,
            attempt_id        TEXT NOT NULL,
            origin            TEXT NOT NULL,
            kind              TEXT NOT NULL,
            name              TEXT NOT NULL,
            workspace_relpath TEXT,
            storage_key       TEXT,
            capture_state     TEXT NOT NULL DEFAULT 'declared',
            sha256            TEXT,
            size_bytes        INTEGER,
            declared_at       REAL NOT NULL,
            captured_at       REAL,
            created_at        REAL NOT NULL,
            updated_at        REAL NOT NULL,
            metadata_json     TEXT NOT NULL DEFAULT '{}',

            FOREIGN KEY (task_id, attempt_id)
                REFERENCES attempts(task_id, attempt_id)
                ON DELETE CASCADE,

            UNIQUE (task_id, attempt_id, artifact_id),

            CHECK (origin IN ('agent', 'backend', 'runtime')),
            CHECK (kind IN ('file', 'patch', 'report', 'build', 'log')),
            CHECK (capture_state IN (
                'declared', 'captured', 'missing', 'rejected'
            )),
            CHECK (size_bytes IS NULL OR size_bytes >= 0),
            CHECK (
                capture_state != 'captured'
                OR (
                    storage_key IS NOT NULL
                    AND sha256 IS NOT NULL
                    AND captured_at IS NOT NULL
                )
            )
        )
        """,
        # Evidence: immutable append-only records bound to an Attempt
        # (PR4 design ch.5: no update, no updated_at; a future Verifier
        # appends verified_* rows referencing subject_evidence_id).
        # PR4-B1.1: subject/artifact references are COMPOSITE foreign keys —
        # a referenced row must belong to the SAME (task_id, attempt_id), so
        # Attempt stays the hard isolation boundary for evidence chains.
        # The origin/trust_state combination CHECKs freeze the trust model:
        # a Backend can declare/observe but never produce verified_*.
        """
        CREATE TABLE IF NOT EXISTS evidences (
            evidence_id         TEXT PRIMARY KEY,
            task_id             TEXT NOT NULL,
            attempt_id          TEXT NOT NULL,
            subject_evidence_id TEXT,
            artifact_id         TEXT,
            evidence_type       TEXT NOT NULL,
            trust_state         TEXT NOT NULL,
            origin              TEXT NOT NULL,
            summary             TEXT NOT NULL DEFAULT '',
            detail_json         TEXT NOT NULL DEFAULT '{}',
            captured_at         REAL NOT NULL,
            created_at          REAL NOT NULL,

            FOREIGN KEY (task_id, attempt_id)
                REFERENCES attempts(task_id, attempt_id)
                ON DELETE CASCADE,

            FOREIGN KEY (task_id, attempt_id, subject_evidence_id)
                REFERENCES evidences(task_id, attempt_id, evidence_id)
                ON DELETE CASCADE,

            FOREIGN KEY (task_id, attempt_id, artifact_id)
                REFERENCES artifacts(task_id, attempt_id, artifact_id)
                ON DELETE CASCADE,

            UNIQUE (task_id, attempt_id, evidence_id),

            CHECK (evidence_type IN (
                'agent_claim', 'test', 'command',
                'file', 'review', 'artifact'
            )),
            CHECK (trust_state IN (
                'declared', 'observed',
                'verified_passed', 'verified_failed',
                'needs_review', 'skipped'
            )),
            CHECK (origin IN (
                'agent', 'backend', 'runtime',
                'verifier', 'human'
            )),
            -- Frozen origin/trust_state combinations (PR4 trust model):
            -- backend can never write verified_*/needs_review.
            CHECK (
                (trust_state = 'declared'
                    AND origin IN ('agent', 'backend', 'human'))
                OR (trust_state = 'observed'
                    AND origin IN ('backend', 'runtime', 'human'))
                OR (trust_state IN (
                        'verified_passed', 'verified_failed', 'needs_review')
                    AND origin IN ('verifier', 'human'))
                OR (trust_state = 'skipped'
                    AND origin IN ('backend', 'runtime', 'verifier', 'human'))
            ),
            -- agent_claim is exactly the agent's declared bottom line.
            CHECK (
                evidence_type != 'agent_claim'
                OR (origin = 'agent' AND trust_state = 'declared')
            ),
            -- artifact-typed evidence must point at exactly one artifact;
            -- every other evidence type carries no artifact reference.
            CHECK (
                (evidence_type = 'artifact' AND artifact_id IS NOT NULL)
                OR (evidence_type != 'artifact' AND artifact_id IS NULL)
            ),
            -- No self-verifying evidence.
            CHECK (subject_evidence_id IS NULL
                OR subject_evidence_id != evidence_id)
        )
        """,
        # PR4-B1 indexes (declared in the design; child-row lookups are
        # always scoped by task + attempt, optionally by type/state).
        "CREATE INDEX IF NOT EXISTS idx_evidences_task_attempt "
        "    ON evidences(task_id, attempt_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_evidences_subject "
        "    ON evidences(subject_evidence_id)",
        "CREATE INDEX IF NOT EXISTS idx_evidences_type_state "
        "    ON evidences(evidence_type, trust_state)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_task_attempt "
        "    ON artifacts(task_id, attempt_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_capture_state "
        "    ON artifacts(capture_state)",
    ],
    5: [
        # PR5: generic Sub-Agent parent/child metadata lives in a separate
        # relation table so legacy task rows and their stable public shape are
        # untouched.  The child row is one-to-one with a durable task.
        """
        CREATE TABLE IF NOT EXISTS task_lineage (
            child_task_id  TEXT PRIMARY KEY,
            parent_task_id TEXT,
            root_task_id   TEXT NOT NULL,
            context_id     TEXT,
            agent_profile  TEXT,
            execution_mode TEXT NOT NULL DEFAULT 'background',
            created_at     REAL NOT NULL,

            FOREIGN KEY (child_task_id)
                REFERENCES tasks(task_id) ON DELETE CASCADE,
            FOREIGN KEY (parent_task_id)
                REFERENCES tasks(task_id) ON DELETE RESTRICT,
            FOREIGN KEY (root_task_id)
                REFERENCES tasks(task_id) ON DELETE RESTRICT,

            CHECK (execution_mode IN ('background', 'detached')),
            CHECK (parent_task_id IS NULL OR parent_task_id != child_task_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_task_lineage_parent "
        "    ON task_lineage(parent_task_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_task_lineage_root "
        "    ON task_lineage(root_task_id, created_at)",
    ],
    6: [
        # V1.2 Platform: an optional linear workflow control plane.  It owns
        # no prompt, backend handle, retry policy, or second task state
        # machine.  Stages bind to existing durable tasks by FK.
        """
        CREATE TABLE IF NOT EXISTS workflows (
            workflow_id TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            context_id  TEXT,
            status      TEXT NOT NULL DEFAULT 'active',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            version     INTEGER NOT NULL DEFAULT 1,

            CHECK (status IN (
                'active', 'blocked', 'completed', 'failed', 'cancelled'
            )),
            CHECK (version >= 1)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_workflows_status_updated "
        "    ON workflows(status, updated_at)",
        """
        CREATE TABLE IF NOT EXISTS workflow_stages (
            stage_id          TEXT PRIMARY KEY,
            workflow_id       TEXT NOT NULL,
            stage_key         TEXT NOT NULL,
            title             TEXT NOT NULL,
            position          INTEGER NOT NULL,
            status            TEXT NOT NULL DEFAULT 'pending',
            approval_required INTEGER NOT NULL DEFAULT 0,
            runtime           TEXT,
            agent_profile     TEXT,
            task_id           TEXT UNIQUE,
            created_at        REAL NOT NULL,
            updated_at        REAL NOT NULL,
            started_at        REAL,
            finished_at       REAL,

            FOREIGN KEY (workflow_id)
                REFERENCES workflows(workflow_id) ON DELETE CASCADE,
            FOREIGN KEY (task_id)
                REFERENCES tasks(task_id) ON DELETE SET NULL,

            UNIQUE (workflow_id, stage_key),
            UNIQUE (workflow_id, position),
            CHECK (position >= 1),
            CHECK (approval_required IN (0, 1)),
            CHECK (status IN (
                'pending', 'ready', 'running', 'waiting_approval',
                'completed', 'failed', 'cancelled', 'skipped'
            ))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_workflow_stages_order "
        "    ON workflow_stages(workflow_id, position)",
        "CREATE INDEX IF NOT EXISTS idx_workflow_stages_status "
        "    ON workflow_stages(workflow_id, status)",
        """
        CREATE TABLE IF NOT EXISTS workflow_approvals (
            approval_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            stage_id    TEXT NOT NULL UNIQUE,
            decision    TEXT NOT NULL,
            actor       TEXT NOT NULL DEFAULT 'operator',
            reason_code TEXT,
            decided_at  REAL NOT NULL,

            FOREIGN KEY (workflow_id)
                REFERENCES workflows(workflow_id) ON DELETE CASCADE,
            FOREIGN KEY (stage_id)
                REFERENCES workflow_stages(stage_id) ON DELETE CASCADE,

            CHECK (decision IN ('approved', 'rejected'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_workflow_approvals_workflow "
        "    ON workflow_approvals(workflow_id, decided_at)",
        """
        CREATE TABLE IF NOT EXISTS workflow_events (
            seq          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id     TEXT NOT NULL UNIQUE,
            workflow_id  TEXT NOT NULL,
            stage_id     TEXT,
            event_type   TEXT NOT NULL,
            event_time   REAL NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            visibility   TEXT NOT NULL DEFAULT 'public',

            FOREIGN KEY (workflow_id)
                REFERENCES workflows(workflow_id) ON DELETE CASCADE,
            FOREIGN KEY (stage_id)
                REFERENCES workflow_stages(stage_id) ON DELETE CASCADE,

            CHECK (visibility IN ('public', 'internal'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_workflow_events_seq "
        "    ON workflow_events(workflow_id, seq)",
    ],
    7: [
        # V1.2 Project Context Manifest: content-free identity for an explicit
        # bounded set of project files.  Root paths and file bytes are never
        # stored, and no automatic prompt injection is performed.
        """
        CREATE TABLE IF NOT EXISTS context_manifests (
            context_id  TEXT PRIMARY KEY,
            root_hash   TEXT NOT NULL,
            file_count  INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            created_at  REAL NOT NULL,

            CHECK (file_count >= 1),
            CHECK (total_bytes >= 0),
            CHECK (length(root_hash) = 64)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS context_entries (
            context_id TEXT NOT NULL,
            relpath    TEXT NOT NULL,
            sha256     TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,

            PRIMARY KEY (context_id, relpath),
            FOREIGN KEY (context_id)
                REFERENCES context_manifests(context_id) ON DELETE CASCADE,

            CHECK (length(relpath) >= 1),
            CHECK (length(sha256) = 64),
            CHECK (size_bytes >= 0)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_context_entries_context "
        "    ON context_entries(context_id, relpath)",
    ],
    8: [
        # V1.4 Tool Runtime Foundation: explicit read-only tool invocations.
        # Raw inputs, workspace paths, query text, file contents, and Git diffs
        # are never persisted.  The table is an audit ledger of hashes and
        # bounded result metadata only; tools do not own Task lifecycle state.
        """
        CREATE TABLE IF NOT EXISTS tool_invocations (
            invocation_id   TEXT PRIMARY KEY,
            tool_name       TEXT NOT NULL,
            tool_version    TEXT NOT NULL,
            task_id         TEXT,
            context_id      TEXT,
            status          TEXT NOT NULL,
            requested_at    REAL NOT NULL,
            finished_at     REAL NOT NULL,
            workspace_ref   TEXT NOT NULL,
            input_sha256    TEXT NOT NULL,
            output_sha256   TEXT,
            bytes_returned  INTEGER NOT NULL DEFAULT 0,
            item_count      INTEGER NOT NULL DEFAULT 0,
            error_code      TEXT,
            error_message   TEXT,
            metadata_json   TEXT NOT NULL DEFAULT '{}',

            FOREIGN KEY (task_id)
                REFERENCES tasks(task_id) ON DELETE SET NULL,
            FOREIGN KEY (context_id)
                REFERENCES context_manifests(context_id) ON DELETE SET NULL,

            CHECK (status IN ('succeeded', 'failed', 'rejected')),
            CHECK (length(workspace_ref) = 64),
            CHECK (length(input_sha256) = 64),
            CHECK (output_sha256 IS NULL OR length(output_sha256) = 64),
            CHECK (bytes_returned >= 0),
            CHECK (item_count >= 0),
            CHECK (finished_at >= requested_at)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tool_invocations_time "
        "    ON tool_invocations(requested_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tool_invocations_tool_status "
        "    ON tool_invocations(tool_name, status, requested_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tool_invocations_task "
        "    ON tool_invocations(task_id, requested_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tool_invocations_context "
        "    ON tool_invocations(context_id, requested_at DESC)",
    ],
    9: [
        # V1.5 Knowledge Runtime: collections are immutable projections over
        # content-free Context Manifests.  Resolution audit rows persist only
        # query/output hashes and bounded counters; query text, snippets,
        # workspace paths, embeddings, and source bytes are never stored.
        """
        CREATE TABLE IF NOT EXISTS knowledge_collections (
            knowledge_id TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            context_id    TEXT NOT NULL UNIQUE,
            root_hash     TEXT NOT NULL,
            source_count  INTEGER NOT NULL,
            total_bytes   INTEGER NOT NULL,
            created_at    REAL NOT NULL,

            FOREIGN KEY (context_id)
                REFERENCES context_manifests(context_id) ON DELETE RESTRICT,

            CHECK (length(knowledge_id) BETWEEN 1 AND 80),
            CHECK (length(name) BETWEEN 1 AND 120),
            CHECK (length(root_hash) = 64),
            CHECK (source_count >= 1),
            CHECK (total_bytes >= 0),
            UNIQUE (knowledge_id, context_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_knowledge_collections_created "
        "    ON knowledge_collections(created_at DESC)",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_context_entries_identity
            ON context_entries(context_id, relpath, sha256, size_bytes)
        """,
        """
        CREATE TABLE IF NOT EXISTS knowledge_sources (
            knowledge_id TEXT NOT NULL,
            context_id   TEXT NOT NULL,
            relpath      TEXT NOT NULL,
            sha256       TEXT NOT NULL,
            size_bytes   INTEGER NOT NULL,
            kind         TEXT NOT NULL,
            ordinal      INTEGER NOT NULL,

            PRIMARY KEY (knowledge_id, relpath),
            UNIQUE (knowledge_id, ordinal),
            FOREIGN KEY (knowledge_id, context_id)
                REFERENCES knowledge_collections(knowledge_id, context_id)
                ON DELETE CASCADE,
            FOREIGN KEY (context_id, relpath, sha256, size_bytes)
                REFERENCES context_entries(context_id, relpath, sha256, size_bytes)
                ON DELETE RESTRICT,

            CHECK (length(relpath) >= 1),
            CHECK (length(sha256) = 64),
            CHECK (size_bytes >= 0),
            CHECK (ordinal >= 0),
            CHECK (kind IN (
                'overview', 'rules', 'architecture', 'decision',
                'experience', 'reference'
            ))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_knowledge_sources_kind "
        "    ON knowledge_sources(knowledge_id, kind, ordinal)",
        """
        CREATE TABLE IF NOT EXISTS knowledge_resolutions (
            resolution_id  TEXT PRIMARY KEY,
            knowledge_id   TEXT NOT NULL,
            task_id        TEXT,
            operation      TEXT NOT NULL,
            status         TEXT NOT NULL,
            requested_at   REAL NOT NULL,
            finished_at    REAL NOT NULL,
            query_sha256   TEXT NOT NULL,
            output_sha256  TEXT,
            source_count   INTEGER NOT NULL DEFAULT 0,
            citation_count INTEGER NOT NULL DEFAULT 0,
            bytes_returned INTEGER NOT NULL DEFAULT 0,
            error_code     TEXT,
            error_message  TEXT,
            metadata_json  TEXT NOT NULL DEFAULT '{}',

            FOREIGN KEY (knowledge_id)
                REFERENCES knowledge_collections(knowledge_id) ON DELETE CASCADE,
            FOREIGN KEY (task_id)
                REFERENCES tasks(task_id) ON DELETE SET NULL,

            CHECK (operation IN ('search', 'bundle')),
            CHECK (status IN ('succeeded', 'failed', 'rejected')),
            CHECK (length(query_sha256) = 64),
            CHECK (output_sha256 IS NULL OR length(output_sha256) = 64),
            CHECK (source_count >= 0),
            CHECK (citation_count >= 0),
            CHECK (bytes_returned >= 0),
            CHECK (finished_at >= requested_at)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_knowledge_resolutions_time "
        "    ON knowledge_resolutions(requested_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_resolutions_collection "
        "    ON knowledge_resolutions(knowledge_id, requested_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_resolutions_task "
        "    ON knowledge_resolutions(task_id, requested_at DESC)",
    ],
    10: [
        # V1.6 deterministic Planner Foundation.  Plans contain only bounded
        # policy metadata and SHA-256 digests of caller intent.  Raw
        # requirement/acceptance text is deliberately absent.  Preparation
        # returns an execution specification but does not create workflows,
        # tasks, select a backend/model, or dispatch an agent.
        """
        CREATE TABLE IF NOT EXISTS planner_plans (
            plan_id             TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            task_kind           TEXT NOT NULL,
            complexity          TEXT NOT NULL,
            risk_level          TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'draft',
            requirement_sha256  TEXT NOT NULL,
            acceptance_sha256   TEXT NOT NULL,
            policy_version      TEXT NOT NULL,
            step_count          INTEGER NOT NULL,
            knowledge_id        TEXT,
            context_id          TEXT,
            runtime             TEXT,
            agent_profile       TEXT,
            created_at          REAL NOT NULL,
            updated_at          REAL NOT NULL,
            version             INTEGER NOT NULL DEFAULT 1,

            FOREIGN KEY (knowledge_id)
                REFERENCES knowledge_collections(knowledge_id) ON DELETE RESTRICT,
            FOREIGN KEY (context_id)
                REFERENCES context_manifests(context_id) ON DELETE RESTRICT,

            CHECK (length(name) BETWEEN 1 AND 160),
            CHECK (task_kind IN (
                'analysis', 'implementation', 'review',
                'documentation', 'maintenance'
            )),
            CHECK (complexity IN ('low', 'medium', 'high')),
            CHECK (risk_level IN ('low', 'medium', 'high')),
            CHECK (status IN ('draft', 'validated', 'prepared')),
            CHECK (length(requirement_sha256) = 64),
            CHECK (length(acceptance_sha256) = 64),
            CHECK (step_count BETWEEN 1 AND 8),
            CHECK (version >= 1),
            CHECK (updated_at >= created_at)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_planner_plans_status_updated "
        "    ON planner_plans(status, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_planner_plans_knowledge "
        "    ON planner_plans(knowledge_id, created_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS planner_steps (
            step_id                TEXT PRIMARY KEY,
            plan_id                TEXT NOT NULL,
            step_key               TEXT NOT NULL,
            title                  TEXT NOT NULL,
            position               INTEGER NOT NULL,
            kind                   TEXT NOT NULL,
            approval_required      INTEGER NOT NULL DEFAULT 0,
            verification_required  INTEGER NOT NULL DEFAULT 0,
            capabilities_json      TEXT NOT NULL DEFAULT '[]',
            reason_code            TEXT NOT NULL,
            created_at             REAL NOT NULL,

            FOREIGN KEY (plan_id)
                REFERENCES planner_plans(plan_id) ON DELETE CASCADE,

            UNIQUE (plan_id, step_key),
            UNIQUE (plan_id, position),
            UNIQUE (plan_id, step_id),
            CHECK (position BETWEEN 1 AND 8),
            CHECK (kind IN (
                'analysis', 'implementation', 'documentation',
                'verification', 'review', 'report'
            )),
            CHECK (approval_required IN (0, 1)),
            CHECK (verification_required IN (0, 1)),
            CHECK (json_valid(capabilities_json)),
            CHECK (json_type(capabilities_json) = 'array'),
            CHECK (length(reason_code) BETWEEN 1 AND 120)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_planner_steps_order "
        "    ON planner_steps(plan_id, position)",
        """
        CREATE TABLE IF NOT EXISTS planner_dependencies (
            plan_id             TEXT NOT NULL,
            step_id             TEXT NOT NULL,
            depends_on_step_id  TEXT NOT NULL,

            PRIMARY KEY (plan_id, step_id, depends_on_step_id),
            FOREIGN KEY (plan_id, step_id)
                REFERENCES planner_steps(plan_id, step_id) ON DELETE CASCADE,
            FOREIGN KEY (plan_id, depends_on_step_id)
                REFERENCES planner_steps(plan_id, step_id) ON DELETE CASCADE,
            CHECK (step_id != depends_on_step_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_planner_dependencies_step "
        "    ON planner_dependencies(plan_id, step_id)",
        """
        CREATE TABLE IF NOT EXISTS planner_events (
            seq          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id     TEXT NOT NULL UNIQUE,
            plan_id      TEXT NOT NULL,
            event_type   TEXT NOT NULL,
            event_time   REAL NOT NULL,
            status       TEXT NOT NULL,
            reason_code  TEXT NOT NULL,
            step_count   INTEGER NOT NULL,

            FOREIGN KEY (plan_id)
                REFERENCES planner_plans(plan_id) ON DELETE CASCADE,

            CHECK (event_type IN (
                'plan_created', 'plan_validated', 'plan_prepared'
            )),
            CHECK (status IN ('draft', 'validated', 'prepared')),
            CHECK (step_count BETWEEN 1 AND 8),
            CHECK (length(reason_code) BETWEEN 1 AND 240)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_planner_events_plan_seq "
        "    ON planner_events(plan_id, seq)",
    ]
}


def _migrate_v11(connection: sqlite3.Connection) -> None:
    """Apply the V2 execution-control schema atomically.

    ``workflow_stages.status`` has a CHECK constraint in V1.x, so adding the
    V2 ``needs_review`` state requires a table rebuild.  The two child tables
    that reference stages are rebuilt in the same transaction while foreign
    key enforcement is temporarily disabled.  A final ``foreign_key_check``
    is mandatory before commit.
    """
    if connection.in_transaction:
        raise RuntimePersistenceError(
            "V11 migration must start outside an explicit transaction"
        )

    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            stage_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(workflow_stages)"
                ).fetchall()
            }
            if "completion_policy" not in stage_columns:
                connection.execute(
                    """
                    CREATE TABLE workflow_stages_v11 (
                        stage_id              TEXT PRIMARY KEY,
                        workflow_id           TEXT NOT NULL,
                        stage_key             TEXT NOT NULL,
                        title                 TEXT NOT NULL,
                        position              INTEGER NOT NULL,
                        status                TEXT NOT NULL DEFAULT 'pending',
                        approval_required     INTEGER NOT NULL DEFAULT 0,
                        verification_required INTEGER NOT NULL DEFAULT 0,
                        completion_policy      TEXT NOT NULL DEFAULT 'legacy',
                        block_reason           TEXT,
                        runtime               TEXT,
                        agent_profile         TEXT,
                        task_id               TEXT UNIQUE,
                        created_at            REAL NOT NULL,
                        updated_at            REAL NOT NULL,
                        started_at            REAL,
                        finished_at           REAL,

                        FOREIGN KEY (workflow_id)
                            REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                        FOREIGN KEY (task_id)
                            REFERENCES tasks(task_id) ON DELETE SET NULL,

                        UNIQUE (workflow_id, stage_key),
                        UNIQUE (workflow_id, position),
                        CHECK (position >= 1),
                        CHECK (approval_required IN (0, 1)),
                        CHECK (verification_required IN (0, 1)),
                        CHECK (completion_policy IN ('legacy', 'plan_execution_v2')),
                        CHECK (
                            block_reason IS NULL
                            OR length(block_reason) BETWEEN 1 AND 160
                        ),
                        CHECK (status IN (
                            'pending', 'ready', 'running', 'waiting_approval',
                            'needs_review', 'completed', 'failed', 'cancelled',
                            'skipped'
                        ))
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO workflow_stages_v11 (
                        stage_id, workflow_id, stage_key, title, position, status,
                        approval_required, verification_required, completion_policy,
                        block_reason, runtime, agent_profile, task_id, created_at,
                        updated_at, started_at, finished_at
                    )
                    SELECT
                        stage_id, workflow_id, stage_key, title, position, status,
                        approval_required, 0, 'legacy', NULL, runtime, agent_profile,
                        task_id, created_at, updated_at, started_at, finished_at
                    FROM workflow_stages
                    """
                )

                connection.execute(
                    """
                    CREATE TABLE workflow_approvals_v11 (
                        approval_id TEXT PRIMARY KEY,
                        workflow_id TEXT NOT NULL,
                        stage_id    TEXT NOT NULL UNIQUE,
                        decision    TEXT NOT NULL,
                        actor       TEXT NOT NULL DEFAULT 'operator',
                        reason_code TEXT,
                        decided_at  REAL NOT NULL,

                        FOREIGN KEY (workflow_id)
                            REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                        FOREIGN KEY (stage_id)
                            REFERENCES workflow_stages_v11(stage_id) ON DELETE CASCADE,

                        CHECK (decision IN ('approved', 'rejected'))
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO workflow_approvals_v11 (
                        approval_id, workflow_id, stage_id, decision, actor,
                        reason_code, decided_at
                    )
                    SELECT approval_id, workflow_id, stage_id, decision, actor,
                           reason_code, decided_at
                    FROM workflow_approvals
                    """
                )

                connection.execute(
                    """
                    CREATE TABLE workflow_events_v11 (
                        seq          INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id     TEXT NOT NULL UNIQUE,
                        workflow_id  TEXT NOT NULL,
                        stage_id     TEXT,
                        event_type   TEXT NOT NULL,
                        event_time   REAL NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        visibility   TEXT NOT NULL DEFAULT 'public',

                        FOREIGN KEY (workflow_id)
                            REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                        FOREIGN KEY (stage_id)
                            REFERENCES workflow_stages_v11(stage_id) ON DELETE CASCADE,

                        CHECK (visibility IN ('public', 'internal'))
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO workflow_events_v11 (
                        seq, event_id, workflow_id, stage_id, event_type,
                        event_time, payload_json, visibility
                    )
                    SELECT seq, event_id, workflow_id, stage_id, event_type,
                           event_time, payload_json, visibility
                    FROM workflow_events
                    """
                )

                connection.execute("DROP TABLE workflow_approvals")
                connection.execute("DROP TABLE workflow_events")
                connection.execute("DROP TABLE workflow_stages")
                connection.execute(
                    "ALTER TABLE workflow_stages_v11 RENAME TO workflow_stages"
                )
                connection.execute(
                    "ALTER TABLE workflow_approvals_v11 RENAME TO workflow_approvals"
                )
                connection.execute(
                    "ALTER TABLE workflow_events_v11 RENAME TO workflow_events"
                )

                connection.execute(
                    "CREATE INDEX idx_workflow_stages_order "
                    "ON workflow_stages(workflow_id, position)"
                )
                connection.execute(
                    "CREATE INDEX idx_workflow_stages_status "
                    "ON workflow_stages(workflow_id, status)"
                )
                connection.execute(
                    "CREATE INDEX idx_workflow_approvals_workflow "
                    "ON workflow_approvals(workflow_id, decided_at)"
                )
                connection.execute(
                    "CREATE INDEX idx_workflow_events_seq "
                    "ON workflow_events(workflow_id, seq)"
                )

            # V2 Plan Execution control plane.  It binds existing durable
            # Planner/Workflow/Task truth; it does not duplicate Task status.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_executions (
                    execution_id          TEXT PRIMARY KEY,
                    plan_id               TEXT NOT NULL UNIQUE,
                    workflow_id           TEXT NOT NULL UNIQUE,
                    status                TEXT NOT NULL DEFAULT 'ready',
                    reason_code           TEXT,
                    input_manifest_sha256 TEXT NOT NULL,
                    created_at            REAL NOT NULL,
                    updated_at            REAL NOT NULL,
                    started_at            REAL,
                    finished_at           REAL,
                    version               INTEGER NOT NULL DEFAULT 1,

                    FOREIGN KEY (plan_id)
                        REFERENCES planner_plans(plan_id) ON DELETE RESTRICT,
                    FOREIGN KEY (workflow_id)
                        REFERENCES workflows(workflow_id) ON DELETE RESTRICT,

                    CHECK (status IN (
                        'ready', 'running', 'blocked', 'needs_review',
                        'completed', 'failed', 'cancelled'
                    )),
                    CHECK (
                        reason_code IS NULL
                        OR length(reason_code) BETWEEN 1 AND 160
                    ),
                    CHECK (length(input_manifest_sha256) = 64),
                    CHECK (updated_at >= created_at),
                    CHECK (version >= 1)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plan_executions_status_updated "
                "ON plan_executions(status, updated_at DESC)"
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_execution_steps (
                    execution_id          TEXT NOT NULL,
                    step_id               TEXT NOT NULL,
                    stage_id              TEXT NOT NULL UNIQUE,
                    runtime               TEXT NOT NULL,
                    route                 TEXT NOT NULL,
                    model                 TEXT,
                    reasoning_effort      TEXT,
                    agent_profile         TEXT,
                    context_id            TEXT,
                    knowledge_id          TEXT,
                    prompt_sha256         TEXT NOT NULL,
                    knowledge_query_sha256 TEXT,
                    verification_required INTEGER NOT NULL DEFAULT 0,
                    verification_plan_json TEXT NOT NULL DEFAULT '{}',
                    binding_json          TEXT NOT NULL DEFAULT '{}',
                    created_at            REAL NOT NULL,
                    updated_at            REAL NOT NULL,

                    PRIMARY KEY (execution_id, step_id),
                    FOREIGN KEY (execution_id)
                        REFERENCES plan_executions(execution_id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id)
                        REFERENCES planner_steps(step_id) ON DELETE RESTRICT,
                    FOREIGN KEY (stage_id)
                        REFERENCES workflow_stages(stage_id) ON DELETE RESTRICT,
                    FOREIGN KEY (context_id)
                        REFERENCES context_manifests(context_id) ON DELETE RESTRICT,
                    FOREIGN KEY (knowledge_id)
                        REFERENCES knowledge_collections(knowledge_id) ON DELETE RESTRICT,

                    CHECK (length(runtime) BETWEEN 1 AND 32),
                    CHECK (length(route) BETWEEN 1 AND 32),
                    CHECK (length(prompt_sha256) = 64),
                    CHECK (
                        knowledge_query_sha256 IS NULL
                        OR length(knowledge_query_sha256) = 64
                    ),
                    CHECK (verification_required IN (0, 1)),
                    CHECK (json_valid(verification_plan_json)),
                    CHECK (json_type(verification_plan_json) = 'object'),
                    CHECK (json_valid(binding_json)),
                    CHECK (json_type(binding_json) = 'object'),
                    CHECK (updated_at >= created_at)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plan_execution_steps_execution "
                "ON plan_execution_steps(execution_id, stage_id)"
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_execution_events (
                    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id     TEXT NOT NULL UNIQUE,
                    execution_id TEXT NOT NULL,
                    step_id      TEXT,
                    event_type   TEXT NOT NULL,
                    event_time   REAL NOT NULL,
                    status       TEXT NOT NULL,
                    reason_code  TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',

                    FOREIGN KEY (execution_id)
                        REFERENCES plan_executions(execution_id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id)
                        REFERENCES planner_steps(step_id) ON DELETE SET NULL,

                    CHECK (event_type IN (
                        'execution_created', 'workflow_created',
                        'step_material_bound', 'step_ready', 'task_created',
                        'task_bound', 'stage_advanced', 'gate_blocked',
                        'input_required', 'execution_resumed',
                        'execution_cancel_requested', 'execution_completed',
                        'execution_failed', 'execution_cancelled'
                    )),
                    CHECK (status IN (
                        'ready', 'running', 'blocked', 'needs_review',
                        'completed', 'failed', 'cancelled'
                    )),
                    CHECK (
                        reason_code IS NULL
                        OR length(reason_code) BETWEEN 1 AND 160
                    ),
                    CHECK (json_valid(payload_json)),
                    CHECK (json_type(payload_json) = 'object')
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plan_execution_events_execution_seq "
                "ON plan_execution_events(execution_id, seq)"
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_results (
                    execution_id TEXT PRIMARY KEY,
                    schema       TEXT NOT NULL,
                    result_json  TEXT NOT NULL,
                    created_at   REAL NOT NULL,

                    FOREIGN KEY (execution_id)
                        REFERENCES plan_executions(execution_id) ON DELETE CASCADE,

                    CHECK (length(schema) BETWEEN 1 AND 120),
                    CHECK (json_valid(result_json)),
                    CHECK (json_type(result_json) = 'object')
                )
                """
            )

            violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    f"V11 foreign key check failed: {len(violations)} violation(s)"
                )
            connection.execute("PRAGMA user_version = 11")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")



def _migrate_v12(connection: sqlite3.Connection) -> None:
    """Allow immutable ``usage`` Evidence without adding a second store.

    SQLite cannot alter the existing evidence-type CHECK in place, so rebuild
    only the Evidence table, preserve every row, and verify all foreign keys
    before publishing schema v12.
    """
    if connection.in_transaction:
        raise RuntimePersistenceError(
            "V12 migration must start outside an explicit transaction"
        )
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                CREATE TABLE evidences_v12 (
                    evidence_id         TEXT PRIMARY KEY,
                    task_id             TEXT NOT NULL,
                    attempt_id          TEXT NOT NULL,
                    subject_evidence_id TEXT,
                    artifact_id         TEXT,
                    evidence_type       TEXT NOT NULL,
                    trust_state         TEXT NOT NULL,
                    origin              TEXT NOT NULL,
                    summary             TEXT NOT NULL DEFAULT '',
                    detail_json         TEXT NOT NULL DEFAULT '{}',
                    captured_at         REAL NOT NULL,
                    created_at          REAL NOT NULL,

                    FOREIGN KEY (task_id, attempt_id)
                        REFERENCES attempts(task_id, attempt_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (task_id, attempt_id, subject_evidence_id)
                        REFERENCES evidences_v12(task_id, attempt_id, evidence_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (task_id, attempt_id, artifact_id)
                        REFERENCES artifacts(task_id, attempt_id, artifact_id)
                        ON DELETE CASCADE,

                    UNIQUE (task_id, attempt_id, evidence_id),
                    CHECK (evidence_type IN (
                        'agent_claim', 'test', 'command',
                        'file', 'review', 'artifact', 'usage'
                    )),
                    CHECK (trust_state IN (
                        'declared', 'observed',
                        'verified_passed', 'verified_failed',
                        'needs_review', 'skipped'
                    )),
                    CHECK (origin IN (
                        'agent', 'backend', 'runtime',
                        'verifier', 'human'
                    )),
                    CHECK (
                        (trust_state = 'declared'
                            AND origin IN ('agent', 'backend', 'human'))
                        OR (trust_state = 'observed'
                            AND origin IN ('backend', 'runtime', 'human'))
                        OR (trust_state IN (
                                'verified_passed', 'verified_failed', 'needs_review')
                            AND origin IN ('verifier', 'human'))
                        OR (trust_state = 'skipped'
                            AND origin IN ('backend', 'runtime', 'verifier', 'human'))
                    ),
                    CHECK (
                        evidence_type != 'agent_claim'
                        OR (origin = 'agent' AND trust_state = 'declared')
                    ),
                    CHECK (
                        (evidence_type = 'artifact' AND artifact_id IS NOT NULL)
                        OR (evidence_type != 'artifact' AND artifact_id IS NULL)
                    ),
                    CHECK (subject_evidence_id IS NULL
                        OR subject_evidence_id != evidence_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO evidences_v12 (
                    evidence_id, task_id, attempt_id, subject_evidence_id,
                    artifact_id, evidence_type, trust_state, origin, summary,
                    detail_json, captured_at, created_at
                )
                SELECT
                    evidence_id, task_id, attempt_id, subject_evidence_id,
                    artifact_id, evidence_type, trust_state, origin, summary,
                    detail_json, captured_at, created_at
                FROM evidences
                """
            )
            connection.execute("DROP TABLE evidences")
            connection.execute("ALTER TABLE evidences_v12 RENAME TO evidences")
            connection.execute(
                "CREATE INDEX idx_evidences_task_attempt "
                "ON evidences(task_id, attempt_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX idx_evidences_subject "
                "ON evidences(subject_evidence_id)"
            )
            connection.execute(
                "CREATE INDEX idx_evidences_type_state "
                "ON evidences(evidence_type, trust_state)"
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    f"V12 foreign key check failed: {len(violations)} violation(s)"
                )
            connection.execute("PRAGMA user_version = 12")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")



def _migrate_v13(connection: sqlite3.Connection) -> None:
    """Add v1.0.5 run provenance and the resource-only RunControl ledger."""
    if connection.in_transaction:
        raise RuntimePersistenceError("V13 migration must start outside an explicit transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        if "run_id" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN run_id TEXT")
        if "step_key" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN step_key TEXT")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_run_step "
            "ON tasks(run_id, step_key) "
            "WHERE run_id IS NOT NULL AND run_id <> '' AND step_key IS NOT NULL AND step_key <> ''"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_run_id ON tasks(run_id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_controls (
                run_id TEXT PRIMARY KEY,
                max_dispatches INTEGER NOT NULL,
                max_runtime_seconds REAL NOT NULL,
                max_input_tokens INTEGER,
                max_output_tokens INTEGER,
                max_credits REAL,
                require_strict_usage_budget INTEGER NOT NULL DEFAULT 0,
                dispatches_reserved INTEGER NOT NULL DEFAULT 0,
                dispatches_consumed INTEGER NOT NULL DEFAULT 0,
                runtime_reserved_seconds REAL NOT NULL DEFAULT 0,
                runtime_consumed_seconds REAL NOT NULL DEFAULT 0,
                input_tokens_consumed INTEGER,
                output_tokens_consumed INTEGER,
                credits_consumed REAL,
                usage_complete INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'open',
                revision INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                CHECK (length(run_id) BETWEEN 1 AND 160),
                CHECK (max_dispatches > 0),
                CHECK (max_runtime_seconds > 0),
                CHECK (max_input_tokens IS NULL OR max_input_tokens > 0),
                CHECK (max_output_tokens IS NULL OR max_output_tokens > 0),
                CHECK (max_credits IS NULL OR max_credits > 0),
                CHECK (require_strict_usage_budget IN (0,1)),
                CHECK (dispatches_reserved >= 0 AND dispatches_consumed >= 0),
                CHECK (runtime_reserved_seconds >= 0 AND runtime_consumed_seconds >= 0),
                CHECK (usage_complete IN (0,1)),
                CHECK (status IN ('open','exhausted','closed')),
                CHECK (revision > 0),
                CHECK (updated_at >= created_at)
            )
            """
        )
        connection.execute("PRAGMA user_version = 13")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def migrate(connection: sqlite3.Connection) -> None:
    """Apply all pending migrations; idempotent and repeatable."""
    if connection.in_transaction:
        raise RuntimePersistenceError(
            "migrate() must run outside an explicit transaction"
        )
    try:
        current = _user_version(connection)
        for version in sorted(_MIGRATIONS):
            if version <= current:
                continue
            with connection:
                for statement in _MIGRATIONS[version]:
                    try:
                        connection.execute(statement)
                    except sqlite3.OperationalError as exc:
                        # ALTER TABLE ADD COLUMN may fail when the column
                        # already exists (two runs on the same schema).
                        if "duplicate column" in str(exc).lower():
                            continue
                        raise
                connection.execute(f"PRAGMA user_version = {int(version)}")
            current = version
        if current < 11:
            _migrate_v11(connection)
        current = _user_version(connection)
        if current < 12:
            _migrate_v12(connection)
        current = _user_version(connection)
        if current < 13:
            _migrate_v13(connection)
    except sqlite3.Error as exc:
        raise RuntimePersistenceError(f"migration failed: {exc}") from exc


def schema_version(connection: sqlite3.Connection) -> int:
    return _user_version(connection)


def _user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0