from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ATLAS_PRINCIPAL_ID="principal_atlas"

@dataclass(frozen=True)
class Principal:
    principal_id:str; principal_kind:str; display_name:str; status:str; created_at:str
    def as_dict(self)->dict[str,Any]: return self.__dict__.copy()

@dataclass(frozen=True)
class AccountConnection:
    connection_id:str; provider_id:str; provider_subject_id:str; canonical_address:str; display_name:str
    owner_principal_id:str; status:str; identity_profile_version:str; created_at:str; updated_at:str
    tenant_id:str|None=None; provider_metadata:dict[str,Any]|None=None
    def as_dict(self)->dict[str,Any]:
        value=self.__dict__.copy(); value["provider_metadata"]=dict(self.provider_metadata or {}); return value

@dataclass(frozen=True)
class ServiceBinding:
    binding_id:str; connection_id:str; service:str; channel:str; dispatch_ref:str
    attested_operations:tuple[str,...]; service_profile_version:str; health:str; lifecycle:str
    attested_at:str|None; created_at:str; updated_at:str
    def as_dict(self, *, include_dispatch_ref:bool=False)->dict[str,Any]:
        value={"binding_id":self.binding_id,"connection_id":self.connection_id,"service":self.service,"channel":self.channel,"attested_operations":list(self.attested_operations),"service_profile_version":self.service_profile_version,"health":self.health,"lifecycle":self.lifecycle,"attested_at":self.attested_at,"created_at":self.created_at,"updated_at":self.updated_at}
        if include_dispatch_ref:value["dispatch_ref"]=self.dispatch_ref
        return value

@dataclass(frozen=True)
class ExternalAccountBinding:
    connection_id:str; service_binding_id:str; provider_id:str; capability_id:str; operation:str
    identity_profile_version:str; service_profile_version:str
    def as_dict(self)->dict[str,Any]: return self.__dict__.copy()
