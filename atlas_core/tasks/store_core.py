from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any, Iterable

from atlas_core.authority import validate_authority

from .models import *
from .store_common import (InvalidTransitionError, TaskStoreError, UnknownRecordError, _new_id, _json_dump, _json_load, _payload_hash, _TASK_TRANSITIONS, _STEP_TRANSITIONS)

class TaskStoreCoreMixin:
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

    _TASK_OWNED_TABLES = (
        "task_criteria",
        "task_steps",
        "task_artifacts",
        "task_executions",
        "task_context_manifests",
        "task_checkpoints",
        "task_claims",
        "task_approvals",
        "task_events",
        "tasks",
    )

    def delete_task(self, task_id: str) -> None:
        self.get_task(task_id)
        with self._db() as db:
            cursor = db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            if cursor.rowcount != 1:
                raise UnknownRecordError(f"Unknown task: {task_id}")
            leftover = []
            for table in self._TASK_OWNED_TABLES:
                if table == "tasks":
                    row = db.execute("SELECT COUNT(*) AS n FROM tasks WHERE id=?", (task_id,)).fetchone()
                else:
                    row = db.execute(
                        f"SELECT COUNT(*) AS n FROM {table} WHERE task_id=?",
                        (task_id,),
                    ).fetchone()
                if int(row["n"]):
                    leftover.append(table)
            if leftover:
                raise TaskStoreError(
                    f"Task {task_id} still has rows after delete: {leftover}"
                )

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
        if status == "accepted" and not evidence_ids:
            raise ValueError("Accepted success criteria require evidence artifacts.")
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
        capability_version: str | None = None,
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
                "INSERT INTO task_steps (id,task_id,ordinal,description,capability,capability_version,status,dependencies_json,input_artifact_ids_json,metadata_json) VALUES (?,?,?,?,?,?,'pending',?,?,?)",
                (step_id, task_id, ordinal, description, capability.strip() if capability else None, capability_version.strip() if capability_version else None, _json_dump(dep_ids), _json_dump(input_ids), _json_dump(metadata or {})),
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

    def set_step_input_artifact_ids(self, step_id: str, input_artifact_ids: Iterable[str]) -> StepRecord:
        current = self.get_step(step_id)
        if current.status not in {"pending", "rework"}:
            raise InvalidTransitionError("Cannot attach input artifacts after a step has started.")
        if current.input_artifact_ids:
            raise InvalidTransitionError("Step already has input artifacts.")
        input_ids = tuple(dict.fromkeys(input_artifact_ids))
        self._validate_artifacts_for_task(current.task_id, input_ids)
        with self._db() as db:
            db.execute(
                "UPDATE task_steps SET input_artifact_ids_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (_json_dump(input_ids), step_id),
            )
        return self.get_step(step_id)

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
