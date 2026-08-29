from __future__ import annotations

from dataclasses import dataclass,field
from typing import Any,Literal

EffectClass=Literal["none","internal","reversible","external","destructive"]

@dataclass(frozen=True)
class CapabilityDefinition:
    id:str
    description:str
    operation:str
    effect_class:EffectClass="none"
    input_schema:dict[str,Any]=field(default_factory=dict)
    source:str="native"
    tags:tuple[str,...]=()
    def __post_init__(self)->None:
        if not self.id.strip():raise ValueError("capability id must not be empty")
        if not self.description.strip():raise ValueError("capability description must not be empty")
        if not self.operation.strip():raise ValueError("capability operation must not be empty")
        if self.effect_class not in {"none","internal","reversible","external","destructive"}:raise ValueError("unsupported effect_class")
    def as_dict(self)->dict[str,Any]:return {"id":self.id,"description":self.description,"operation":self.operation,"effect_class":self.effect_class,"input_schema":self.input_schema,"source":self.source,"tags":list(self.tags)}

@dataclass(frozen=True)
class ScopeResolution:
    scope:str
    payload:dict[str,Any]
    summary:str|None=None

@dataclass(frozen=True)
class CapabilitySnapshot:
    definition:CapabilityDefinition
    available:bool
    availability_reason:str
    policy_decision:str
    policy_revision:int
    scope_hint:str|None=None
    metadata:dict[str,Any]=field(default_factory=dict)
    def as_dict(self)->dict[str,Any]:
        value=self.definition.as_dict();value.update({"available":self.available,"availability_reason":self.availability_reason,"policy_decision":self.policy_decision,"policy_revision":self.policy_revision,"scope_hint":self.scope_hint,"metadata":self.metadata});return value
