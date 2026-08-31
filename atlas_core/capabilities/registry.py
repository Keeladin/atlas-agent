from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass,field
from typing import Any
from atlas_core.actions import ActionResult
from .models import CapabilityDefinition,ScopeResolution

ScopeResolver=Callable[[dict[str,Any]],ScopeResolution]
Executor=Callable[[dict[str,Any]],ActionResult]
Availability=Callable[[],tuple[bool,str]]

@dataclass
class CapabilityRegistration:
    definition:CapabilityDefinition
    resolve_scope:ScopeResolver
    executor:Executor
    availability:Availability=lambda:(True,"available")
    metadata:dict[str,Any]=field(default_factory=dict)

class CapabilityRegistry:
    """Complete runtime capability inventory. Registration never grants authority."""
    def __init__(self)->None:self._items:dict[str,CapabilityRegistration]={}
    def register(self,item:CapabilityRegistration,*,replace:bool=False)->None:
        cid=item.definition.id
        if cid in self._items and not replace:raise ValueError(f"capability already registered: {cid}")
        self._items[cid]=item
    def unregister_prefix(self,prefix:str)->None:
        for key in tuple(self._items):
            if key.startswith(prefix):self._items.pop(key,None)
    def get(self,capability_id:str)->CapabilityRegistration:
        try:return self._items[capability_id]
        except KeyError as exc:raise KeyError(f"unknown capability: {capability_id}") from exc
    def all(self)->tuple[CapabilityRegistration,...]:return tuple(self._items[key] for key in sorted(self._items))
    def executor(self, capability_id: str, principal_id: str | None = None, surface: str | None = None,
                 work_id: str | None = None, step_id: str | None = None) -> Executor:
        item = self.get(capability_id)
        needs_owner = bool(item.metadata.get("requires_owner_context"))
        needs_invocation = bool(item.metadata.get("requires_invocation_context"))
        if not needs_owner and not needs_invocation:
            return item.executor
        def execute(payload: dict[str, Any]) -> ActionResult:
            contextual = dict(payload)
            if needs_owner and principal_id:
                contextual["__owner_principal_id"] = principal_id
            if needs_owner and surface:
                contextual["__invocation_surface"] = surface
            if needs_invocation and work_id:
                contextual["__work_id"] = work_id
            if needs_invocation and step_id:
                contextual["__step_id"] = step_id
            return item.executor(contextual)
        return execute
