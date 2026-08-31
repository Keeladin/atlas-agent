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
    def tick(self)->tuple[dict[str,Any],...]:
        now=_utcnow();created=[]
        for cadence in self.store.due(_iso(now)):
            next_run=self.next_after(cadence.schedule,now)
            if cadence.kind=="intake_sweep":
                if self.intake is None: raise RuntimeError("intake sweep runtime unavailable")
                summary=self.intake.sweep(cadence.intake_root_id or "",cadence.owner_principal_id,max_candidates=cadence.max_candidates)
                work_ids=[row.get("work_id") for row in summary.get("results",[]) if row.get("work_id")]
                last_work_id=work_ids[-1] if work_ids else None
                self.store.mark_run(cadence.cadence_id,last_run_at=_iso(now),last_work_id=last_work_id,next_run_at=_iso(next_run),last_result=summary)
                created.append({"cadence_id":cadence.cadence_id,"kind":"intake_sweep","summary":summary,"work_ids":work_ids})
                continue
            work=self.work.create(cadence.objective,cadence.steps,owner_principal_id=cadence.owner_principal_id,metadata={"cadence_id":cadence.cadence_id})
            self.work.run(work.work_id)
            self.store.mark_run(cadence.cadence_id,last_run_at=_iso(now),last_work_id=work.work_id,next_run_at=_iso(next_run),last_result={"work_id":work.work_id})
            created.append({"cadence_id":cadence.cadence_id,"kind":"work_template","work_id":work.work_id})
        return tuple(created)
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
    schema={"type":"object","required":["name","objective","schedule"],"properties":{"name":{"type":"string"},"objective":{"type":"string"},"schedule":{"type":"object"},"kind":{"type":"string","enum":["work_template","intake_sweep"]},"steps":{"type":"array","items":{"type":"object"}},"root_id":{"type":"string"},"max_candidates":{"type":"integer","minimum":1,"maximum":250}},"additionalProperties":False}
    def resolve(payload):return ScopeResolution("atlas/cadence",dict(payload),f"Create Cadence: {payload.get('name','')}")
    def execute(payload):
        owner=payload.pop("__owner_principal_id",None);payload.pop("__invocation_surface",None)
        if not owner:return ActionResult(False,error_code="owner_context_missing",error="owner principal unavailable")
        kind=str(payload.get("kind") or "work_template");steps=payload.get("steps") or []
        try:
            item=runtime.create(name=payload["name"],objective=payload["objective"],schedule=payload["schedule"],steps=steps,owner_principal_id=owner,kind=kind,intake_root_id=payload.get("root_id"),max_candidates=int(payload.get("max_candidates") or 25))
            return ActionResult(True,item.as_dict(),{"ok":True,"operation":"cadence.create","cadence_id":item.cadence_id,"kind":kind})
        except Exception as exc:
            return ActionResult(False,error_code="cadence_create_failed",error=str(exc),receipt={"ok":False,"operation":"cadence.create"})
    registry.register(CapabilityRegistration(CapabilityDefinition("cadence.create","Create a recurring standing duty: ordinary Work materialization or a monitored Artifact intake sweep.","create","internal",schema,source="cadence",tags=("cadence","work","sources")),resolve,execute,metadata={"scope_hint":"atlas/cadence","requires_owner_context":True}),replace=True)
