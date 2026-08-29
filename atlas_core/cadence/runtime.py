from __future__ import annotations
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from typing import Any
from atlas_core.work import WorkRuntime
from .store import CadenceStore

def _utcnow()->datetime:return datetime.now(timezone.utc)
def _iso(dt:datetime)->str:return dt.astimezone(timezone.utc).isoformat()

class CadenceRuntime:
    def __init__(self,store:CadenceStore,work:WorkRuntime)->None:self.store=store;self.work=work
    def create(self,*,name:str,objective:str,schedule:dict[str,Any],steps:list[dict[str,Any]],owner_principal_id:str):
        next_run=self.next_after(schedule,_utcnow()-timedelta(seconds=1));return self.store.create(name=name,objective=objective,schedule=schedule,steps=steps,owner_principal_id=owner_principal_id,next_run_at=_iso(next_run))
    def tick(self)->tuple[dict[str,Any],...]:
        now=_utcnow();created=[]
        for cadence in self.store.due(_iso(now)):
            work=self.work.create(cadence.objective,cadence.steps,owner_principal_id=cadence.owner_principal_id,metadata={"cadence_id":cadence.cadence_id});self.work.run(work.work_id);next_run=self.next_after(cadence.schedule,now);self.store.mark_run(cadence.cadence_id,last_run_at=_iso(now),last_work_id=work.work_id,next_run_at=_iso(next_run));created.append({"cadence_id":cadence.cadence_id,"work_id":work.work_id})
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
    schema={"type":"object","required":["name","objective","schedule","steps"],"properties":{"name":{"type":"string"},"objective":{"type":"string"},"schedule":{"type":"object"},"steps":{"type":"array","minItems":1,"items":{"type":"object"}}},"additionalProperties":False}
    def resolve(payload):return ScopeResolution("atlas/cadence",dict(payload),f"Create Cadence: {payload.get('name','')}")
    def execute(payload):
        owner=payload.pop("__owner_principal_id",None)
        if not owner:return ActionResult(False,error_code="owner_context_missing",error="owner principal unavailable")
        item=runtime.create(name=payload["name"],objective=payload["objective"],schedule=payload["schedule"],steps=payload["steps"],owner_principal_id=owner);return ActionResult(True,item.as_dict(),{"ok":True,"operation":"cadence.create","cadence_id":item.cadence_id})
    registry.register(CapabilityRegistration(CapabilityDefinition("cadence.create","Create a recurring standing duty that instantiates Work when due.","create","internal",schema,source="cadence",tags=("cadence","work")),resolve,execute,metadata={"scope_hint":"atlas/cadence","requires_owner_context":True}),replace=True)
