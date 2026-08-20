from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas_core.capabilities import CapabilityBinding
from atlas_core.capabilities.definition import CapabilityDefinition

from .profile import ExecutionProfileIndex


@dataclass(frozen=True)
class RuntimeFrame:
    """What this work execution is allowed to use.

    Built after Work accepts a Task Brief. Not Chat context, not discovery.
    """

    work_id: str
    capabilities: tuple[str, ...]
    bindings: tuple[CapabilityBinding, ...]
    allowed_tools: tuple[str, ...]
    authority_scope: str
    confirmation_requirements: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "capabilities": list(self.capabilities),
            "bindings": [
                {
                    "capability_id": item.capability_id,
                    "provider": item.provider,
                    "implementation": item.implementation,
                    "version": item.version,
                }
                for item in self.bindings
            ],
            "allowed_tools": list(self.allowed_tools),
            "authority_scope": self.authority_scope,
            "confirmation_requirements": list(self.confirmation_requirements),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RuntimeFrame:
        bindings = tuple(
            CapabilityBinding(
                capability_id=str(item["capability_id"]),
                provider=str(item["provider"]),
                implementation=str(item["implementation"]),
                version=str(item.get("version") or "1"),
            )
            for item in payload.get("bindings") or ()
            if isinstance(item, dict)
        )
        return cls(
            work_id=str(payload["work_id"]),
            capabilities=tuple(str(item) for item in payload.get("capabilities") or ()),
            bindings=bindings,
            allowed_tools=tuple(str(item) for item in payload.get("allowed_tools") or ()),
            authority_scope=str(payload["authority_scope"]),
            confirmation_requirements=tuple(
                str(item) for item in payload.get("confirmation_requirements") or ()
            ),
        )


def assemble_frame(
    *,
    work_id: str,
    capabilities: tuple[str, ...],
    authority_scope: str,
    definitions: dict[str, CapabilityDefinition],
    profiles: ExecutionProfileIndex,
) -> RuntimeFrame:
    resolved: list[CapabilityBinding] = []
    allowed: list[str] = []
    confirmations: list[str] = []
    for capability_id in capabilities:
        definition = definitions[capability_id]
        profile = profiles.get(capability_id)
        if profile is not None and profile.implementation is not None:
            resolved.append(profile.implementation)
        if profile is not None:
            for tool_id in profile.tools:
                if tool_id not in allowed:
                    allowed.append(tool_id)
        if definition.confirmation == "required" and capability_id not in confirmations:
            confirmations.append(capability_id)
    return RuntimeFrame(
        work_id=work_id,
        capabilities=capabilities,
        bindings=tuple(resolved),
        allowed_tools=tuple(allowed),
        authority_scope=authority_scope,
        confirmation_requirements=tuple(confirmations),
    )
