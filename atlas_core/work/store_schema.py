from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path

from .store_common import WORK_SCHEMA_VERSION, WorkStoreError


class WorkStoreSchemaMixin:
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

    @contextmanager
    def _immediate(self):
        """Exclusive read-modify-write. Serializes control/execution writers."""

        db = self._connect()
        previous = db.isolation_level
        try:
            db.isolation_level = None
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            else:
                db.execute("COMMIT")
        finally:
            db.isolation_level = previous
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
                "SELECT version FROM atlas_schema_meta WHERE component='work'"
            ).fetchone()
            existing_version = int(row["version"]) if row is not None else None
            if existing_version is not None and existing_version > WORK_SCHEMA_VERSION:
                raise WorkStoreError(
                    "Work database was created by a newer Atlas work schema."
                )

            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS work (
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

                CREATE TABLE IF NOT EXISTS work_criteria (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('pending','accepted','rejected','unknown')),
                    evidence_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    note TEXT,
                    satisfaction_policy TEXT NOT NULL DEFAULT 'deliverable' CHECK
                        (satisfaction_policy IN ('deliverable','evidence_grounded')),
                    semantic_verification TEXT NOT NULL DEFAULT 'none' CHECK
                        (semantic_verification IN ('none','required')),
                    verification_artifact_id TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    FOREIGN KEY (verification_artifact_id) REFERENCES work_artifacts(id) ON DELETE SET NULL,
                    UNIQUE(work_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS work_steps (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    capability TEXT,
                    capability_version TEXT,
                    contract_capability_ordinal INTEGER,
                    status TEXT NOT NULL CHECK (status IN
                        ('pending','running','pass','rework','blocked','failed','skipped')),
                    dependencies_json TEXT NOT NULL,
                    input_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    UNIQUE(work_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS work_artifacts (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    step_id TEXT,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    provenance_category TEXT NOT NULL DEFAULT 'generated_deliverable'
                        CHECK (provenance_category IN
                            ('invocation_input','acquired_observation','acquired_content','generated_deliverable',
                             'execution_receipt','verifier_result')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES work_steps(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_work_artifacts_work
                    ON work_artifacts(work_id, created_at, id);

                CREATE TABLE IF NOT EXISTS work_executions (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
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
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES work_steps(id) ON DELETE CASCADE,
                    FOREIGN KEY (verifier_artifact_id) REFERENCES work_artifacts(id) ON DELETE SET NULL,
                    UNIQUE(step_id, attempt)
                );
                CREATE INDEX IF NOT EXISTS idx_work_executions_step
                    ON work_executions(step_id, attempt);

                CREATE TABLE IF NOT EXISTS work_context_manifests (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
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
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES work_steps(id) ON DELETE CASCADE,
                    FOREIGN KEY (execution_id) REFERENCES work_executions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_work_context_manifests_work
                    ON work_context_manifests(work_id, step_id, created_at, id);

                CREATE TABLE IF NOT EXISTS work_checkpoints (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS work_claims (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    step_id TEXT,
                    kind TEXT NOT NULL CHECK (kind IN
                        ('observed','retrieved','calculated','inferred','suggested','executed')),
                    subject TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    evidence_artifact_ids_json TEXT NOT NULL,
                    confidence REAL,
                    execution_id TEXT,
                    context_manifest_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES work_steps(id) ON DELETE SET NULL,
                    FOREIGN KEY (execution_id) REFERENCES work_executions(id) ON DELETE SET NULL,
                    FOREIGN KEY (context_manifest_id) REFERENCES work_context_manifests(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS work_claim_criteria (
                    claim_id TEXT NOT NULL,
                    criterion_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (claim_id, criterion_id),
                    FOREIGN KEY (claim_id) REFERENCES work_claims(id) ON DELETE CASCADE,
                    FOREIGN KEY (criterion_id) REFERENCES work_criteria(id) ON DELETE CASCADE,
                    FOREIGN KEY (execution_id) REFERENCES work_executions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS work_criterion_verifications (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    criterion_id TEXT NOT NULL,
                    contract_capability_ordinal INTEGER NOT NULL,
                    step_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    evaluation_attempt INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pass','rework','abstain','fail')),
                    input_sha256 TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    FOREIGN KEY (criterion_id) REFERENCES work_criteria(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES work_steps(id) ON DELETE CASCADE,
                    FOREIGN KEY (execution_id) REFERENCES work_executions(id) ON DELETE CASCADE,
                    FOREIGN KEY (artifact_id) REFERENCES work_artifacts(id) ON DELETE CASCADE,
                    UNIQUE (criterion_id, evaluation_attempt)
                );

                CREATE TABLE IF NOT EXISTS work_approvals (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    step_id TEXT,
                    required_authority TEXT NOT NULL,
                    requested_action TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('pending','approved','denied','cancelled')),
                    decision_note TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    decided_at TEXT,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES work_steps(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS work_confirmations (
                    id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('pending','confirmed','denied','cancelled')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    decided_at TEXT,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES work_steps(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_work_confirmations_work
                    ON work_confirmations(work_id, created_at, id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_work_confirmations_active
                    ON work_confirmations(step_id, payload_sha256)
                    WHERE status IN ('pending', 'confirmed');

                CREATE TABLE IF NOT EXISTS work_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_id TEXT NOT NULL,
                    step_id TEXT,
                    execution_id TEXT,
                    name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                    FOREIGN KEY (step_id) REFERENCES work_steps(id) ON DELETE SET NULL,
                    FOREIGN KEY (execution_id) REFERENCES work_executions(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_work_events_work ON work_events(work_id, id);

                CREATE TABLE IF NOT EXISTS work_contracts (
                    work_id TEXT PRIMARY KEY,
                    contract_id TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    compiled_at TEXT NOT NULL,
                    FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE
                );
                """
            )

            artifact_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(work_artifacts)")
            }
            if "provenance_category" not in artifact_columns:
                db.execute(
                    "ALTER TABLE work_artifacts ADD COLUMN provenance_category "
                    "TEXT NOT NULL DEFAULT 'generated_deliverable'"
                )
                db.execute(
                    "UPDATE work_artifacts SET provenance_category='execution_receipt' "
                    "WHERE kind='execution_receipt'"
                )
                db.execute(
                    "UPDATE work_artifacts SET provenance_category='verifier_result' "
                    "WHERE kind IN ('verification_result','criterion_verification_result')"
                )

            artifact_sql_row = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='work_artifacts'"
            ).fetchone()
            artifact_sql = "" if artifact_sql_row is None else str(artifact_sql_row["sql"] or "")
            if "invocation_input" not in artifact_sql:
                db.commit()
                db.execute("PRAGMA foreign_keys = OFF")
                db.executescript(
                    """
                    CREATE TABLE work_artifacts_v5 (
                        id TEXT PRIMARY KEY,
                        work_id TEXT NOT NULL,
                        step_id TEXT,
                        kind TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        provenance_category TEXT NOT NULL DEFAULT 'generated_deliverable'
                            CHECK (provenance_category IN
                                ('invocation_input','acquired_observation','acquired_content',
                                 'generated_deliverable','execution_receipt','verifier_result')),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
                        FOREIGN KEY (step_id) REFERENCES work_steps(id) ON DELETE SET NULL
                    );
                    INSERT INTO work_artifacts_v5
                        (id,work_id,step_id,kind,payload_json,sha256,metadata_json,
                         provenance_category,created_at)
                    SELECT id,work_id,step_id,kind,payload_json,sha256,metadata_json,
                           provenance_category,created_at
                    FROM work_artifacts;
                    DROP TABLE work_artifacts;
                    ALTER TABLE work_artifacts_v5 RENAME TO work_artifacts;
                    CREATE INDEX idx_work_artifacts_work
                        ON work_artifacts(work_id, created_at, id);
                    """
                )
                db.execute("PRAGMA foreign_keys = ON")

            # Version 3 adds policy/provenance columns to existing durable rows.
            # CREATE TABLE IF NOT EXISTS does not evolve pre-existing tables.
            criterion_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(work_criteria)")
            }
            if "satisfaction_policy" not in criterion_columns:
                db.execute(
                    "ALTER TABLE work_criteria ADD COLUMN satisfaction_policy "
                    "TEXT NOT NULL DEFAULT 'deliverable'"
                )
            if "semantic_verification" not in criterion_columns:
                db.execute(
                    "ALTER TABLE work_criteria ADD COLUMN semantic_verification "
                    "TEXT NOT NULL DEFAULT 'none'"
                )
            if "verification_artifact_id" not in criterion_columns:
                db.execute(
                    "ALTER TABLE work_criteria ADD COLUMN verification_artifact_id TEXT"
                )
            step_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(work_steps)")
            }
            if "contract_capability_ordinal" not in step_columns:
                db.execute(
                    "ALTER TABLE work_steps ADD COLUMN contract_capability_ordinal INTEGER"
                )
            claim_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(work_claims)")
            }
            if "execution_id" not in claim_columns:
                db.execute("ALTER TABLE work_claims ADD COLUMN execution_id TEXT")
            if "context_manifest_id" not in claim_columns:
                db.execute("ALTER TABLE work_claims ADD COLUMN context_manifest_id TEXT")
            artifact_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(work_artifacts)")
            }
            if "provenance_category" not in artifact_columns:
                db.execute(
                    "ALTER TABLE work_artifacts ADD COLUMN provenance_category "
                    "TEXT NOT NULL DEFAULT 'generated_deliverable'"
                )
                db.execute(
                    "UPDATE work_artifacts SET provenance_category='execution_receipt' "
                    "WHERE kind='execution_receipt'"
                )
                db.execute(
                    "UPDATE work_artifacts SET provenance_category='verifier_result' "
                    "WHERE kind IN ('verification_result','criterion_verification_result')"
                )
            if existing_version is not None and existing_version < 3:
                # Phase A contracts already froze the global policy. Materialize
                # that policy onto their legacy criterion rows during migration.
                for row in db.execute("SELECT work_id,payload_json FROM work_contracts"):
                    try:
                        payload = json.loads(row["payload_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if payload.get("completion_grounding_policy") == "evidence_required":
                        db.execute(
                            "UPDATE work_criteria SET satisfaction_policy='evidence_grounded', "
                            "semantic_verification='required' WHERE work_id=?",
                            (row["work_id"],),
                        )

            if existing_version is None:
                db.execute(
                    "INSERT INTO atlas_schema_meta (component,version) VALUES ('work',?)",
                    (WORK_SCHEMA_VERSION,),
                )
            elif existing_version != WORK_SCHEMA_VERSION:
                db.execute(
                    "UPDATE atlas_schema_meta SET version=?,updated_at=CURRENT_TIMESTAMP WHERE component='work'",
                    (WORK_SCHEMA_VERSION,),
                )
