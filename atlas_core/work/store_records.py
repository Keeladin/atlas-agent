from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any, Iterable

from atlas_core.authority import validate_authority

from .records import *
from .store_common import (InvalidTransitionError, UnknownRecordError, _new_id, _json_dump, _json_load, _payload_hash, _WORK_TRANSITIONS, _STEP_TRANSITIONS)

class WorkStoreRecordsMixin:
    def add_claim(
        self,
        work_id: str,
        *,
        kind: str,
        subject: str,
        value: Any,
        evidence_artifact_ids: Iterable[str] = (),
        step_id: str | None = None,
        confidence: float | None = None,
        claim_id: str | None = None,
    ) -> ClaimRecord:
        self.get_work(work_id)
        if kind not in CLAIM_KINDS:
            raise ValueError(f"Unsupported claim kind: {kind}")
        subject = subject.strip()
        if not subject:
            raise ValueError("Claim subject must not be empty.")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("Claim confidence must be between 0 and 1.")
        if step_id is not None and self.get_step(step_id).work_id != work_id:
            raise ValueError("Claim step must belong to the same work.")
        evidence_ids = tuple(dict.fromkeys(evidence_artifact_ids))
        self._validate_artifacts_for_work(work_id, evidence_ids)
        if kind in {"observed", "retrieved", "calculated", "executed"} and not evidence_ids:
            raise ValueError(f"{kind} claims require evidence artifacts.")
        claim_id = claim_id or _new_id("claim")
        with self._db() as db:
            db.execute(
                "INSERT INTO work_claims (id,work_id,step_id,kind,subject,value_json,evidence_artifact_ids_json,confidence) VALUES (?,?,?,?,?,?,?,?)",
                (claim_id, work_id, step_id, kind, subject, _json_dump(value), _json_dump(evidence_ids), confidence),
            )
        return self.get_claim(claim_id)

    def get_claim(self, claim_id: str) -> ClaimRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM work_claims WHERE id=?", (claim_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown claim: {claim_id}")
        return self._claim_from_row(row)

    def list_claims(self, work_id: str) -> tuple[ClaimRecord, ...]:
        self.get_work(work_id)
        with self._db() as db:
            rows = db.execute("SELECT * FROM work_claims WHERE work_id=? ORDER BY created_at,id", (work_id,)).fetchall()
        return tuple(self._claim_from_row(row) for row in rows)

    def request_approval(
        self,
        work_id: str,
        *,
        required_authority: str,
        requested_action: str,
        step_id: str | None = None,
        approval_id: str | None = None,
    ) -> ApprovalRecord:
        self.get_work(work_id)
        required_authority = validate_authority(required_authority)
        requested_action = requested_action.strip()
        if not requested_action:
            raise ValueError("Requested action must not be empty.")
        if step_id is not None and self.get_step(step_id).work_id != work_id:
            raise ValueError("Approval step must belong to the same work.")
        approval_id = approval_id or _new_id("approval")
        with self._db() as db:
            db.execute(
                "INSERT INTO work_approvals (id,work_id,step_id,required_authority,requested_action,status) VALUES (?,?,?,?,?,'pending')",
                (approval_id, work_id, step_id, required_authority, requested_action),
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM work_approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown approval: {approval_id}")
        return self._approval_from_row(row)

    def list_approvals(self, work_id: str, *, status: str | None = None) -> tuple[ApprovalRecord, ...]:
        self.get_work(work_id)
        if status is not None and status not in APPROVAL_STATUSES:
            raise ValueError(f"Unsupported approval status: {status}")
        with self._db() as db:
            if status is None:
                rows = db.execute("SELECT * FROM work_approvals WHERE work_id=? ORDER BY created_at,id", (work_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM work_approvals WHERE work_id=? AND status=? ORDER BY created_at,id", (work_id, status)).fetchall()
        return tuple(self._approval_from_row(row) for row in rows)

    def decide_approval(self, approval_id: str, *, status: str, note: str | None = None) -> ApprovalRecord:
        if status not in {"approved", "denied", "cancelled"}:
            raise ValueError("Approval decision must be approved, denied, or cancelled.")
        current = self.get_approval(approval_id)
        if current.status != "pending":
            raise InvalidTransitionError("Approval is already terminal.")
        with self._db() as db:
            db.execute("UPDATE work_approvals SET status=?,decision_note=?,decided_at=CURRENT_TIMESTAMP WHERE id=?", (status, note, approval_id))
        return self.get_approval(approval_id)

    def append_event(
        self,
        work_id: str,
        *,
        name: str,
        payload: dict[str, Any] | None = None,
        step_id: str | None = None,
        execution_id: str | None = None,
    ) -> EventRecord:
        self.get_work(work_id)
        if step_id is not None and self.get_step(step_id).work_id != work_id:
            raise ValueError("Event step must belong to the same work.")
        if execution_id is not None and self.get_execution(execution_id).work_id != work_id:
            raise ValueError("Event execution must belong to the same work.")
        name = name.strip()
        if not name:
            raise ValueError("Event name must not be empty.")
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO work_events (work_id,step_id,execution_id,name,payload_json) VALUES (?,?,?,?,?)",
                (work_id, step_id, execution_id, name, _json_dump(payload or {})),
            )
            row = db.execute("SELECT * FROM work_events WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._event_from_row(row)

    def list_events(self, work_id: str) -> tuple[EventRecord, ...]:
        self.get_work(work_id)
        with self._db() as db:
            rows = db.execute("SELECT * FROM work_events WHERE work_id=? ORDER BY id", (work_id,)).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def write_context_manifest(
        self,
        work_id: str,
        *,
        step_id: str,
        execution_id: str,
        capability: str,
        capability_version: str,
        assembler_version: str,
        budget_tokens: int,
        total_tokens: int,
        manifest: dict[str, Any],
        manifest_id: str | None = None,
    ) -> ContextManifestRecord:
        self.get_work(work_id)
        if self.get_step(step_id).work_id != work_id:
            raise ValueError("Context manifest step must belong to the same work.")
        execution = self.get_execution(execution_id)
        if execution.work_id != work_id or execution.step_id != step_id:
            raise ValueError("Context manifest execution must belong to the same work and step.")
        if execution.status != "running":
            raise InvalidTransitionError("Context manifest must be written before execution becomes terminal.")
        if budget_tokens < 0 or total_tokens < 0 or total_tokens > budget_tokens:
            raise ValueError("Context manifest token accounting is invalid.")
        encoded, digest = _payload_hash(manifest)
        manifest_id = manifest_id or str(manifest.get("manifest_id") or _new_id("context"))
        with self._db() as db:
            try:
                db.execute(
                    "INSERT INTO work_context_manifests "
                    "(id,work_id,step_id,execution_id,capability,capability_version,assembler_version,budget_tokens,total_tokens,manifest_json,sha256) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (manifest_id, work_id, step_id, execution_id, capability, capability_version, assembler_version, budget_tokens, total_tokens, encoded, digest),
                )
            except sqlite3.IntegrityError as exc:
                raise InvalidTransitionError(
                    "Context manifest is immutable and this execution already has one."
                ) from exc
        return self.get_context_manifest(manifest_id)

    def get_context_manifest(self, manifest_id: str) -> ContextManifestRecord:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM work_context_manifests WHERE id=?",
                (manifest_id,),
            ).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown context manifest: {manifest_id}")
        return self._context_manifest_from_row(row)

    def context_manifest_for_execution(self, execution_id: str) -> ContextManifestRecord | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM work_context_manifests WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
        return None if row is None else self._context_manifest_from_row(row)

    def list_context_manifests(
        self,
        work_id: str,
        *,
        step_id: str | None = None,
    ) -> tuple[ContextManifestRecord, ...]:
        self.get_work(work_id)
        with self._db() as db:
            if step_id is None:
                rows = db.execute(
                    "SELECT * FROM work_context_manifests WHERE work_id=? ORDER BY created_at,rowid",
                    (work_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM work_context_manifests WHERE work_id=? AND step_id=? ORDER BY created_at,rowid",
                    (work_id, step_id),
                ).fetchall()
        return tuple(self._context_manifest_from_row(row) for row in rows)

    def create_checkpoint(self, work_id: str, *, reason: str, checkpoint_id: str | None = None) -> CheckpointRecord:
        reason = reason.strip()
        if not reason:
            raise ValueError("Checkpoint reason must not be empty.")
        snapshot = self.snapshot(work_id, include_artifact_payloads=False)
        checkpoint_id = checkpoint_id or _new_id("checkpoint")
        with self._db() as db:
            db.execute("INSERT INTO work_checkpoints (id,work_id,reason,snapshot_json) VALUES (?,?,?,?)", (checkpoint_id, work_id, reason, _json_dump(snapshot)))
        return self.get_checkpoint(checkpoint_id)

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord:
        with self._db() as db:
            row = db.execute("SELECT * FROM work_checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        if row is None:
            raise UnknownRecordError(f"Unknown checkpoint: {checkpoint_id}")
        return CheckpointRecord(row["id"], row["work_id"], row["reason"], _json_load(row["snapshot_json"], {}), row["created_at"])

    def latest_checkpoint(self, work_id: str) -> CheckpointRecord | None:
        self.get_work(work_id)
        with self._db() as db:
            row = db.execute("SELECT id FROM work_checkpoints WHERE work_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (work_id,)).fetchone()
        return None if row is None else self.get_checkpoint(row["id"])

    def snapshot(self, work_id: str, *, include_artifact_payloads: bool = False) -> dict[str, Any]:
        work = self.get_work(work_id)
        artifacts = self.list_artifacts(work_id)
        artifact_rows = []
        for artifact in artifacts:
            item = {
                "id": artifact.id,
                "work_id": artifact.work_id,
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
            "work": asdict(work),
            "criteria": [asdict(x) for x in self.list_criteria(work_id)],
            "steps": [asdict(x) for x in self.list_steps(work_id)],
            "executions": [asdict(x) for x in self.list_executions(work_id)],
            "artifacts": artifact_rows,
            "claims": [asdict(x) for x in self.list_claims(work_id)],
            "approvals": [asdict(x) for x in self.list_approvals(work_id)],
            "context_manifests": [
                {
                    "id": x.id,
                    "work_id": x.work_id,
                    "step_id": x.step_id,
                    "execution_id": x.execution_id,
                    "capability": x.capability,
                    "capability_version": x.capability_version,
                    "assembler_version": x.assembler_version,
                    "budget_tokens": x.budget_tokens,
                    "total_tokens": x.total_tokens,
                    "sha256": x.sha256,
                    "created_at": x.created_at,
                }
                for x in self.list_context_manifests(work_id)
            ],
        }

    def _validate_artifacts_for_work(self, work_id: str, artifact_ids: Iterable[str]) -> None:
        for artifact_id in artifact_ids:
            if self.get_artifact(artifact_id).work_id != work_id:
                raise ValueError("Artifact must belong to the same work.")

    @staticmethod
    def _work_from_row(row: sqlite3.Row) -> WorkState:
        return WorkState(row["id"], row["objective"], tuple(_json_load(row["success_criteria_json"], [])), tuple(_json_load(row["constraints_json"], [])), row["authority_scope"], row["status"], _json_load(row["metadata_json"], {}), row["created_at"], row["updated_at"])

    @staticmethod
    def _criterion_from_row(row: sqlite3.Row) -> CriterionRecord:
        return CriterionRecord(row["id"], row["work_id"], int(row["ordinal"]), row["text"], row["status"], tuple(_json_load(row["evidence_artifact_ids_json"], [])), row["note"], row["updated_at"])

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> StepRecord:
        return StepRecord(row["id"], row["work_id"], int(row["ordinal"]), row["description"], row["capability"], row["capability_version"] if "capability_version" in row.keys() else None, row["status"], tuple(_json_load(row["dependencies_json"], [])), tuple(_json_load(row["input_artifact_ids_json"], [])), _json_load(row["metadata_json"], {}), row["created_at"], row["updated_at"])

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(row["id"], row["work_id"], row["step_id"], row["kind"], _json_load(row["payload_json"], None), row["sha256"], _json_load(row["metadata_json"], {}), row["created_at"])

    @staticmethod
    def _execution_from_row(row: sqlite3.Row) -> ExecutionRecord:
        version = row["capability_version"] if "capability_version" in row.keys() and row["capability_version"] else "1.0.0"
        return ExecutionRecord(row["id"], row["work_id"], row["step_id"], row["capability"], version, row["provider"], int(row["attempt"]), row["status"], tuple(_json_load(row["input_artifact_ids_json"], [])), tuple(_json_load(row["output_artifact_ids_json"], [])), row["verifier_artifact_id"], _json_load(row["receipt_json"], {}), _json_load(row["metrics_json"], {}), row["error"], row["started_at"], row["ended_at"])

    @staticmethod
    def _context_manifest_from_row(row: sqlite3.Row) -> ContextManifestRecord:
        return ContextManifestRecord(
            row["id"], row["work_id"], row["step_id"], row["execution_id"],
            row["capability"], row["capability_version"], row["assembler_version"],
            int(row["budget_tokens"]), int(row["total_tokens"]),
            _json_load(row["manifest_json"], {}), row["sha256"], row["created_at"],
        )

    @staticmethod
    def _claim_from_row(row: sqlite3.Row) -> ClaimRecord:
        return ClaimRecord(row["id"], row["work_id"], row["step_id"], row["kind"], row["subject"], _json_load(row["value_json"], None), tuple(_json_load(row["evidence_artifact_ids_json"], [])), row["confidence"], row["created_at"])

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(row["id"], row["work_id"], row["step_id"], row["required_authority"], row["requested_action"], row["status"], row["decision_note"], row["created_at"], row["decided_at"])

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventRecord:
        return EventRecord(int(row["id"]), row["work_id"], row["step_id"], row["execution_id"], row["name"], _json_load(row["payload_json"], {}), row["created_at"])
