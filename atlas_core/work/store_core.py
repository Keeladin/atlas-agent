from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict
from typing import Any, Iterable

from atlas_core.authority import validate_authority

from .records import *
from .store_common import (InvalidTransitionError, WorkStoreError, UnknownRecordError, _new_id, _json_dump, _json_load, _payload_hash, _WORK_TRANSITIONS, _STEP_TRANSITIONS)

class WorkStoreCoreMixin:
    def create_work(
        self,
        *,
        objective: str,
        success_criteria: Iterable[str],
        constraints: Iterable[str] = (),
        authority_scope: str = "read",
        status: str = "planned",
        metadata: dict[str, Any] | None = None,
        work_id: str | None = None,
    ) -> WorkState:
        objective = objective.strip()
        if not objective:
            raise ValueError("Work objective must not be empty.")
        criteria = tuple(x.strip() for x in success_criteria if x.strip())
        if not criteria:
            raise ValueError("Work must have at least one success criterion.")
        constraints_tuple = tuple(x.strip() for x in constraints if x.strip())
        if status not in WORK_STATUSES:
            raise ValueError(f"Unsupported work status: {status}")
        authority_scope = validate_authority(authority_scope)
        work_id = work_id or _new_id("work")
        with self._db() as db:
            db.execute(
                "INSERT INTO work (id,objective,success_criteria_json,constraints_json,authority_scope,status,metadata_json) VALUES (?,?,?,?,?,?,?)",
                (work_id, objective, _json_dump(criteria), _json_dump(constraints_tuple), authority_scope, status, _json_dump(metadata or {})),
            )
            for ordinal, text in enumerate(criteria, start=1):
                db.execute(
                    "INSERT INTO work_criteria (id,work_id,ordinal,text,status) VALUES (?,?,?,?, 'pending')",
                    (_new_id("criterion"), work_id, ordinal, text),
                )
        return self.get_work(work_id)

    def get_work(self, work_id: str) -> WorkState:
        with self._db() as db:
            row = db.execute("SELECT * FROM work WHERE id=?", (work_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown work: {work_id}")
        return self._work_from_row(row)

    def list_work(self, *, status: str | None = None) -> tuple[WorkState, ...]:
        with self._db() as db:
            if status is None:
                rows = db.execute("SELECT * FROM work ORDER BY created_at,id").fetchall()
            else:
                if status not in WORK_STATUSES:
                    raise ValueError(f"Unsupported work status: {status}")
                rows = db.execute("SELECT * FROM work WHERE status=? ORDER BY created_at,id", (status,)).fetchall()
        return tuple(self._work_from_row(row) for row in rows)

    def set_work_status(self, work_id: str, status: str, *, force: bool = False) -> WorkState:
        if status not in WORK_STATUSES:
            raise ValueError(f"Unsupported work status: {status}")
        current = self.get_work(work_id)
        if current.status == status:
            return current
        if not force and status not in _WORK_TRANSITIONS[current.status]:
            raise InvalidTransitionError(f"Work transition {current.status} -> {status} is not allowed.")
        with self._db() as db:
            db.execute("UPDATE work SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, work_id))
        return self.get_work(work_id)

    _WORK_OWNED_TABLES = (
        "work_criteria",
        "work_steps",
        "work_artifacts",
        "work_executions",
        "work_context_manifests",
        "work_checkpoints",
        "work_claims",
        "work_approvals",
        "work_confirmations",
        "work_events",
        "work_contracts",
        "work",
    )

    def delete_work(self, work_id: str) -> None:
        self.get_work(work_id)
        with self._db() as db:
            cursor = db.execute("DELETE FROM work WHERE id=?", (work_id,))
            if cursor.rowcount != 1:
                raise UnknownRecordError(f"Unknown work: {work_id}")
            leftover = []
            for table in self._WORK_OWNED_TABLES:
                if table == "work":
                    row = db.execute("SELECT COUNT(*) AS n FROM work WHERE id=?", (work_id,)).fetchone()
                else:
                    row = db.execute(
                        f"SELECT COUNT(*) AS n FROM {table} WHERE work_id=?",
                        (work_id,),
                    ).fetchone()
                if int(row["n"]):
                    leftover.append(table)
            if leftover:
                raise WorkStoreError(
                    f"Work {work_id} still has rows after delete: {leftover}"
                )

    def insert_work_contract(
        self,
        *,
        work_id: str,
        contract_id: str,
        sha256: str,
        payload: dict[str, Any],
        compiled_at: str,
    ) -> None:
        self.get_work(work_id)
        encoded, digest = _payload_hash(payload)
        if digest != sha256:
            raise WorkStoreError("Work contract digest does not match payload")
        if encoded != _json_dump(payload):
            raise WorkStoreError("Work contract payload encoding is not canonical")
        try:
            with self._db() as db:
                db.execute(
                    """
                    INSERT INTO work_contracts
                        (work_id, contract_id, sha256, payload_json, compiled_at)
                    VALUES (?,?,?,?,?)
                    """,
                    (work_id, contract_id, sha256, encoded, compiled_at),
                )
        except sqlite3.IntegrityError as exc:
            raise WorkStoreError(
                f"Work contract already exists for {work_id}"
            ) from exc

    def load_work_contract_row(self, work_id: str) -> dict[str, Any]:
        self.get_work(work_id)
        with self._db() as db:
            row = db.execute(
                "SELECT work_id, contract_id, sha256, payload_json, compiled_at "
                "FROM work_contracts WHERE work_id=?",
                (work_id,),
            ).fetchone()
        if row is None:
            raise UnknownRecordError(f"Work {work_id} has no contract")
        payload_json = str(row["payload_json"])
        digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        if digest != str(row["sha256"]):
            raise WorkStoreError("Work contract digest mismatch")
        try:
            payload = _json_load(payload_json, None)
        except (TypeError, ValueError) as exc:
            raise WorkStoreError("Work contract payload is not an object") from exc
        if not isinstance(payload, dict):
            raise WorkStoreError("Work contract payload is not an object")
        encoded, rehash = _payload_hash(payload)
        if encoded != payload_json or rehash != str(row["sha256"]):
            raise WorkStoreError("Work contract digest mismatch")
        return {
            "work_id": str(row["work_id"]),
            "contract_id": str(row["contract_id"]),
            "sha256": str(row["sha256"]),
            "payload": payload,
            "compiled_at": str(row["compiled_at"]),
        }

    def list_criteria(self, work_id: str) -> tuple[CriterionRecord, ...]:
        self.get_work(work_id)
        with self._db() as db:
            rows = db.execute("SELECT * FROM work_criteria WHERE work_id=? ORDER BY ordinal", (work_id,)).fetchall()
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
            row = db.execute("SELECT * FROM work_criteria WHERE id=?", (criterion_id,)).fetchone()
            if row is None:
                raise UnknownRecordError(f"Unknown criterion: {criterion_id}")
            work_id = row["work_id"]
        evidence_ids = tuple(dict.fromkeys(evidence_artifact_ids))
        self._validate_artifacts_for_work(work_id, evidence_ids)
        if status == "accepted" and not evidence_ids:
            raise ValueError("Accepted success criteria require evidence artifacts.")
        with self._db() as db:
            db.execute(
                "UPDATE work_criteria SET status=?,evidence_artifact_ids_json=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, _json_dump(evidence_ids), note, criterion_id),
            )
            row = db.execute("SELECT * FROM work_criteria WHERE id=?", (criterion_id,)).fetchone()
        return self._criterion_from_row(row)

    def add_step(
        self,
        work_id: str,
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
        self.get_work(work_id)
        description = description.strip()
        if not description:
            raise ValueError("Step description must not be empty.")
        dep_ids = tuple(dict.fromkeys(dependencies))
        input_ids = tuple(dict.fromkeys(input_artifact_ids))
        with self._db() as db:
            for dep_id in dep_ids:
                row = db.execute("SELECT work_id FROM work_steps WHERE id=?", (dep_id,)).fetchone()
                if row is None:
                    raise UnknownRecordError(f"Unknown dependency step: {dep_id}")
                if row["work_id"] != work_id:
                    raise ValueError("Step dependencies must belong to the same work.")
            if ordinal is None:
                row = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 AS next_ordinal FROM work_steps WHERE work_id=?", (work_id,)).fetchone()
                ordinal = int(row["next_ordinal"])
        self._validate_artifacts_for_work(work_id, input_ids)
        step_id = step_id or _new_id("step")
        with self._db() as db:
            db.execute(
                "INSERT INTO work_steps (id,work_id,ordinal,description,capability,capability_version,status,dependencies_json,input_artifact_ids_json,metadata_json) VALUES (?,?,?,?,?,?,'pending',?,?,?)",
                (step_id, work_id, ordinal, description, capability.strip() if capability else None, capability_version.strip() if capability_version else None, _json_dump(dep_ids), _json_dump(input_ids), _json_dump(metadata or {})),
            )
        return self.get_step(step_id)

    def get_step(self, step_id: str) -> StepRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM work_steps WHERE id=?", (step_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown step: {step_id}")
        return self._step_from_row(row)

    def list_steps(self, work_id: str) -> tuple[StepRecord, ...]:
        self.get_work(work_id)
        with self._db() as db:
            rows = db.execute("SELECT * FROM work_steps WHERE work_id=? ORDER BY ordinal,id", (work_id,)).fetchall()
        return tuple(self._step_from_row(row) for row in rows)

    def set_step_input_artifact_ids(self, step_id: str, input_artifact_ids: Iterable[str]) -> StepRecord:
        current = self.get_step(step_id)
        if current.status not in {"pending", "rework"}:
            raise InvalidTransitionError("Cannot attach input artifacts after a step has started.")
        if current.input_artifact_ids:
            raise InvalidTransitionError("Step already has input artifacts.")
        input_ids = tuple(dict.fromkeys(input_artifact_ids))
        self._validate_artifacts_for_work(current.work_id, input_ids)
        with self._db() as db:
            db.execute(
                "UPDATE work_steps SET input_artifact_ids_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
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
            db.execute("UPDATE work_steps SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, step_id))
        return self.get_step(step_id)

    def ready_steps(self, work_id: str) -> tuple[StepRecord, ...]:
        steps = self.list_steps(work_id)
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
        work_id: str,
        *,
        kind: str,
        payload: Any,
        step_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRecord:
        self.get_work(work_id)
        if step_id is not None and self.get_step(step_id).work_id != work_id:
            raise ValueError("Artifact step must belong to the same work.")
        kind = kind.strip()
        if not kind:
            raise ValueError("Artifact kind must not be empty.")
        encoded, digest = _payload_hash(payload)
        artifact_id = artifact_id or _new_id("artifact")
        with self._db() as db:
            db.execute(
                "INSERT INTO work_artifacts (id,work_id,step_id,kind,payload_json,sha256,metadata_json) VALUES (?,?,?,?,?,?,?)",
                (artifact_id, work_id, step_id, kind, encoded, digest, _json_dump(metadata or {})),
            )
        return self.get_artifact(artifact_id)

    def get_artifact(self, artifact_id: str) -> ArtifactRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM work_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown artifact: {artifact_id}")
        return self._artifact_from_row(row)

    def list_artifacts(self, work_id: str, *, step_id: str | None = None) -> tuple[ArtifactRecord, ...]:
        self.get_work(work_id)
        with self._db() as db:
            if step_id is None:
                rows = db.execute("SELECT * FROM work_artifacts WHERE work_id=? ORDER BY created_at,id", (work_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM work_artifacts WHERE work_id=? AND step_id=? ORDER BY created_at,id", (work_id, step_id)).fetchall()
        return tuple(self._artifact_from_row(row) for row in rows)
