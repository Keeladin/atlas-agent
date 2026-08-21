from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .store_common import RUNTIME_SCHEMA_VERSION, WORK_SCHEMA_VERSION, TaskStoreError


class TaskStoreSchemaMixin:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    @contextmanager
    def _db(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS atlas_schema_meta (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            row = db.execute(
                "SELECT version FROM atlas_schema_meta WHERE component='task_runtime'"
            ).fetchone()
            existing_version = int(row["version"]) if row is not None else None
            if existing_version is not None and existing_version > RUNTIME_SCHEMA_VERSION:
                raise TaskStoreError(
                    "Task database was created by a newer Atlas runtime schema."
                )

            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    success_criteria_json TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    authority_scope TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('planned','active','waiting','completed','failed','cancelled')),
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS task_criteria (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('pending','accepted','rejected','unknown')),
                    evidence_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    note TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    UNIQUE(task_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS task_steps (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    capability TEXT,
                    capability_version TEXT,
                    status TEXT NOT NULL CHECK (status IN
                        ('pending','running','pass','rework','blocked','failed','skipped')),
                    dependencies_json TEXT NOT NULL,
                    input_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    UNIQUE(task_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES task_steps(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_artifacts_task
                    ON task_artifacts(task_id, created_at, id);

                CREATE TABLE IF NOT EXISTS task_executions (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    capability_version TEXT NOT NULL DEFAULT '1.0.0',
                    provider TEXT,
                    attempt INTEGER NOT NULL CHECK (attempt >= 1),
                    status TEXT NOT NULL CHECK (status IN
                        ('running','pass','rework','abstain','fail','blocked')),
                    input_artifact_ids_json TEXT NOT NULL,
                    output_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    verifier_artifact_id TEXT,
                    receipt_json TEXT NOT NULL DEFAULT '{}',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    ended_at TEXT,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES task_steps(id) ON DELETE CASCADE,
                    FOREIGN KEY (verifier_artifact_id) REFERENCES task_artifacts(id) ON DELETE SET NULL,
                    UNIQUE(step_id, attempt)
                );
                CREATE INDEX IF NOT EXISTS idx_task_executions_step
                    ON task_executions(step_id, attempt);

                CREATE TABLE IF NOT EXISTS task_context_manifests (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL UNIQUE,
                    capability TEXT NOT NULL,
                    capability_version TEXT NOT NULL,
                    assembler_version TEXT NOT NULL,
                    budget_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    manifest_json TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES task_steps(id) ON DELETE CASCADE,
                    FOREIGN KEY (execution_id) REFERENCES task_executions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_context_manifests_task
                    ON task_context_manifests(task_id, step_id, created_at, id);

                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS task_claims (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT,
                    kind TEXT NOT NULL CHECK (kind IN
                        ('observed','retrieved','calculated','inferred','suggested','executed')),
                    subject TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    evidence_artifact_ids_json TEXT NOT NULL,
                    confidence REAL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES task_steps(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS task_approvals (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT,
                    required_authority TEXT NOT NULL,
                    requested_action TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('pending','approved','denied','cancelled')),
                    decision_note TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    decided_at TEXT,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES task_steps(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    step_id TEXT,
                    execution_id TEXT,
                    name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES task_steps(id) ON DELETE SET NULL,
                    FOREIGN KEY (execution_id) REFERENCES task_executions(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, id);
                """
            )

            step_columns = {
                item["name"] for item in db.execute("PRAGMA table_info(task_steps)").fetchall()
            }
            if "capability_version" not in step_columns:
                db.execute("ALTER TABLE task_steps ADD COLUMN capability_version TEXT")
            execution_columns = {
                item["name"] for item in db.execute("PRAGMA table_info(task_executions)").fetchall()
            }
            if "capability_version" not in execution_columns:
                db.execute(
                    "ALTER TABLE task_executions ADD COLUMN capability_version TEXT NOT NULL DEFAULT '1.0.0'"
                )

            if existing_version is None:
                db.execute(
                    "INSERT INTO atlas_schema_meta (component,version) VALUES ('task_runtime',?)",
                    (RUNTIME_SCHEMA_VERSION,),
                )
            elif existing_version != RUNTIME_SCHEMA_VERSION:
                db.execute(
                    "UPDATE atlas_schema_meta SET version=?,updated_at=CURRENT_TIMESTAMP WHERE component='task_runtime'",
                    (RUNTIME_SCHEMA_VERSION,),
                )

    def initialize_work_schema(self) -> None:
        """Additive Work contract table on the Work DB."""

        with self._db() as db:
            work_row = db.execute(
                "SELECT version FROM atlas_schema_meta WHERE component='work_runtime'"
            ).fetchone()
            work_version = int(work_row["version"]) if work_row is not None else None
            if work_version is not None and work_version > WORK_SCHEMA_VERSION:
                raise TaskStoreError(
                    "Task database was created by a newer Atlas work schema."
                )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS work_contracts (
                    work_id TEXT PRIMARY KEY,
                    contract_id TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    compiled_at TEXT NOT NULL,
                    FOREIGN KEY (work_id) REFERENCES tasks(id) ON DELETE CASCADE
                )
                """
            )
            if work_version is None:
                db.execute(
                    "INSERT INTO atlas_schema_meta (component,version) VALUES ('work_runtime',?)",
                    (WORK_SCHEMA_VERSION,),
                )
            elif work_version != WORK_SCHEMA_VERSION:
                db.execute(
                    "UPDATE atlas_schema_meta SET version=?,updated_at=CURRENT_TIMESTAMP WHERE component='work_runtime'",
                    (WORK_SCHEMA_VERSION,),
                )
