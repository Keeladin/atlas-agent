from __future__ import annotations

from typing import Any
from atlas_core.actions import ActionRequest,ActionRuntime
from atlas_core.policy import OwnerPolicy
from atlas_core.provenance import InvocationProvenance
from atlas_core.schema_validation import SchemaValidationError,validate_json
from .models import CapabilitySnapshot
from .registry import CapabilityRegistry


class RuntimeContinuityRequired(RuntimeError):
    """Resolved execution cannot safely remain owned by an ephemeral caller."""

    def __init__(self, *, capability_id: str, scope: str, payload: dict[str, Any],
                 summary: str | None, reason: str | None) -> None:
        super().__init__(reason or "durable Work is required before this action can execute")
        self.capability_id = capability_id
        self.scope = scope
        self.payload = dict(payload)
        self.summary = summary
        self.reason = reason or "runtime continuity requires durable Work"


class CapabilityRuntime:
    def __init__(self,registry:CapabilityRegistry,actions:ActionRuntime,policy:OwnerPolicy)->None:self.registry=registry;self.actions=actions;self.policy=policy
    def invoke(self,capability_id:str,payload:dict[str,Any],*,provenance:InvocationProvenance,work_id:str|None=None,step_id:str|None=None,on_occurrence_created=None):
        registration=self.registry.get(capability_id)
        available,reason=registration.availability()
        if not available:raise RuntimeError(f"capability unavailable: {reason}")
        try:validate_json(payload,registration.definition.input_schema or {},path="$.invocation")
        except SchemaValidationError as exc:raise ValueError(f"capability input invalid: {exc}") from exc
        resolved=registration.resolve_scope(dict(payload))
        normalized=dict(resolved.payload)
        if resolved.requires_durable_work and work_id is None:
            raise RuntimeContinuityRequired(
                capability_id=capability_id, scope=resolved.scope, payload=normalized,
                summary=resolved.summary, reason=resolved.continuity_reason,
            )
        return self.actions.submit(
            ActionRequest(
                capability_id=capability_id, operation=registration.definition.operation, scope=resolved.scope,
                payload=normalized, provenance=provenance, work_id=work_id, step_id=step_id,
                summary=resolved.summary, initial_receipt=dict(resolved.pre_execution_receipt),
            ),
            on_created=on_occurrence_created,
        )
    def snapshot(self,*,principal_id:str)->tuple[CapabilitySnapshot,...]:
        rows=[]
        rules,revision=self.policy.store.snapshot(principal_id)
        for item in self.registry.all():
            available,reason=item.availability();hint=item.metadata.get("scope_hint")
            if hint:
                resolution=self.policy.resolve_from_rules(principal_id=principal_id,scope=str(hint),operation=item.definition.operation,rules=rules,revision=revision)
                decision=resolution.decision
            else:
                decision="NO"
            rows.append(CapabilitySnapshot(item.definition,available,reason,decision,revision,str(hint) if hint else None,dict(item.metadata)))
        return tuple(rows)
