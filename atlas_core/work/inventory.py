from __future__ import annotations

from atlas_core.capabilities.execution import CapabilityExecutionProfile
from atlas_core.capabilities.registry import CapabilityHandler


def _version_key(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


class DeploymentInventory:
    """What this process has available. Not what a Work item may execute.

    Keyed by ``(capability_id, version)``. ``get(id)`` without a version is
    compile-time pin selection (latest non-deprecated). Run resolution must
    pass the exact pinned version.
    """

    def __init__(self) -> None:
        self._profiles: dict[tuple[str, str], CapabilityExecutionProfile] = {}
        self._handlers: dict[tuple[str, str], CapabilityHandler] = {}

    def register(
        self,
        profile: CapabilityExecutionProfile,
        handler: CapabilityHandler | None = None,
    ) -> None:
        """Register an immutable ``id@version`` document.

        There is no replace. A semantic change is a new version.
        """

        key = (profile.capability_id, profile.version)
        if key in self._profiles:
            raise ValueError(
                "Execution profile already registered: "
                f"{profile.capability_id}@{profile.version}"
            )
        self._profiles[key] = profile
        if handler is not None:
            self._handlers[key] = handler

    def get(
        self,
        capability_id: str,
        version: str | None = None,
    ) -> CapabilityExecutionProfile | None:
        if version is not None:
            return self._profiles.get((capability_id, version))
        candidates = [
            profile
            for (candidate_id, _), profile in self._profiles.items()
            if candidate_id == capability_id and not profile.deprecated
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: _version_key(item.version))

    def handler(
        self,
        capability_id: str,
        version: str | None = None,
    ) -> CapabilityHandler | None:
        if version is not None:
            return self._handlers.get((capability_id, version))
        profile = self.get(capability_id)
        if profile is None:
            return None
        return self._handlers.get((capability_id, profile.version))

    def all(self) -> tuple[CapabilityExecutionProfile, ...]:
        """Bootstrap/debug only. Compile and resolve must not call this."""

        return tuple(
            sorted(
                self._profiles.values(),
                key=lambda item: (item.capability_id, _version_key(item.version)),
            )
        )
