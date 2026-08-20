from __future__ import annotations

from atlas_core.capabilities.execution import CapabilityExecutionProfile
from atlas_core.capabilities.registry import CapabilityHandler


class ExecutionProfileIndex:
    """Deployment execution profiles. Not a capability catalog."""

    def __init__(self) -> None:
        self._profiles: dict[str, CapabilityExecutionProfile] = {}
        self._handlers: dict[str, CapabilityHandler] = {}

    def register(
        self,
        profile: CapabilityExecutionProfile,
        handler: CapabilityHandler | None = None,
        *,
        replace: bool = False,
    ) -> None:
        if profile.capability_id in self._profiles and not replace:
            raise ValueError(
                f"Execution profile already registered: {profile.capability_id}"
            )
        self._profiles[profile.capability_id] = profile
        if handler is not None:
            self._handlers[profile.capability_id] = handler
        elif replace:
            self._handlers.pop(profile.capability_id, None)

    def get(self, capability_id: str) -> CapabilityExecutionProfile | None:
        return self._profiles.get(capability_id)

    def handler(self, capability_id: str) -> CapabilityHandler | None:
        return self._handlers.get(capability_id)

    def all(self) -> tuple[CapabilityExecutionProfile, ...]:
        return tuple(self._profiles.values())
