from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any, Iterable

from .records import *
from .store_common import (InvalidTransitionError, UnknownRecordError, _new_id, _json_dump, _json_load, _payload_hash, _WORK_TRANSITIONS, _STEP_TRANSITIONS)

class WorkStoreExecutionMixin:
    def begin_execution(
        self,
        work_id: str,
        *,
        step_id: str,
        capability: str,
        capability_version: str = "1.0.0",
        provider: str | None = None,
        input_artifact_ids: Iterable[str] = (),
        execution_id: str | None = None,
    ) -> ExecutionRecord:
        capability = capability.strip()
        if not capability:
            raise ValueError("Execution capability must not be empty.")
        input_ids = tuple(dict.fromkeys(input_artifact_ids))
        self._validate_artifacts_for_work(work_id, input_ids)
        execution_id = execution_id or _new_id("execution")

        with self._db() as db:
            work_row = db.execute("SELECT status FROM work WHERE id=?", (work_id,)).fetchone()
            if work_row is None:
                raise UnknownRecordError(f"Unknown work: {work_id}")
            if work_row["status"] not in {"planned", "active", "waiting"}:
                raise InvalidTransitionError(f"Cannot execute terminal work {work_id}.")
            step_row = db.execute("SELECT work_id,status FROM work_steps WHERE id=?", (step_id,)).fetchone()
            if step_row is None:
                raise UnknownRecordError(f"Unknown step: {step_id}")
            if step_row["work_id"] != work_id:
                raise ValueError("Execution step must belong to the same work.")
            if step_row["status"] not in {"pending", "rework", "blocked"}:
                raise InvalidTransitionError(f"Step is not executable from status {step_row['status']}.")
            claim = db.execute(
                "UPDATE work_steps SET status='running',updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status IN ('pending','rework','blocked')",
                (step_id,),
            )
            if claim.rowcount != 1:
                raise InvalidTransitionError(f"Step was claimed by another runtime: {step_id}")
            row = db.execute(
                "SELECT COALESCE(MAX(attempt),0)+1 AS attempt FROM work_executions WHERE step_id=?",
                (step_id,),
            ).fetchone()
            attempt = int(row["attempt"])
            db.execute(
                "INSERT INTO work_executions "
                "(id,work_id,step_id,capability,capability_version,provider,attempt,status,input_artifact_ids_json) "
                "VALUES (?,?,?,?,?,?,?,'running',?)",
                (execution_id, work_id, step_id, capability, capability_version, provider.strip() if provider else None, attempt, _json_dump(input_ids)),
            )
        return self.get_execution(execution_id)

    def set_execution_provider(self, execution_id: str, provider: str) -> ExecutionRecord:
        provider = provider.strip()
        if not provider:
            raise ValueError("Execution provider must not be empty.")
        current = self.get_execution(execution_id)
        if current.status != "running":
            raise InvalidTransitionError("Only running executions may receive a provider.")
        if current.provider is not None and current.provider != provider:
            raise InvalidTransitionError("Execution provider is already fixed.")
        with self._db() as db:
            db.execute(
                "UPDATE work_executions SET provider=? WHERE id=? AND status='running'",
                (provider, execution_id),
            )
        return self.get_execution(execution_id)

    def get_execution(self, execution_id: str) -> ExecutionRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM work_executions WHERE id=?", (execution_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown execution: {execution_id}")
        return self._execution_from_row(row)

    def list_executions(self, work_id: str, *, step_id: str | None = None) -> tuple[ExecutionRecord, ...]:
        self.get_work(work_id)
        with self._db() as db:
            if step_id is None:
                rows = db.execute("SELECT * FROM work_executions WHERE work_id=? ORDER BY rowid", (work_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM work_executions WHERE work_id=? AND step_id=? ORDER BY attempt", (work_id, step_id)).fetchall()
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
        self._validate_artifacts_for_work(current.work_id, output_ids)
        if verifier_artifact_id is not None:
            self._validate_artifacts_for_work(current.work_id, (verifier_artifact_id,))
        step_status = {"pass":"pass","rework":"rework","abstain":"blocked","fail":"failed","blocked":"blocked"}[status]
        with self._db() as db:
            cursor = db.execute(
                "UPDATE work_executions SET status=?,output_artifact_ids_json=?,verifier_artifact_id=?,receipt_json=?,metrics_json=?,error=?,ended_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
                (status, _json_dump(output_ids), verifier_artifact_id, _json_dump(receipt or {}), _json_dump(metrics or {}), error, execution_id),
            )
            if cursor.rowcount != 1:
                raise InvalidTransitionError(f"Execution is already terminal: {execution_id}")
            db.execute("UPDATE work_steps SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (step_status, current.step_id))
        return self.get_execution(execution_id)

    def dependency_output_artifact_ids(self, step_id: str) -> tuple[str, ...]:
        step = self.get_step(step_id)
        result: list[str] = list(step.input_artifact_ids)
        for dependency_id in step.dependencies:
            executions = self.list_executions(step.work_id, step_id=dependency_id)
            if not executions:
                continue
            accepted = [execution for execution in executions if execution.status == "pass"]
            if accepted:
                result.extend(accepted[-1].output_artifact_ids)
        return tuple(dict.fromkeys(result))
