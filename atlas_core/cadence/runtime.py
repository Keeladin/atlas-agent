from __future__ import annotations
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from typing import Any
from atlas_core.work import WorkRuntime
from .store import CadenceStore

def _utcnow()->datetime:return datetime.now(timezone.utc)
def _iso(dt:datetime)->str:return dt.astimezone(timezone.utc).isoformat()

class CadenceRuntime:
    def __init__(self,store:CadenceStore,work:WorkRuntime,intake=None)->None:self.store=store;self.work=work;self.intake=intake
    def create(self,*,name:str,objective:str,schedule:dict[str,Any],steps:list[dict[str,Any]],owner_principal_id:str,kind:str="work_template",intake_root_id:str|None=None,max_candidates:int=25):
        next_run=self.next_after(schedule,_utcnow()-timedelta(seconds=1))
        return self.store.create(name=name,objective=objective,schedule=schedule,steps=steps,owner_principal_id=owner_principal_id,next_run_at=_iso(next_run),kind=kind,intake_root_id=intake_root_id,max_candidates=max_candidates)
    def _materialize(self,cadence)->tuple[dict[str,Any],str|None,dict[str,Any]|None]:
        if cadence.kind=="intake_sweep":
            if self.intake is None: raise RuntimeError("intake sweep runtime unavailable")
            summary=self.intake.sweep(cadence.intake_root_id or "",cadence.owner_principal_id,max_candidates=cadence.max_candidates)
            work_ids=[row.get("work_id") for row in summary.get("results",[]) if row.get("work_id")]
            last_work_id=work_ids[-1] if work_ids else None
            return {"cadence_id":cadence.cadence_id,"kind":"intake_sweep","summary":summary,"work_ids":work_ids},last_work_id,summary
        work=self.work.create(cadence.objective,cadence.steps,owner_principal_id=cadence.owner_principal_id,metadata={"cadence_id":cadence.cadence_id})
        self.work.run(work.work_id)
        return {"cadence_id":cadence.cadence_id,"kind":"work_template","work_id":work.work_id},work.work_id,{"work_id":work.work_id}
    def tick(self)->tuple[dict[str,Any],...]:
        now=_utcnow();created=[]
        for cadence in self.store.due(_iso(now)):
            next_run=self.next_after(cadence.schedule,now)
            entry,last_work_id,last_result=self._materialize(cadence)
            self.store.mark_run(cadence.cadence_id,last_run_at=_iso(now),last_work_id=last_work_id,next_run_at=_iso(next_run),last_result=last_result)
            created.append(entry)
        return tuple(created)
    def update(self,cadence_id:str,patch:dict[str,Any]):
        current=self.store.get(cadence_id)
        next_run=current.next_run_at
        if "schedule" in patch and current.enabled:next_run=_iso(self.next_after(patch["schedule"],_utcnow()-timedelta(seconds=1)))
        return self.store.update(cadence_id,patch,next_run_at=next_run)
    def update_work_template(self,cadence_id:str,patch:dict[str,Any]):
        """Chat-facing update. Authoring an intake sweep definition is deliberately not exposed."""
        current=self.store.get(cadence_id)
        if current.kind!="work_template":raise ValueError("cadence_update_not_supported")
        return self.update(cadence_id,patch)
    def run_now(self,cadence_id:str)->dict[str,Any]:
        """Owner-triggered manual run.

        Deliberately bypasses both the enabled flag and the next_run_at gate.
        `enabled=False` means "do not fire automatically on schedule", not "this
        object may never execute"; an explicit owner trigger is a different origin
        from the scheduler. The schedule itself is left untouched.
        """
        cadence=self.store.get(cadence_id)
        entry,last_work_id,last_result=self._materialize(cadence)
        self.store.mark_manual_run(cadence_id,last_run_at=_iso(_utcnow()),last_work_id=last_work_id,last_result=last_result)
        return {**entry,"trigger":"manual"}
    def next_after(self,schedule:dict[str,Any],after:datetime)->datetime:
        kind=str(schedule.get("kind") or "");tz=ZoneInfo(str(schedule.get("timezone") or "UTC"));local=after.astimezone(tz)
        if kind=="interval":
            minutes=int(schedule.get("minutes") or 0)
            if minutes<1:raise ValueError("interval cadence requires minutes >= 1")
            return after+timedelta(minutes=minutes)
        hour=int(schedule.get("hour",8));minute=int(schedule.get("minute",0))
        if not (0<=hour<=23 and 0<=minute<=59):raise ValueError("invalid cadence hour/minute")
        candidate=local.replace(hour=hour,minute=minute,second=0,microsecond=0)
        if kind=="daily":
            if candidate<=local:candidate+=timedelta(days=1)
            return candidate.astimezone(timezone.utc)
        if kind=="weekly":
            weekday=int(schedule.get("weekday",0))
            if not 0<=weekday<=6:raise ValueError("weekday must be 0..6")
            days=(weekday-local.weekday())%7;candidate=candidate+timedelta(days=days)
            if candidate<=local:candidate+=timedelta(days=7)
            return candidate.astimezone(timezone.utc)
        raise ValueError("cadence schedule kind must be interval, daily, or weekly")


def register_cadence_capabilities(registry,runtime:CadenceRuntime)->None:
    from atlas_core.actions import ActionResult
    from atlas_core.capabilities import CapabilityDefinition,CapabilityRegistration,ScopeResolution
    steps_schema={"type":"array","minItems":1,"items":{"type":"object"}}
    # Chat-facing cadence authoring is work-template only. CadenceRuntime/CadenceStore
    # stay generic across both kinds; the narrowing lives here at the capability boundary.
    create_schema={"type":"object","required":["name","objective","schedule","steps"],"properties":{"name":{"type":"string"},"objective":{"type":"string"},"schedule":{"type":"object"},"steps":steps_schema},"additionalProperties":False}
    update_schema={"type":"object","required":["cadence_id"],"properties":{"cadence_id":{"type":"string"},"name":{"type":"string"},"objective":{"type":"string"},"schedule":{"type":"object"},"steps":steps_schema},"additionalProperties":False}
    run_schema={"type":"object","required":["cadence_id"],"properties":{"cadence_id":{"type":"string"}},"additionalProperties":False}
    get_schema={"type":"object","required":["cadence_id"],"properties":{"cadence_id":{"type":"string"}},"additionalProperties":False}
    list_schema={"type":"object","properties":{"query":{"type":"string"}},"additionalProperties":False}

    def _owner(payload):
        owner=payload.pop("__owner_principal_id",None);payload.pop("__invocation_surface",None);return owner

    def create_execute(payload):
        owner=_owner(payload)
        if not owner:return ActionResult(False,error_code="owner_context_missing",error="owner principal unavailable")
        try:
            item=runtime.create(name=payload["name"],objective=payload["objective"],schedule=payload["schedule"],steps=payload["steps"],owner_principal_id=owner,kind="work_template")
            return ActionResult(True,item.as_dict(),{"ok":True,"operation":"cadence.create","cadence_id":item.cadence_id,"kind":"work_template"})
        except Exception as exc:
            return ActionResult(False,error_code="cadence_create_failed",error=str(exc),receipt={"ok":False,"operation":"cadence.create"})

    def update_execute(payload):
        _owner(payload)
        cadence_id=payload.pop("cadence_id")
        try:
            item=runtime.update_work_template(cadence_id,dict(payload))
            return ActionResult(True,item.as_dict(),{"ok":True,"operation":"cadence.update","cadence_id":item.cadence_id})
        except Exception as exc:
            code="cadence_update_not_supported" if str(exc)=="cadence_update_not_supported" else "cadence_update_failed"
            return ActionResult(False,error_code=code,error=str(exc),receipt={"ok":False,"operation":"cadence.update"})

    def run_execute(payload):
        _owner(payload)
        try:
            result=runtime.run_now(payload["cadence_id"])
            return ActionResult(True,result,{"ok":True,"operation":"cadence.run_now","cadence_id":payload["cadence_id"]})
        except Exception as exc:
            return ActionResult(False,error_code="cadence_run_failed",error=str(exc),receipt={"ok":False,"operation":"cadence.run_now"})

    def list_execute(payload):
        _owner(payload)
        query=str(payload.get("query") or "").strip().lower()
        rows=[item.as_dict() for item in runtime.store.list()]
        if query:
            terms=[term for term in query.split() if term]
            rows=[row for row in rows if any(term in f"{row.get('name','')} {row.get('objective','')}".lower() for term in terms)]
        return ActionResult(True,rows,{"ok":True,"operation":"cadence.list","count":len(rows)})

    def get_execute(payload):
        _owner(payload)
        try:
            return ActionResult(True,runtime.store.get(payload["cadence_id"]).as_dict(),{"ok":True,"operation":"cadence.get","cadence_id":payload["cadence_id"]})
        except KeyError:
            return ActionResult(False,error_code="cadence_unknown",error="cadence not found",receipt={"ok":False,"operation":"cadence.get"})

    owner_meta={"scope_hint":"atlas/cadence","requires_owner_context":True}
    registry.register(CapabilityRegistration(CapabilityDefinition("cadence.create","Create a recurring standing duty that materializes ordinary Work on a schedule.","create","internal",create_schema,source="cadence",tags=("cadence","work")),lambda p:ScopeResolution("atlas/cadence",dict(p),f"Create Cadence: {p.get('name','')}"),create_execute,metadata=owner_meta),replace=True)
    registry.register(CapabilityRegistration(CapabilityDefinition("cadence.update","Change an existing standing duty's name, objective, schedule, or capability steps.","update","internal",update_schema,source="cadence",tags=("cadence","work")),lambda p:ScopeResolution("atlas/cadence",dict(p),f"Update Cadence: {p.get('cadence_id','')}"),update_execute,metadata=owner_meta),replace=True)
    registry.register(CapabilityRegistration(CapabilityDefinition("cadence.run_now","Run an existing standing duty immediately without changing its schedule.","run_now","internal",run_schema,source="cadence",tags=("cadence","work")),lambda p:ScopeResolution("atlas/cadence",dict(p),f"Run Cadence now: {p.get('cadence_id','')}"),run_execute,metadata=owner_meta),replace=True)
    registry.register(CapabilityRegistration(CapabilityDefinition("cadence.list","List standing duties, optionally narrowed by a name or objective query.","list","none",list_schema,source="cadence",tags=("cadence","work")),lambda p:ScopeResolution("atlas/cadence",dict(p),"List Cadence"),list_execute,metadata=owner_meta),replace=True)
    registry.register(CapabilityRegistration(CapabilityDefinition("cadence.get","Read one standing duty's full definition and latest run reference.","get","none",get_schema,source="cadence",tags=("cadence","work")),lambda p:ScopeResolution("atlas/cadence",dict(p),f"Read Cadence: {p.get('cadence_id','')}"),get_execute,metadata=owner_meta),replace=True)
