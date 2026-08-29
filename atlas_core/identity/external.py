from __future__ import annotations

from .models import ExternalAccountBinding
from .store import IdentityError, IdentityStore

class ExternalBindingError(ValueError):
    def __init__(self,code:str,detail:str="")->None:self.code=code;super().__init__(code if not detail else f"{code}: {detail}")

def validate_external_binding(identities:IdentityStore,binding:ExternalAccountBinding,*,capability_id:str,operation:str)->tuple:
    """Revalidate custody/provider technical truth only. Owner policy is separate."""
    if binding.capability_id!=capability_id or binding.operation!=operation:raise ExternalBindingError("binding_scope_mismatch")
    try:connection=identities.connection(binding.connection_id);service=identities.service_binding(binding.service_binding_id)
    except IdentityError as exc:raise ExternalBindingError(str(exc)) from exc
    if connection.provider_id!=binding.provider_id:raise ExternalBindingError("provider_mismatch")
    if connection.identity_profile_version!=binding.identity_profile_version:raise ExternalBindingError("identity_profile_changed")
    if service.connection_id!=connection.connection_id:raise ExternalBindingError("service_binding_mismatch")
    if service.service_profile_version!=binding.service_profile_version:raise ExternalBindingError("service_profile_changed")
    if operation not in service.attested_operations:raise ExternalBindingError("operation_not_attested")
    return connection,service
