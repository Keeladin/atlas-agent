from __future__ import annotations

from atlas_core.capabilities import (
    CapabilityDefinition,
    CapabilityExecutionProfile,
    CapabilityRegistration,
)


def make_registration(handler=None, *, id: str, **kwargs) -> CapabilityRegistration:
    description = kwargs.pop("description", id)
    required_authority = kwargs.pop("required_authority", "read")
    confirmation = kwargs.pop("confirmation", "none")
    side_effect_class = kwargs.pop("side_effect_class", "none")
    if "allowed_tools" in kwargs:
        kwargs["tools"] = kwargs.pop("allowed_tools")
    definition = CapabilityDefinition(
        id=id,
        description=description,
        required_authority=required_authority,
        confirmation=confirmation,
        side_effect_class=side_effect_class,
    )
    profile = CapabilityExecutionProfile(capability_id=id, **kwargs)
    return CapabilityRegistration(definition, profile, handler)
