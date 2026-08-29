from __future__ import annotations
from typing import Any
from atlas_core.actions import ActionStore
from atlas_core.capabilities import CapabilityRuntime
from atlas_core.provenance import InvocationProvenance
from .store import WorkStore

class WorkRuntime:
    """Durable responsibility. Current owner policy is resolved at every actual step execution."""
    def __init__(self,store:WorkStore,capabilities:CapabilityRuntime,actions:ActionStore)->None:self.store=store;self.capabilities=capabilities;self.actions=actions
    def create(self,objective:str,steps:list[dict[str,Any]],*,owner_principal_id:str,metadata:dict[str,Any]|None=None):return self.store.create(objective,owner_principal_id,steps,metadata=metadata)
    def detail(self,work_id:str)->dict[str,Any]:return {**self.store.get(work_id).as_dict(),"steps":[s.as_dict() for s in self.store.steps(work_id)]}
    def run(self,work_id:str)->dict[str,Any]:
        work=self.store.get(work_id)
        if work.status in {"completed","failed","cancelled"}:return self.detail(work_id)
        if work.status=="paused":return self.detail(work_id)
        self.store.set_work_status(work_id,"active")
        for step in self.store.steps(work_id):
            if step.status=="completed":continue
            if step.occurrence_id:
                occurrence=self.actions.get(step.occurrence_id)
                if occurrence.status=="succeeded":self.store.set_step(step.step_id,status="completed",output=occurrence.result);continue
                if occurrence.status in {"pending_confirmation","uncertain"}:self.store.set_step(step.step_id,status="waiting_confirmation" if occurrence.status=="pending_confirmation" else "waiting");self.store.set_work_status(work_id,"waiting_confirmation" if occurrence.status=="pending_confirmation" else "waiting");return self.detail(work_id)
                if occurrence.status in {"blocked","failed","expired","cancelled"}:self.store.set_step(step.step_id,status="failed",error=occurrence.error or occurrence.error_code);self.store.set_work_status(work_id,"failed");return self.detail(work_id)
            self.store.set_step(step.step_id,status="running")
            try:occurrence=self.capabilities.invoke(step.capability_id,step.input,provenance=InvocationProvenance(work.owner_principal_id,"human","work"),work_id=work_id,step_id=step.step_id)
            except Exception as exc:self.store.set_step(step.step_id,status="failed",error=str(exc));self.store.set_work_status(work_id,"failed");return self.detail(work_id)
            if occurrence.status=="succeeded":self.store.set_step(step.step_id,status="completed",occurrence_id=occurrence.occurrence_id,output=occurrence.result);continue
            if occurrence.status=="pending_confirmation":self.store.set_step(step.step_id,status="waiting_confirmation",occurrence_id=occurrence.occurrence_id);self.store.set_work_status(work_id,"waiting_confirmation");return self.detail(work_id)
            if occurrence.status=="uncertain":self.store.set_step(step.step_id,status="waiting",occurrence_id=occurrence.occurrence_id);self.store.set_work_status(work_id,"waiting");return self.detail(work_id)
            self.store.set_step(step.step_id,status="failed",occurrence_id=occurrence.occurrence_id,error=occurrence.error or occurrence.error_code);self.store.set_work_status(work_id,"failed");return self.detail(work_id)
        self.store.set_work_status(work_id,"completed");return self.detail(work_id)
    def pause(self,work_id:str):return self.store.set_work_status(work_id,"paused")
    def resume(self,work_id:str):self.store.set_work_status(work_id,"active");return self.run(work_id)


def register_work_capabilities(registry, runtime:WorkRuntime)->None:
    from atlas_core.actions import ActionResult
    from atlas_core.capabilities import CapabilityDefinition,CapabilityRegistration,ScopeResolution
    schema={"type":"object","required":["objective","steps"],"properties":{"objective":{"type":"string"},"steps":{"type":"array","minItems":1,"items":{"type":"object","required":["capability_id","input"],"properties":{"capability_id":{"type":"string"},"description":{"type":"string"},"input":{"type":"object"}},"additionalProperties":False}},"run":{"type":"boolean"}},"additionalProperties":False}
    def resolve(payload):return ScopeResolution("atlas/work",dict(payload),f"Create Work: {payload.get('objective','')}")
    def execute(payload):
        owner=payload.pop("__owner_principal_id",None)
        if not owner:return ActionResult(False,error_code="owner_context_missing",error="owner principal unavailable")
        work=runtime.create(payload["objective"],payload["steps"],owner_principal_id=owner);result=runtime.run(work.work_id) if payload.get("run",True) else runtime.detail(work.work_id);return ActionResult(True,result,{"ok":True,"operation":"work.create","work_id":work.work_id})
    # owner context is injected by Chat before invocation; Work create is also available via its direct control API.
    registry.register(CapabilityRegistration(CapabilityDefinition("work.create","Create durable Work from an objective and explicit capability steps.","create","internal",schema,source="work",tags=("work",)),resolve,execute,metadata={"scope_hint":"atlas/work","requires_owner_context":True}),replace=True)
