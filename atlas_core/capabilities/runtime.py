from __future__ import annotations

from typing import Any
from atlas_core.actions import ActionRequest,ActionRuntime
from atlas_core.policy import OwnerPolicy
from atlas_core.provenance import InvocationProvenance
from atlas_core.schema_validation import SchemaValidationError,validate_json
from .models import CapabilitySnapshot
from .registry import CapabilityRegistry

class CapabilityRuntime:
    def __init__(self,registry:CapabilityRegistry,actions:ActionRuntime,policy:OwnerPolicy)->None:self.registry=registry;self.actions=actions;self.policy=policy
    def invoke(self,capability_id:str,payload:dict[str,Any],*,provenance:InvocationProvenance,work_id:str|None=None,step_id:str|None=None):
        registration=self.registry.get(capability_id)
        available,reason=registration.availability()
        if not available:raise RuntimeError(f"capability unavailable: {reason}")
        try:validate_json(payload,registration.definition.input_schema or {},path="$.invocation")
        except SchemaValidationError as exc:raise ValueError(f"capability input invalid: {exc}") from exc
        resolved=registration.resolve_scope(dict(payload))
        normalized=dict(resolved.payload)
        return self.actions.submit(ActionRequest(capability_id=capability_id,operation=registration.definition.operation,scope=resolved.scope,payload=normalized,provenance=provenance,work_id=work_id,step_id=step_id,summary=resolved.summary))
    def snapshot(self,*,principal_id:str)->tuple[CapabilitySnapshot,...]:
        rows=[]
        for item in self.registry.all():
            available,reason=item.availability();hint=item.metadata.get("scope_hint")
            if hint:
                resolution=self.policy.resolve(principal_id=principal_id,scope=str(hint),operation=item.definition.operation)
                decision=resolution.decision;revision=resolution.revision
            else:
                decision="NO";revision=self.policy.store.revision()
            rows.append(CapabilitySnapshot(item.definition,available,reason,decision,revision,str(hint) if hint else None,dict(item.metadata)))
        return tuple(rows)
