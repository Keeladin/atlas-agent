from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from atlas_core.provenance import InvocationProvenance

ActionStatus = Literal["blocked", "pending_confirmation", "executing", "succeeded", "failed", "uncertain", "expired", "cancelled"]


def canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActionRequest:
    capability_id: str
    operation: str
    scope: str
    payload: dict[str, Any]
    provenance: InvocationProvenance
    work_id: str | None = None
    step_id: str | None = None
    summary: str | None = None


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    output: Any = None
    receipt: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ActionOccurrence:
    occurrence_id: str
    capability_id: str
    operation: str
    scope: str
    payload: dict[str, Any]
    payload_sha256: str
    principal_id: str
    principal_kind: str
    surface: str
    policy_decision: str
    policy_revision: int
    policy_event_id: str | None
    status: ActionStatus
    work_id: str | None
    step_id: str | None
    summary: str | None
    result: Any
    receipt: dict[str, Any]
    error_code: str | None
    error: str | None
    created_at: str
    confirmed_at: str | None
    executed_at: str | None
    completed_at: str | None

    def public(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id, "capability_id": self.capability_id,
            "operation": self.operation, "scope": self.scope, "payload_sha256": self.payload_sha256,
            "policy_decision": self.policy_decision, "policy_revision": self.policy_revision,
            "status": self.status, "work_id": self.work_id, "step_id": self.step_id,
            "summary": self.summary, "result": self.result, "receipt": self.receipt,
            "error_code": self.error_code, "error": self.error, "created_at": self.created_at,
            "confirmed_at": self.confirmed_at, "executed_at": self.executed_at,
            "completed_at": self.completed_at,
        }
