from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas_core.work import WorkRuntime
from atlas_core.work.records import (
    ApprovalRecord,
    ArtifactRecord,
    ClaimRecord,
    ConfirmationRecord,
    EventRecord,
    ExecutionRecord,
    StepRecord,
    WorkState,
)
from atlas_core.work.contract import ContractCapability, WorkContract


@dataclass(frozen=True)
class WorkDetailView:
    """Semantic Companion projection of one Work item. Not a store snapshot."""

    work_id: str
    objective: str
    status: str
    authority_scope: str
    created_at: str
    updated_at: str
    phase: str
    blocking: dict[str, Any] | None
    contract: dict[str, Any]
    capabilities: tuple[dict[str, Any], ...]
    steps: tuple[dict[str, Any], ...]
    pending_approvals: tuple[dict[str, Any], ...]
    pending_confirmations: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]
    executions: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    criteria: tuple[dict[str, Any], ...]
    actions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "objective": self.objective,
            "status": self.status,
            "authority_scope": self.authority_scope,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "phase": self.phase,
            "blocking": self.blocking,
            "contract": self.contract,
            "capabilities": list(self.capabilities),
            "steps": list(self.steps),
            "pending_approvals": list(self.pending_approvals),
            "pending_confirmations": list(self.pending_confirmations),
            "artifacts": list(self.artifacts),
            "claims": list(self.claims),
            "executions": list(self.executions),
            "events": list(self.events),
            "criteria": list(self.criteria),
            "actions": list(self.actions),
        }


def work_list_item(state: WorkState) -> dict[str, Any]:
    return {
        "work_id": state.id,
        "objective": state.objective,
        "status": state.status,
        "authority_scope": state.authority_scope,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def build_work_detail(runtime: WorkRuntime, work_id: str) -> WorkDetailView:
    store = runtime.store
    state = store.get_work(work_id)
    contract = runtime.contract(work_id)
    steps = store.list_steps(work_id)
    approvals = store.list_approvals(work_id)
    confirmations = store.list_confirmations(work_id)
    pending_approvals = tuple(item for item in approvals if item.status == "pending")
    pending_confirmations = tuple(
        item for item in confirmations if item.status == "pending"
    )
    executions = store.list_executions(work_id)
    running = tuple(item for item in executions if item.status == "running")
    phase, blocking = _phase_and_blocking(
        state=state,
        pending_approvals=pending_approvals,
        pending_confirmations=pending_confirmations,
        running=running,
    )
    return WorkDetailView(
        work_id=state.id,
        objective=state.objective,
        status=state.status,
        authority_scope=state.authority_scope,
        created_at=state.created_at,
        updated_at=state.updated_at,
        phase=phase,
        blocking=blocking,
        contract=_contract_summary(contract),
        capabilities=tuple(_capability_pin(pin) for pin in contract.capabilities),
        steps=tuple(_step_view(step) for step in steps),
        pending_approvals=tuple(_approval_view(item) for item in pending_approvals),
        pending_confirmations=tuple(
            _confirmation_view(item) for item in pending_confirmations
        ),
        artifacts=tuple(
            _artifact_view(item) for item in store.list_artifacts(work_id)
        ),
        claims=tuple(_claim_view(item) for item in store.list_claims(work_id)),
        executions=tuple(_execution_view(item) for item in executions),
        events=tuple(_event_view(item) for item in store.list_events(work_id)),
        criteria=tuple(
            {
                "id": item.id,
                "ordinal": item.ordinal,
                "text": item.text,
                "status": item.status,
                "evidence_artifact_ids": list(item.evidence_artifact_ids),
                "note": item.note,
            }
            for item in store.list_criteria(work_id)
        ),
        actions=_actions(state, running),
    )


def _phase_and_blocking(
    *,
    state: WorkState,
    pending_approvals: tuple[ApprovalRecord, ...],
    pending_confirmations: tuple[ConfirmationRecord, ...],
    running: tuple[ExecutionRecord, ...],
) -> tuple[str, dict[str, Any] | None]:
    if state.status in {"completed", "failed", "cancelled"}:
        return "terminal", None
    if running:
        return "running", {
            "kind": "execution",
            "message": "An execution is in progress.",
            "execution_ids": [item.id for item in running],
        }
    if pending_confirmations:
        return "waiting_confirmation", {
            "kind": "payload_confirmation",
            "message": "Exact payload confirmation is required before execution.",
            "confirmation_ids": [item.id for item in pending_confirmations],
        }
    if pending_approvals:
        return "waiting_authority", {
            "kind": "authority_approval",
            "message": "Authority approval is required before execution.",
            "approval_ids": [item.id for item in pending_approvals],
        }
    if state.status == "waiting":
        return "waiting", {
            "kind": "waiting",
            "message": "Work is waiting with no ready step.",
        }
    if state.status == "planned":
        return "planned", None
    return "active", None


def _actions(
    state: WorkState, running: tuple[ExecutionRecord, ...]
) -> tuple[str, ...]:
    if state.status in {"completed", "failed", "cancelled"}:
        actions = ["result"]
        if running:
            actions.append("recover")
        return tuple(actions)
    actions = ["run"]
    if running:
        actions.append("recover")
    if state.status != "cancelled":
        actions.append("cancel")
    return tuple(actions)


def _contract_summary(contract: WorkContract) -> dict[str, Any]:
    return {
        "contract_id": contract.contract_id,
        "sha256": contract.sha256,
        "compiled_at": contract.compiled_at,
        "objective": contract.objective,
        "success_criteria": list(contract.success_criteria),
        "constraints": list(contract.constraints),
        "authority_scope": contract.authority_scope,
        "allowed_tools": list(contract.allowed_tools),
        "confirmation_requirements": list(contract.confirmation_requirements),
    }


def _capability_pin(pin: ContractCapability) -> dict[str, Any]:
    return {
        "capability_id": pin.capability_id,
        "armed": pin.armed,
        "confirmation": pin.confirmation,
        "required_authority": pin.required_authority,
        "profile_version": pin.profile_version,
        "executor_kind": pin.executor_kind,
        "tools": list(pin.tools),
        "side_effects": list(pin.side_effects),
    }


def _step_view(step: StepRecord) -> dict[str, Any]:
    return {
        "id": step.id,
        "ordinal": step.ordinal,
        "description": step.description,
        "capability": step.capability,
        "capability_version": step.capability_version,
        "status": step.status,
        "dependencies": list(step.dependencies),
        "input_artifact_ids": list(step.input_artifact_ids),
    }


def _approval_view(item: ApprovalRecord) -> dict[str, Any]:
    return {
        "id": item.id,
        "work_id": item.work_id,
        "step_id": item.step_id,
        "required_authority": item.required_authority,
        "requested_action": item.requested_action,
        "status": item.status,
        "created_at": item.created_at,
    }


def _confirmation_view(item: ConfirmationRecord) -> dict[str, Any]:
    return {
        "id": item.id,
        "work_id": item.work_id,
        "step_id": item.step_id,
        "capability_id": item.capability_id,
        "payload_sha256": item.payload_sha256,
        "summary": item.summary,
        "payload": _ui_payload(item.payload),
        "status": item.status,
        "created_at": item.created_at,
    }


def _ui_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Exact confirmation binding without ambient runtime noise."""

    return {
        "capability_id": payload.get("capability_id"),
        "profile_version": payload.get("profile_version"),
        "executor_kind": payload.get("executor_kind"),
        "tools": payload.get("tools") or [],
        "invocation_input": payload.get("invocation_input") or {},
        "binding": payload.get("binding"),
        "provider_snapshots": payload.get("provider_snapshots") or [],
        "work_id": payload.get("work_id"),
        "step_id": payload.get("step_id"),
    }


def _artifact_view(item: ArtifactRecord) -> dict[str, Any]:
    return {
        "id": item.id,
        "step_id": item.step_id,
        "kind": item.kind,
        "sha256": item.sha256,
        "metadata": item.metadata,
        "created_at": item.created_at,
        "payload": item.payload,
    }


def _claim_view(item: ClaimRecord) -> dict[str, Any]:
    return {
        "id": item.id,
        "step_id": item.step_id,
        "kind": item.kind,
        "subject": item.subject,
        "value": item.value,
        "evidence_artifact_ids": list(item.evidence_artifact_ids),
        "confidence": item.confidence,
        "created_at": item.created_at,
    }


def _execution_view(item: ExecutionRecord) -> dict[str, Any]:
    return {
        "id": item.id,
        "step_id": item.step_id,
        "capability": item.capability,
        "capability_version": item.capability_version,
        "provider": item.provider,
        "attempt": item.attempt,
        "status": item.status,
        "error": item.error,
        "receipt": item.receipt,
        "started_at": item.started_at,
        "ended_at": item.ended_at,
        "input_artifact_ids": list(item.input_artifact_ids),
        "output_artifact_ids": list(item.output_artifact_ids),
    }


def _event_view(item: EventRecord) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "step_id": item.step_id,
        "execution_id": item.execution_id,
        "payload": item.payload,
        "created_at": item.created_at,
    }
