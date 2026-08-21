from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityBinding:
    """Deployment map: Atlas capability → provider implementation.

    This is not permission, not MCP discovery, and not a runtime handler.
    Replacing ``n8n.execute_workflow`` with ``temporal.run_workflow`` must not
    change capability id, prompts, authority, or confirmation.
    """

    capability_id: str
    provider: str
    implementation: str
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("Capability binding capability_id must not be empty")
        if not self.provider.strip():
            raise ValueError("Capability binding provider must not be empty")
        if not self.implementation.strip():
            raise ValueError("Capability binding implementation must not be empty")
        if not str(self.version).strip():
            raise ValueError("Capability binding version must not be empty")

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.capability_id, self.provider, self.implementation, str(self.version))

    def as_dict(self) -> dict[str, str]:
        return {
            "capability_id": self.capability_id,
            "provider": self.provider,
            "implementation": self.implementation,
            "version": str(self.version),
        }


class CapabilityBindingIndex:
    """In-memory deployment bindings. Not persisted and not an authority grant."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str], CapabilityBinding] = {}

    def register(self, binding: CapabilityBinding, *, replace: bool = False) -> None:
        if binding.key in self._rows and not replace:
            raise ValueError(
                "Capability binding already registered: "
                f"{binding.capability_id} -> {binding.provider}.{binding.implementation}@{binding.version}"
            )
        self._rows[binding.key] = binding

    def for_capability(self, capability_id: str) -> tuple[CapabilityBinding, ...]:
        return tuple(
            row
            for row in self._rows.values()
            if row.capability_id == capability_id
        )

    def for_implementation(self, provider: str, implementation: str) -> tuple[CapabilityBinding, ...]:
        return tuple(
            row
            for row in self._rows.values()
            if row.provider == provider and row.implementation == implementation
        )

    def mapped_implementations(self) -> frozenset[tuple[str, str]]:
        return frozenset((row.provider, row.implementation) for row in self._rows.values())
