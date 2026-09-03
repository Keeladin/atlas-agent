from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

from atlas_core.evidence import EvidenceStore
from atlas_core.policy import OwnerPolicy

from .models import ActionOccurrence, ActionRequest, ActionResult
from .store import ActionStore

Executor = Callable[[dict], ActionResult]
ExecutorResolver = Callable[[str, str | None, str | None, str | None, str | None], Executor]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActionRuntime:
    """Canonical execution gate for every consequential principal action."""

    def __init__(self, *, policy: OwnerPolicy, store: ActionStore, evidence: EvidenceStore, executor_resolver: ExecutorResolver) -> None:
        self.policy = policy
        self.store = store
        self.evidence = evidence
        self.executor_resolver = executor_resolver

    def submit(self, request: ActionRequest) -> ActionOccurrence:
        resolution = self.policy.resolve(
            principal_id=request.provenance.principal_id, scope=request.scope, operation=request.operation,
        )
        if resolution.decision != "YES":
            occurrence = self.store.create(
                request, decision="NO", revision=resolution.revision, event_id=resolution.event_id, status="blocked",
            )
            self.evidence.add(occurrence.occurrence_id, "policy", {**resolution.as_dict(), "outcome": "blocked"})
            return occurrence
        occurrence = self.store.create(
            request, decision="YES", revision=resolution.revision, event_id=resolution.event_id, status="executing",
        )
        self.evidence.add(occurrence.occurrence_id, "policy", {**resolution.as_dict(), "outcome": "execute"})
        return self._execute(occurrence)

    def _execute(self, occurrence: ActionOccurrence) -> ActionOccurrence:
        if occurrence.executed_at is None:
            occurrence = self.store.transition(
                occurrence.occurrence_id, from_status=("executing",), to_status="executing", executed_at=_iso(),
            )
        try:
            executor = self.executor_resolver(
                occurrence.capability_id, occurrence.principal_id, occurrence.surface, occurrence.work_id, occurrence.step_id,
            )
            result = executor(dict(occurrence.payload))
        except Exception as exc:
            result = ActionResult(False, error_code="executor_exception", error=str(exc), receipt={"ok": False})
        self.evidence.add(
            occurrence.occurrence_id, "execution_receipt",
            {"ok": result.ok, "receipt": result.receipt, "error_code": result.error_code, "error": result.error},
        )
        if result.ok and result.receipt.get("verification_pending") is True:
            return self.store.transition(
                occurrence.occurrence_id, from_status=("executing",), to_status="uncertain",
                result_json=json.dumps(result.output, default=str, ensure_ascii=False),
                receipt_json=json.dumps(result.receipt, default=str, ensure_ascii=False),
            )
        if result.ok:
            return self.store.transition(
                occurrence.occurrence_id, from_status=("executing",), to_status="succeeded",
                result_json=json.dumps(result.output, default=str, ensure_ascii=False),
                receipt_json=json.dumps(result.receipt, default=str, ensure_ascii=False), completed_at=_iso(),
            )
        return self.store.transition(
            occurrence.occurrence_id, from_status=("executing",), to_status="failed",
            result_json=json.dumps(result.output, default=str, ensure_ascii=False),
            receipt_json=json.dumps(result.receipt, default=str, ensure_ascii=False),
            error_code=result.error_code or "execution_failed", error=result.error or "execution failed", completed_at=_iso(),
        )
