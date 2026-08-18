from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .contracts import CapabilityOutcome, CapabilityRequest, CapabilitySpec


CapabilityHandler = Callable[[CapabilityRequest], CapabilityOutcome]


@dataclass(frozen=True)
class CapabilityBinding:
    spec: CapabilitySpec
    handler: CapabilityHandler | None = None


class CapabilityRegistryError(RuntimeError):
    pass


class CapabilityRegistry:
    def __init__(self) -> None:
        self._bindings: dict[str, CapabilityBinding] = {}

    def register(self, spec: CapabilitySpec, handler: CapabilityHandler | None = None, *, replace: bool = False) -> None:
        if spec.id in self._bindings and not replace:
            raise CapabilityRegistryError(f"Capability already registered: {spec.id}")
        if spec.executor_kind in {"deterministic", "tool", "composite"} and handler is None:
            raise CapabilityRegistryError(f"{spec.executor_kind} capability requires a handler: {spec.id}")
        self._bindings[spec.id] = CapabilityBinding(spec, handler)

    def get(self, capability_id: str) -> CapabilityBinding:
        try:
            return self._bindings[capability_id]
        except KeyError as exc:
            raise CapabilityRegistryError(f"Unknown capability: {capability_id}") from exc

    def specs(self) -> tuple[CapabilitySpec, ...]:
        return tuple(binding.spec for _, binding in sorted(self._bindings.items()))

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "id": spec.id,
                "description": spec.description,
                "executor_kind": spec.executor_kind,
                "required_authority": spec.required_authority,
                "side_effects": list(spec.side_effects),
                "context_profile": spec.context_profile,
                "eligible_providers": list(spec.eligible_providers),
                "verifier_id": spec.verifier_id,
                "idempotent": spec.idempotent,
                "parallel_safe": spec.parallel_safe,
                "privacy": spec.privacy,
                "budget": {
                    "max_attempts": spec.budget.max_attempts,
                    "timeout_seconds": spec.budget.timeout_seconds,
                    "max_context_chars": spec.budget.max_context_chars,
                    "max_output_chars": spec.budget.max_output_chars,
                    "max_cost_usd": spec.budget.max_cost_usd,
                },
            }
            for spec in self.specs()
        ]
