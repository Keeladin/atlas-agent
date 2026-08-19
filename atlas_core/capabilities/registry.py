from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .contracts import CapabilityOutcome, CapabilityRequest, CapabilitySpec


CapabilityHandler = Callable[[CapabilityRequest], CapabilityOutcome]


def _version_key(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


@dataclass(frozen=True)
class CapabilityBinding:
    spec: CapabilitySpec
    handler: CapabilityHandler | None = None


class CapabilityRegistryError(RuntimeError):
    pass


class CapabilityRegistry:
    """Version-aware registry of Atlas capability contracts."""

    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], CapabilityBinding] = {}

    def register(
        self,
        spec: CapabilitySpec,
        handler: CapabilityHandler | None = None,
        *,
        replace: bool = False,
    ) -> None:
        key = (spec.id, spec.version)
        if key in self._bindings and not replace:
            raise CapabilityRegistryError(
                f"Capability already registered: {spec.id}@{spec.version}"
            )
        if spec.executor_kind in {"deterministic", "tool", "composite"} and handler is None:
            raise CapabilityRegistryError(
                f"{spec.executor_kind} capability requires a handler: {spec.id}"
            )
        self._bindings[key] = CapabilityBinding(spec, handler)

    def get(self, capability_id: str, version: str | None = None) -> CapabilityBinding:
        if version is not None:
            try:
                return self._bindings[(capability_id, version)]
            except KeyError as exc:
                raise CapabilityRegistryError(
                    f"Unknown capability: {capability_id}@{version}"
                ) from exc

        candidates = [
            binding
            for (candidate_id, _), binding in self._bindings.items()
            if candidate_id == capability_id and not binding.spec.deprecated
        ]
        if not candidates:
            # A pinned task may still legitimately refer to a deprecated version;
            # unpinned resolution never silently chooses one.
            if any(key[0] == capability_id for key in self._bindings):
                raise CapabilityRegistryError(
                    f"Capability has no active version: {capability_id}"
                )
            raise CapabilityRegistryError(f"Unknown capability: {capability_id}")
        return max(candidates, key=lambda item: _version_key(item.spec.version))

    def resolve_ref(self, reference: str) -> CapabilityBinding:
        if "@" not in reference:
            return self.get(reference)
        capability_id, version = reference.rsplit("@", 1)
        return self.get(capability_id, version)

    def specs(self, *, include_deprecated: bool = True) -> tuple[CapabilitySpec, ...]:
        specs = [binding.spec for binding in self._bindings.values()]
        if not include_deprecated:
            specs = [spec for spec in specs if not spec.deprecated]
        return tuple(sorted(specs, key=lambda spec: (spec.id, _version_key(spec.version))))

    def manifest(self, *, include_all_versions: bool = False) -> list[dict[str, Any]]:
        if include_all_versions:
            specs = self.specs(include_deprecated=True)
        else:
            ids = sorted({spec.id for spec in self.specs(include_deprecated=False)})
            specs = tuple(self.get(capability_id).spec for capability_id in ids)
        return [self._manifest_item(spec) for spec in specs]

    @staticmethod
    def _manifest_item(spec: CapabilitySpec) -> dict[str, Any]:
        return {
            "id": spec.id,
            "version": spec.version,
            "ref": spec.ref,
            "name": spec.display_name,
            "description": spec.description,
            "objective": spec.effective_objective,
            "executor_kind": spec.executor_kind,
            "required_authority": spec.required_authority,
            "allowed_tools": list(spec.allowed_tools),
            "side_effects": list(spec.side_effects),
            "context_profile": spec.context_profile,
            "context_policy": {
                "max_tokens": spec.context_policy.max_tokens,
                "max_memory_items": spec.context_policy.max_memory_items,
                "max_artifact_items": spec.context_policy.max_artifact_items,
                "max_recent_steps": spec.context_policy.max_recent_steps,
                "min_relevance_score": spec.context_policy.min_relevance_score,
                "per_item_token_cap": spec.context_policy.per_item_token_cap,
                "allow_full_artifact": spec.context_policy.allow_full_artifact,
                "must_include": list(spec.context_policy.must_include),
                "must_exclude": list(spec.context_policy.must_exclude),
                "hybrid_weights": {
                    "semantic": spec.context_policy.hybrid_weights.semantic,
                    "recency": spec.context_policy.hybrid_weights.recency,
                    "importance": spec.context_policy.hybrid_weights.importance,
                },
            },
            "output_kind": spec.output_kind,
            "requires_artifact_kinds": list(spec.requires_artifact_kinds),
            "eligible_providers": list(spec.eligible_providers),
            "verifier_id": spec.verifier_id,
            "verification_required": spec.verification_required,
            "idempotent": spec.idempotent,
            "parallel_safe": spec.parallel_safe,
            "privacy": spec.privacy,
            "data_classification": spec.data_classification,
            "deprecated": spec.deprecated,
            "replaced_by": spec.replaced_by,
            "tags": list(spec.tags),
            "budget": {
                "max_attempts": spec.budget.max_attempts,
                "timeout_seconds": spec.budget.timeout_seconds,
                "max_context_chars": spec.budget.max_context_chars,
                "max_output_chars": spec.budget.max_output_chars,
                "max_cost_usd": spec.budget.max_cost_usd,
                "max_tool_calls": spec.budget.max_tool_calls,
            },
        }
