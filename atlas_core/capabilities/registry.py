from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .contracts import CapabilityOutcome, CapabilityRequest
from .definition import CapabilityDefinition
from .execution import CapabilityExecutionProfile


CapabilityHandler = Callable[[CapabilityRequest], CapabilityOutcome]


@dataclass(frozen=True)
class CapabilityRegistration:
    """Resolved Work triple: meaning + this deployment's profile + handler."""

    definition: CapabilityDefinition
    profile: CapabilityExecutionProfile
    handler: CapabilityHandler | None = None

    def __post_init__(self) -> None:
        if self.profile.capability_id != self.definition.id:
            raise ValueError("execution profile capability_id must match definition id")

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def ref(self) -> str:
        return self.profile.ref

    @property
    def display_name(self) -> str:
        return self.profile.name or self.definition.id

    @property
    def effective_objective(self) -> str:
        return self.profile.objective or self.definition.description
