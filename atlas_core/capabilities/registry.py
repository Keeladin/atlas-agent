from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .contracts import CapabilityOutcome, CapabilityRequest
from .definition import CapabilityDefinition
from .execution import CapabilityExecutionProfile


CapabilityHandler = Callable[[CapabilityRequest], CapabilityOutcome]


def _version_key(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


@dataclass(frozen=True)
class CapabilityRegistration:
    """Executable registration: meaning + this deployment's profile + handler."""

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


class CapabilityRegistryError(RuntimeError):
    pass


class CapabilityRegistry:
    """Executable capability registrations for the Work engine. Not the meaning catalog.

    register() does not add rows to catalog() / lookup(). Product identity is CapabilityDefinition.
    """

    def __init__(self) -> None:
        self._registrations: dict[tuple[str, str], CapabilityRegistration] = {}

    def register(
        self,
        definition: CapabilityDefinition | CapabilityRegistration,
        profile: CapabilityExecutionProfile | CapabilityHandler | None = None,
        handler: CapabilityHandler | None = None,
        *,
        replace: bool = False,
    ) -> None:
        if isinstance(definition, CapabilityRegistration):
            item = definition
            if handler is None and callable(profile):
                handler = profile
            definition = item.definition
            profile = item.profile
            handler = handler or item.handler
        if not isinstance(profile, CapabilityExecutionProfile):
            raise CapabilityRegistryError("execution profile is required")
        if profile.capability_id != definition.id:
            raise CapabilityRegistryError(
                "execution profile capability_id must match definition id"
            )
        key = (definition.id, profile.version)
        if key in self._registrations and not replace:
            raise CapabilityRegistryError(
                f"Capability already registered: {definition.id}@{profile.version}"
            )
        if profile.executor_kind in {"deterministic", "tool", "composite"} and handler is None:
            raise CapabilityRegistryError(
                f"{profile.executor_kind} capability requires a handler: {definition.id}"
            )
        self._registrations[key] = CapabilityRegistration(definition, profile, handler)

    def get(self, capability_id: str, version: str | None = None) -> CapabilityRegistration:
        if version is not None:
            try:
                return self._registrations[(capability_id, version)]
            except KeyError as exc:
                raise CapabilityRegistryError(
                    f"Unknown capability: {capability_id}@{version}"
                ) from exc

        candidates = [
            registration
            for (candidate_id, _), registration in self._registrations.items()
            if candidate_id == capability_id and not registration.profile.deprecated
        ]
        if not candidates:
            if any(key[0] == capability_id for key in self._registrations):
                raise CapabilityRegistryError(
                    f"Capability has no active version: {capability_id}"
                )
            raise CapabilityRegistryError(f"Unknown capability: {capability_id}")
        return max(candidates, key=lambda item: _version_key(item.profile.version))

    def resolve_ref(self, reference: str) -> CapabilityRegistration:
        if "@" not in reference:
            return self.get(reference)
        capability_id, version = reference.rsplit("@", 1)
        return self.get(capability_id, version)

    def registrations(self, *, include_deprecated: bool = True) -> tuple[CapabilityRegistration, ...]:
        items = list(self._registrations.values())
        if not include_deprecated:
            items = [item for item in items if not item.profile.deprecated]
        return tuple(
            sorted(items, key=lambda item: (item.definition.id, _version_key(item.profile.version)))
        )

    def manifest(self, *, include_all_versions: bool = False) -> list[dict[str, Any]]:
        if include_all_versions:
            items = self.registrations(include_deprecated=True)
        else:
            ids = sorted({item.definition.id for item in self.registrations(include_deprecated=False)})
            items = tuple(self.get(capability_id) for capability_id in ids)
        return [self._manifest_item(item) for item in items]

    @staticmethod
    def _manifest_item(registration: CapabilityRegistration) -> dict[str, Any]:
        definition = registration.definition
        profile = registration.profile
        return {
            "id": definition.id,
            "version": profile.version,
            "ref": profile.ref,
            "name": registration.display_name,
            "description": definition.description,
            "objective": registration.effective_objective,
            "executor_kind": profile.executor_kind,
            "required_authority": definition.required_authority,
            "allowed_tools": list(profile.tools),
            "side_effects": list(profile.side_effects),
            "side_effect_class": definition.side_effect_class,
            "confirmation": definition.confirmation,
            "context_profile": profile.context_profile,
            "context_policy": {
                "max_tokens": profile.context_policy.max_tokens,
                "max_memory_items": profile.context_policy.max_memory_items,
                "max_artifact_items": profile.context_policy.max_artifact_items,
                "max_recent_steps": profile.context_policy.max_recent_steps,
                "min_relevance_score": profile.context_policy.min_relevance_score,
                "per_item_token_cap": profile.context_policy.per_item_token_cap,
                "allow_full_artifact": profile.context_policy.allow_full_artifact,
                "must_include": list(profile.context_policy.must_include),
                "must_exclude": list(profile.context_policy.must_exclude),
                "hybrid_weights": {
                    "semantic": profile.context_policy.hybrid_weights.semantic,
                    "recency": profile.context_policy.hybrid_weights.recency,
                    "importance": profile.context_policy.hybrid_weights.importance,
                },
            },
            "output_kind": profile.output_kind,
            "requires_artifact_kinds": list(profile.requires_artifact_kinds),
            "eligible_providers": list(profile.eligible_providers),
            "verifier_id": profile.verifier_id,
            "verification_required": profile.verification_required,
            "idempotent": profile.idempotent,
            "parallel_safe": profile.parallel_safe,
            "privacy": profile.privacy,
            "data_classification": profile.data_classification,
            "deprecated": profile.deprecated,
            "replaced_by": profile.replaced_by,
            "tags": list(profile.tags),
            "budget": {
                "max_attempts": profile.budget.max_attempts,
                "timeout_seconds": profile.budget.timeout_seconds,
                "max_context_chars": profile.budget.max_context_chars,
                "max_output_chars": profile.budget.max_output_chars,
                "max_cost_usd": profile.budget.max_cost_usd,
                "max_tool_calls": profile.budget.max_tool_calls,
            },
        }
