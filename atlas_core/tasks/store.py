from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from atlas_core.authority import validate_authority
from .models import (
    APPROVAL_STATUSES,
    CLAIM_KINDS,
    CRITERION_STATUSES,
    EXECUTION_STATUSES,
    STEP_STATUSES,
    TASK_STATUSES,
    ApprovalRecord,
    ArtifactRecord,
    CheckpointRecord,
    ClaimRecord,
    CriterionRecord,
    EventRecord,
    ExecutionRecord,
    StepRecord,
    TaskRecord,
)


class TaskStoreError(RuntimeError):
    pass


class UnknownRecordError(TaskStoreError):
    pass


class InvalidTransitionError(TaskStoreError):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None, default: Any) -> Any:
    return default if value in (None, "") else json.loads(value)


def _payload_hash(payload: Any) -> tuple[str, str]:
    encoded = _json_dump(payload)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_TASK_TRANSITIONS = {
    "planned": {"active", "cancelled", "failed"},
    "active": {"waiting", "completed", "failed", "cancelled"},
    "waiting": {"active", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}
_STEP_TRANSITIONS = {
    "pending": {"running", "blocked", "skipped", "failed"},
    "running": {"pass", "rework", "blocked", "failed"},
    "rework": {"running", "blocked", "failed", "skipped"},
    "blocked": {"pending", "running", "failed", "skipped"},
    "pass": set(),
    "failed": set(),
    "skipped": set(),
}


class TaskStore:
    """Durable SQLite source of truth for Atlas task execution."""

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

    def create_task(
        self,
        *,
        objective: str,
        success_criteria: Iterable[str],
        constraints: Iterable[str] = (),
        authority_scope: str = "read",
        status: str = "planned",
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> TaskRecord:
        objective = objective.strip()
        if not objective:
            raise ValueError("Task objective must not be empty.")
        criteria = tuple(x.strip() for x in success_criteria if x.strip())
        if not criteria:
            raise ValueError("Task must have at least one success criterion.")
        constraints_tuple = tuple(x.strip() for x in constraints if x.strip())
        if status not in TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {status}")
        authority_scope = validate_authority(authority_scope)
        task_id = task_id or _new_id("task")
        with self._db() as db:
            db.execute(
                "INSERT INTO tasks (id,objective,success_criteria_json,constraints_json,authority_scope,status,metadata_json) VALUES (?,?,?,?,?,?,?)",
                (task_id, objective, _json_dump(criteria), _json_dump(constraints_tuple), authority_scope, status, _json_dump(metadata or {})),
            )
            for ordinal, text in enumerate(criteria, start=1):
                db.execute(
                    "INSERT INTO task_criteria (id,task_id,ordinal,text,status) VALUES (?,?,?,?, 'pending')",
                    (_new_id("criterion"), task_id, ordinal, text),
                )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown task: {task_id}")
        return self._task_from_row(row)

    def list_tasks(self, *, status: str | None = None) -> tuple[TaskRecord, ...]:
        with self._db() as db:
            if status is None:
                rows = db.execute("SELECT * FROM tasks ORDER BY created_at,id").fetchall()
            else:
                if status not in TASK_STATUSES:
                    raise ValueError(f"Unsupported task status: {status}")
                rows = db.execute("SELECT * FROM tasks WHERE status=? ORDER BY created_at,id", (status,)).fetchall()
        return tuple(self._task_from_row(row) for row in rows)

    def set_task_status(self, task_id: str, status: str, *, force: bool = False) -> TaskRecord:
        if status not in TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {status}")
        current = self.get_task(task_id)
        if current.status == status:
            return current
        if not force and status not in _TASK_TRANSITIONS[current.status]:
            raise InvalidTransitionError(f"Task transition {current.status} -> {status} is not allowed.")
        with self._db() as db:
            db.execute("UPDATE tasks SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, task_id))
        return self.get_task(task_id)

    def list_criteria(self, task_id: str) -> tuple[CriterionRecord, ...]:
        self.get_task(task_id)
        with self._db() as db:
            rows = db.execute("SELECT * FROM task_criteria WHERE task_id=? ORDER BY ordinal", (task_id,)).fetchall()
        return tuple(self._criterion_from_row(row) for row in rows)

    def set_criterion_status(
        self,
        criterion_id: str,
        status: str,
        *,
        evidence_artifact_ids: Iterable[str] = (),
        note: str | None = None,
    ) -> CriterionRecord:
        if status not in CRITERION_STATUSES:
            raise ValueError(f"Unsupported criterion status: {status}")
        with self._db() as db:
            row = db.execute("SELECT * FROM task_criteria WHERE id=?", (criterion_id,)).fetchone()
            if row is None:
                raise UnknownRecordError(f"Unknown criterion: {criterion_id}")
            task_id = row["task_id"]
        evidence_ids = tuple(dict.fromkeys(evidence_artifact_ids))
        self._validate_artifacts_for_task(task_id, evidence_ids)
        with self._db() as db:
            db.execute(
                "UPDATE task_criteria SET status=?,evidence_artifact_ids_json=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, _json_dump(evidence_ids), note, criterion_id),
            )
            row = db.execute("SELECT * FROM task_criteria WHERE id=?", (criterion_id,)).fetchone()
        return self._criterion_from_row(row)

    def add_step(
        self,
        task_id: str,
        *,
        description: str,
        capability: str | None = None,
        dependencies: Iterable[str] = (),
        input_artifact_ids: Iterable[str] = (),
        ordinal: int | None = None,
        metadata: dict[str, Any] | None = None,
        step_id: str | None = None,
    ) -> StepRecord:
        self.get_task(task_id)
        description = description.strip()
        if not description:
            raise ValueError("Step description must not be empty.")
        dep_ids = tuple(dict.fromkeys(dependencies))
        input_ids = tuple(dict.fromkeys(input_artifact_ids))
        with self._db() as db:
            for dep_id in dep_ids:
                row = db.execute("SELECT task_id FROM task_steps WHERE id=?", (dep_id,)).fetchone()
                if row is None:
                    raise UnknownRecordError(f"Unknown dependency step: {dep_id}")
                if row["task_id"] != task_id:
                    raise ValueError("Step dependencies must belong to the same task.")
            if ordinal is None:
                row = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 AS next_ordinal FROM task_steps WHERE task_id=?", (task_id,)).fetchone()
                ordinal = int(row["next_ordinal"])
        self._validate_artifacts_for_task(task_id, input_ids)
        step_id = step_id or _new_id("step")
        with self._db() as db:
            db.execute(
                "INSERT INTO task_steps (id,task_id,ordinal,description,capability,status,dependencies_json,input_artifact_ids_json,metadata_json) VALUES (?,?,?,?,?,'pending',?,?,?)",
                (step_id, task_id, ordinal, description, capability.strip() if capability else None, _json_dump(dep_ids), _json_dump(input_ids), _json_dump(metadata or {})),
            )
        return self.get_step(step_id)

    def get_step(self, step_id: str) -> StepRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM task_steps WHERE id=?", (step_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown step: {step_id}")
        return self._step_from_row(row)

    def list_steps(self, task_id: str) -> tuple[StepRecord, ...]:
        self.get_task(task_id)
        with self._db() as db:
            rows = db.execute("SELECT * FROM task_steps WHERE task_id=? ORDER BY ordinal,id", (task_id,)).fetchall()
        return tuple(self._step_from_row(row) for row in rows)

    def set_step_status(self, step_id: str, status: str, *, force: bool = False) -> StepRecord:
        if status not in STEP_STATUSES:
            raise ValueError(f"Unsupported step status: {status}")
        current = self.get_step(step_id)
        if current.status == status:
            return current
        if not force and status not in _STEP_TRANSITIONS[current.status]:
            raise InvalidTransitionError(f"Step transition {current.status} -> {status} is not allowed.")
        with self._db() as db:
            db.execute("UPDATE task_steps SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, step_id))
        return self.get_step(step_id)

    def ready_steps(self, task_id: str) -> tuple[StepRecord, ...]:
        steps = self.list_steps(task_id)
        by_id = {step.id: step for step in steps}
        ready: list[StepRecord] = []
        for step in steps:
            if step.status not in {"pending", "rework"}:
                continue
            if all(by_id[dep].status in {"pass", "skipped"} for dep in step.dependencies):
                ready.append(step)
        return tuple(ready)

    def put_artifact(
        self,
        task_id: str,
        *,
        kind: str,
        payload: Any,
        step_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRecord:
        self.get_task(task_id)
        if step_id is not None and self.get_step(step_id).task_id != task_id:
            raise ValueError("Artifact step must belong to the same task.")
        kind = kind.strip()
        if not kind:
            raise ValueError("Artifact kind must not be empty.")
        encoded, digest = _payload_hash(payload)
        artifact_id = artifact_id or _new_id("artifact")
        with self._db() as db:
            db.execute(
                "INSERT INTO task_artifacts (id,task_id,step_id,kind,payload_json,sha256,metadata_json) VALUES (?,?,?,?,?,?,?)",
                (artifact_id, task_id, step_id, kind, encoded, digest, _json_dump(metadata or {})),
            )
        return self.get_artifact(artifact_id)

    def get_artifact(self, artifact_id: str) -> ArtifactRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM task_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown artifact: {artifact_id}")
        return self._artifact_from_row(row)

    def list_artifacts(self, task_id: str, *, step_id: str | None = None) -> tuple[ArtifactRecord, ...]:
        self.get_task(task_id)
        with self._db() as db:
            if step_id is None:
                rows = db.execute("SELECT * FROM task_artifacts WHERE task_id=? ORDER BY created_at,id", (task_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM task_artifacts WHERE task_id=? AND step_id=? ORDER BY created_at,id", (task_id, step_id)).fetchall()
        return tuple(self._artifact_from_row(row) for row in rows)

    def begin_execution(
        self,
        task_id: str,
        *,
        step_id: str,
        capability: str,
        provider: str | None = None,
        input_artifact_ids: Iterable[str] = (),
        execution_id: str | None = None,
    ) -> ExecutionRecord:
        task = self.get_task(task_id)
        if task.status not in {"planned", "active", "waiting"}:
            raise InvalidTransitionError(f"Cannot execute terminal task {task_id}.")
        step = self.get_step(step_id)
        if step.task_id != task_id:
            raise ValueError("Execution step must belong to the same task.")
        if step.status not in {"pending", "rework", "blocked"}:
            raise InvalidTransitionError(f"Step is not executable from status {step.status}.")
        capability = capability.strip()
        if not capability:
            raise ValueError("Execution capability must not be empty.")
        input_ids = tuple(dict.fromkeys(input_artifact_ids))
        self._validate_artifacts_for_task(task_id, input_ids)
        with self._db() as db:
            row = db.execute("SELECT COALESCE(MAX(attempt),0)+1 AS attempt FROM task_executions WHERE step_id=?", (step_id,)).fetchone()
            attempt = int(row["attempt"])
        execution_id = execution_id or _new_id("execution")
        with self._db() as db:
            db.execute(
                "INSERT INTO task_executions (id,task_id,step_id,capability,provider,attempt,status,input_artifact_ids_json) VALUES (?,?,?,?,?,?,'running',?)",
                (execution_id, task_id, step_id, capability, provider.strip() if provider else None, attempt, _json_dump(input_ids)),
            )
            db.execute("UPDATE task_steps SET status='running',updated_at=CURRENT_TIMESTAMP WHERE id=?", (step_id,))
        return self.get_execution(execution_id)

    def get_execution(self, execution_id: str) -> ExecutionRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM task_executions WHERE id=?", (execution_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown execution: {execution_id}")
        return self._execution_from_row(row)

    def list_executions(self, task_id: str, *, step_id: str | None = None) -> tuple[ExecutionRecord, ...]:
        self.get_task(task_id)
        with self._db() as db:
            if step_id is None:
                rows = db.execute("SELECT * FROM task_executions WHERE task_id=? ORDER BY rowid", (task_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM task_executions WHERE task_id=? AND step_id=? ORDER BY attempt", (task_id, step_id)).fetchall()
        return tuple(self._execution_from_row(row) for row in rows)

    def finish_execution(
        self,
        execution_id: str,
        *,
        status: str,
        output_artifact_ids: Iterable[str] = (),
        verifier_artifact_id: str | None = None,
        receipt: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ExecutionRecord:
        if status not in EXECUTION_STATUSES or status == "running":
            raise ValueError(f"Unsupported terminal execution status: {status}")
        current = self.get_execution(execution_id)
        if current.status != "running":
            raise InvalidTransitionError(f"Execution is already terminal: {execution_id}")
        output_ids = tuple(dict.fromkeys(output_artifact_ids))
        self._validate_artifacts_for_task(current.task_id, output_ids)
        if verifier_artifact_id is not None:
            self._validate_artifacts_for_task(current.task_id, (verifier_artifact_id,))
        step_status = {"pass":"pass","rework":"rework","abstain":"blocked","fail":"failed","blocked":"blocked"}[status]
        with self._db() as db:
            cursor = db.execute(
                "UPDATE task_executions SET status=?,output_artifact_ids_json=?,verifier_artifact_id=?,receipt_json=?,metrics_json=?,error=?,ended_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
                (status, _json_dump(output_ids), verifier_artifact_id, _json_dump(receipt or {}), _json_dump(metrics or {}), error, execution_id),
            )
            if cursor.rowcount != 1:
                raise InvalidTransitionError(f"Execution is already terminal: {execution_id}")
            db.execute("UPDATE task_steps SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (step_status, current.step_id))
        return self.get_execution(execution_id)

    def dependency_output_artifact_ids(self, step_id: str) -> tuple[str, ...]:
        step = self.get_step(step_id)
        result: list[str] = list(step.input_artifact_ids)
        for dependency_id in step.dependencies:
            executions = self.list_executions(step.task_id, step_id=dependency_id)
            if not executions:
                continue
            accepted = [execution for execution in executions if execution.status == "pass"]
            if accepted:
                result.extend(accepted[-1].output_artifact_ids)
        return tuple(dict.fromkeys(result))

    def add_claim(
        self,
        task_id: str,
        *,
        kind: str,
        subject: str,
        value: Any,
        evidence_artifact_ids: Iterable[str] = (),
        step_id: str | None = None,
        confidence: float | None = None,
        claim_id: str | None = None,
    ) -> ClaimRecord:
        self.get_task(task_id)
        if kind not in CLAIM_KINDS:
            raise ValueError(f"Unsupported claim kind: {kind}")
        subject = subject.strip()
        if not subject:
            raise ValueError("Claim subject must not be empty.")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("Claim confidence must be between 0 and 1.")
        if step_id is not None and self.get_step(step_id).task_id != task_id:
            raise ValueError("Claim step must belong to the same task.")
        evidence_ids = tuple(dict.fromkeys(evidence_artifact_ids))
        self._validate_artifacts_for_task(task_id, evidence_ids)
        if kind in {"observed", "retrieved", "calculated", "executed"} and not evidence_ids:
            raise ValueError(f"{kind} claims require evidence artifacts.")
        claim_id = claim_id or _new_id("claim")
        with self._db() as db:
            db.execute(
                "INSERT INTO task_claims (id,task_id,step_id,kind,subject,value_json,evidence_artifact_ids_json,confidence) VALUES (?,?,?,?,?,?,?,?)",
                (claim_id, task_id, step_id, kind, subject, _json_dump(value), _json_dump(evidence_ids), confidence),
            )
        return self.get_claim(claim_id)

    def get_claim(self, claim_id: str) -> ClaimRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM task_claims WHERE id=?", (claim_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown claim: {claim_id}")
        return self._claim_from_row(row)

    def list_claims(self, task_id: str) -> tuple[ClaimRecord, ...]:
        self.get_task(task_id)
        with self._db() as db:
            rows = db.execute("SELECT * FROM task_claims WHERE task_id=? ORDER BY created_at,id", (task_id,)).fetchall()
        return tuple(self._claim_from_row(row) for row in rows)

    def request_approval(
        self,
        task_id: str,
        *,
        required_authority: str,
        requested_action: str,
        step_id: str | None = None,
        approval_id: str | None = None,
    ) -> ApprovalRecord:
        self.get_task(task_id)
        required_authority = validate_authority(required_authority)
        requested_action = requested_action.strip()
        if not requested_action:
            raise ValueError("Requested action must not be empty.")
        if step_id is not None and self.get_step(step_id).task_id != task_id:
            raise ValueError("Approval step must belong to the same task.")
        approval_id = approval_id or _new_id("approval")
        with self._db() as db:
            db.execute(
                "INSERT INTO task_approvals (id,task_id,step_id,required_authority,requested_action,status) VALUES (?,?,?,?,?,'pending')",
                (approval_id, task_id, step_id, required_authority, requested_action),
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM task_approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown approval: {approval_id}")
        return self._approval_from_row(row)

    def list_approvals(self, task_id: str, *, status: str | None = None) -> tuple[ApprovalRecord, ...]:
        self.get_task(task_id)
        if status is not None and status not in APPROVAL_STATUSES:
            raise ValueError(f"Unsupported approval status: {status}")
        with self._db() as db:
            if status is None:
                rows = db.execute("SELECT * FROM task_approvals WHERE task_id=? ORDER BY created_at,id", (task_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM task_approvals WHERE task_id=? AND status=? ORDER BY created_at,id", (task_id, status)).fetchall()
        return tuple(self._approval_from_row(row) for row in rows)

    def decide_approval(self, approval_id: str, *, status: str, note: str | None = None) -> ApprovalRecord:
        if status not in {"approved", "denied", "cancelled"}:
            raise ValueError("Approval decision must be approved, denied, or cancelled.")
        current = self.get_approval(approval_id)
        if current.status != "pending":
            raise InvalidTransitionError("Approval is already terminal.")
        with self._db() as db:
            db.execute("UPDATE task_approvals SET status=?,decision_note=?,decided_at=CURRENT_TIMESTAMP WHERE id=?", (status, note, approval_id))
        return self.get_approval(approval_id)

    def append_event(
        self,
        task_id: str,
        *,
        name: str,
        payload: dict[str, Any] | None = None,
        step_id: str | None = None,
        execution_id: str | None = None,
    ) -> EventRecord:
        self.get_task(task_id)
        if step_id is not None and self.get_step(step_id).task_id != task_id:
            raise ValueError("Event step must belong to task.")
        if execution_id is not None and self.get_execution(execution_id).task_id != task_id:
            raise ValueError("Event execution must belong to task.")
        name = name.strip()
        if not name:
            raise ValueError("Event name must not be empty.")
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO task_events (task_id,step_id,execution_id,name,payload_json) VALUES (?,?,?,?,?)",
                (task_id, step_id, execution_id, name, _json_dump(payload or {})),
            )
            row = db.execute("SELECT * FROM task_events WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._event_from_row(row)

    def list_events(self, task_id: str) -> tuple[EventRecord, ...]:
        self.get_task(task_id)
        with self._db() as db:
            rows = db.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def create_checkpoint(self, task_id: str, *, reason: str, checkpoint_id: str | None = None) -> CheckpointRecord:
        reason = reason.strip()
        if not reason:
            raise ValueError("Checkpoint reason must not be empty.")
        snapshot = self.snapshot(task_id, include_artifact_payloads=False)
        checkpoint_id = checkpoint_id or _new_id("checkpoint")
        with self._db() as db:
            db.execute("INSERT INTO task_checkpoints (id,task_id,reason,snapshot_json) VALUES (?,?,?,?)", (checkpoint_id, task_id, reason, _json_dump(snapshot)))
        return self.get_checkpoint(checkpoint_id)

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM task_checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown checkpoint: {checkpoint_id}")
        return CheckpointRecord(row["id"], row["task_id"], row["reason"], _json_load(row["snapshot_json"], {}), row["created_at"])

    def latest_checkpoint(self, task_id: str) -> CheckpointRecord | None:
        self.get_task(task_id)
        with self._db() as db:
            row = db.execute("SELECT id FROM task_checkpoints WHERE task_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (task_id,)).fetchone()
        return None if row is None else self.get_checkpoint(row["id"])

    def snapshot(self, task_id: str, *, include_artifact_payloads: bool = False) -> dict[str, Any]:
        task = self.get_task(task_id)
        artifacts = self.list_artifacts(task_id)
        artifact_rows = []
        for artifact in artifacts:
            item = {
                "id": artifact.id,
                "task_id": artifact.task_id,
                "step_id": artifact.step_id,
                "kind": artifact.kind,
                "sha256": artifact.sha256,
                "metadata": artifact.metadata,
                "created_at": artifact.created_at,
            }
            if include_artifact_payloads:
                item["payload"] = artifact.payload
            artifact_rows.append(item)
        return {
            "task": asdict(task),
            "criteria": [asdict(x) for x in self.list_criteria(task_id)],
            "steps": [asdict(x) for x in self.list_steps(task_id)],
            "executions": [asdict(x) for x in self.list_executions(task_id)],
            "artifacts": artifact_rows,
            "claims": [asdict(x) for x in self.list_claims(task_id)],
            "approvals": [asdict(x) for x in self.list_approvals(task_id)],
        }

    def _validate_artifacts_for_task(self, task_id: str, artifact_ids: Iterable[str]) -> None:
        for artifact_id in artifact_ids:
            if self.get_artifact(artifact_id).task_id != task_id:
                raise ValueError("Artifact must belong to the same task.")

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(row["id"], row["objective"], tuple(_json_load(row["success_criteria_json"], [])), tuple(_json_load(row["constraints_json"], [])), row["authority_scope"], row["status"], _json_load(row["metadata_json"], {}), row["created_at"], row["updated_at"])

    @staticmethod
    def _criterion_from_row(row: sqlite3.Row) -> CriterionRecord:
        return CriterionRecord(row["id"], row["task_id"], int(row["ordinal"]), row["text"], row["status"], tuple(_json_load(row["evidence_artifact_ids_json"], [])), row["note"], row["updated_at"])

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> StepRecord:
        return StepRecord(row["id"], row["task_id"], int(row["ordinal"]), row["description"], row["capability"], row["status"], tuple(_json_load(row["dependencies_json"], [])), tuple(_json_load(row["input_artifact_ids_json"], [])), _json_load(row["metadata_json"], {}), row["created_at"], row["updated_at"])

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(row["id"], row["task_id"], row["step_id"], row["kind"], _json_load(row["payload_json"], None), row["sha256"], _json_load(row["metadata_json"], {}), row["created_at"])

    @staticmethod
    def _execution_from_row(row: sqlite3.Row) -> ExecutionRecord:
        return ExecutionRecord(row["id"], row["task_id"], row["step_id"], row["capability"], row["provider"], int(row["attempt"]), row["status"], tuple(_json_load(row["input_artifact_ids_json"], [])), tuple(_json_load(row["output_artifact_ids_json"], [])), row["verifier_artifact_id"], _json_load(row["receipt_json"], {}), _json_load(row["metrics_json"], {}), row["error"], row["started_at"], row["ended_at"])

    @staticmethod
    def _claim_from_row(row: sqlite3.Row) -> ClaimRecord:
        return ClaimRecord(row["id"], row["task_id"], row["step_id"], row["kind"], row["subject"], _json_load(row["value_json"], None), tuple(_json_load(row["evidence_artifact_ids_json"], [])), row["confidence"], row["created_at"])

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(row["id"], row["task_id"], row["step_id"], row["required_authority"], row["requested_action"], row["status"], row["decision_note"], row["created_at"], row["decided_at"])

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventRecord:
        return EventRecord(int(row["id"]), row["task_id"], row["step_id"], row["execution_id"], row["name"], _json_load(row["payload_json"], {}), row["created_at"])
