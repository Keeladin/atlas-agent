from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable

from atlas_core.evidence import EvidenceStore
from atlas_core.policy import OwnerPolicy

from .models import ActionOccurrence, ActionRequest, ActionResult
from .store import ActionStore

Executor = Callable[[dict], ActionResult]
ExecutorResolver = Callable[[str], Executor]
CONFIRM_MAX_AGE = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


class ActionRuntime:
    """Canonical execution gate for every consequential Atlas action."""

    def __init__(self, *, policy: OwnerPolicy, store: ActionStore, evidence: EvidenceStore, executor_resolver: ExecutorResolver) -> None:
        self.policy=policy; self.store=store; self.evidence=evidence; self.executor_resolver=executor_resolver

    def submit(self, request: ActionRequest) -> ActionOccurrence:
        resolution=self.policy.resolve(principal_id=request.provenance.principal_id, scope=request.scope, operation=request.operation)
        if resolution.decision == "NO":
            occurrence=self.store.create(request, decision="NO", revision=resolution.revision, event_id=resolution.event_id, status="blocked")
            self.evidence.add(occurrence.occurrence_id, "policy", {**resolution.as_dict(), "outcome":"blocked"})
            return occurrence
        if resolution.decision == "CONFIRM":
            occurrence=self.store.create(request, decision="CONFIRM", revision=resolution.revision, event_id=resolution.event_id, status="pending_confirmation")
            self.evidence.add(occurrence.occurrence_id, "policy", {**resolution.as_dict(), "outcome":"confirmation_required"})
            return occurrence
        occurrence=self.store.create(request, decision="YES", revision=resolution.revision, event_id=resolution.event_id, status="executing")
        self.evidence.add(occurrence.occurrence_id, "policy", {**resolution.as_dict(), "outcome":"execute"})
        return self._execute(occurrence)

    def confirm(self, occurrence_id: str, *, principal_id: str) -> ActionOccurrence:
        occurrence=self.store.get(occurrence_id)
        if occurrence.principal_id != principal_id: raise PermissionError("confirmation principal mismatch")
        if occurrence.status != "pending_confirmation": raise ValueError("action is not pending confirmation")
        created=datetime.fromisoformat(occurrence.created_at.replace("Z", "+00:00"))
        if created.tzinfo is None: created=created.replace(tzinfo=timezone.utc)
        if _now() - created > CONFIRM_MAX_AGE:
            return self.store.transition(occurrence_id, from_status=("pending_confirmation",), to_status="expired", completed_at=_iso(), error_code="confirmation_expired", error="confirmation window expired")
        resolution=self.policy.resolve(principal_id=principal_id, scope=occurrence.scope, operation=occurrence.operation)
        self.evidence.add(occurrence_id, "policy_recheck", resolution.as_dict())
        if resolution.decision == "NO":
            return self.store.transition(occurrence_id, from_status=("pending_confirmation",), to_status="blocked", policy_decision="NO", policy_revision=resolution.revision, policy_event_id=resolution.event_id, completed_at=_iso(), error_code="policy_revoked_before_execution", error="current runtime policy is NO")
        occurrence=self.store.transition(occurrence_id, from_status=("pending_confirmation",), to_status="executing", policy_decision=resolution.decision, policy_revision=resolution.revision, policy_event_id=resolution.event_id, confirmed_at=_iso(), executed_at=_iso())
        return self._execute(occurrence)

    def cancel(self, occurrence_id: str, *, principal_id: str) -> ActionOccurrence:
        occurrence=self.store.get(occurrence_id)
        if occurrence.principal_id != principal_id: raise PermissionError("confirmation principal mismatch")
        return self.store.transition(occurrence_id, from_status=("pending_confirmation",), to_status="cancelled", completed_at=_iso())

    def _execute(self, occurrence: ActionOccurrence) -> ActionOccurrence:
        if occurrence.executed_at is None:
            occurrence=self.store.transition(occurrence.occurrence_id, from_status=("executing",), to_status="executing", executed_at=_iso())
        try:
            executor=self.executor_resolver(occurrence.capability_id)
            result=executor(dict(occurrence.payload))
        except Exception as exc:
            result=ActionResult(False, error_code="executor_exception", error=str(exc), receipt={"ok":False})
        self.evidence.add(occurrence.occurrence_id, "execution_receipt", {"ok":result.ok, "receipt":result.receipt, "error_code":result.error_code, "error":result.error})
        if result.ok and result.receipt.get("verification_pending") is True:
            return self.store.transition(occurrence.occurrence_id, from_status=("executing",), to_status="uncertain", result_json=json.dumps(result.output, default=str, ensure_ascii=False), receipt_json=json.dumps(result.receipt, default=str, ensure_ascii=False))
        if result.ok:
            return self.store.transition(occurrence.occurrence_id, from_status=("executing",), to_status="succeeded", result_json=json.dumps(result.output, default=str, ensure_ascii=False), receipt_json=json.dumps(result.receipt, default=str, ensure_ascii=False), completed_at=_iso())
        return self.store.transition(occurrence.occurrence_id, from_status=("executing",), to_status="failed", result_json=json.dumps(result.output, default=str, ensure_ascii=False), receipt_json=json.dumps(result.receipt, default=str, ensure_ascii=False), error_code=result.error_code or "execution_failed", error=result.error or "execution failed", completed_at=_iso())
