from __future__ import annotations

from pathlib import Path

from atlas_core.capabilities.awareness import CapabilityAwareness, brief_catalog
from atlas_core.providers import (
    ModelProvider,
    ModelRequest,
    ProviderRegistry,
    load_provider_registry,
)

from .brief import TaskBrief, assemble_brief
from .intent import interpret, parse_brief_payload
from .prompts import build_model_input, build_system_prompt


_BRIEF_LABEL = "advanced.brief"
_MAX_OUTPUT_CHARS = 4_096


class AdvancedError(RuntimeError):
    pass


class AdvancedRuntime:
    """Intent and Task Brief runtime. Does not own Work, tools, or execution."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        catalog: tuple[CapabilityAwareness, ...],
    ) -> None:
        self._provider = provider
        self._catalog = catalog
        self._system = build_system_prompt(catalog)

    def brief(self, objective: str, *, notes: str | None = None) -> TaskBrief:
        intent = interpret(objective, notes=notes)
        response = self._provider.generate(
            ModelRequest(
                capability_id=_BRIEF_LABEL,
                system=self._system,
                input=build_model_input(intent),
                max_output_chars=_MAX_OUTPUT_CHARS,
            )
        )
        try:
            payload = parse_brief_payload(response.text)
            return assemble_brief(
                objective=payload["objective"] or intent.objective,
                capability_ids=payload["capabilities"],
                catalog=self._catalog,
                expected_effect=payload["expected_effect"] or None,
                constraints=payload["constraints"],
                deliverable_kind=payload["deliverable_kind"],
                notes=payload["notes"] or intent.notes,
            )
        except ValueError as exc:
            raise AdvancedError(str(exc)) from exc


def build_advanced_runtime(
    *,
    provider_config: str | Path | None = None,
    provider: ModelProvider | None = None,
) -> AdvancedRuntime:
    """Only composition root for AdvancedRuntime."""

    chosen = provider
    if chosen is None:
        if provider_config is None:
            raise AdvancedError("AdvancedRuntime requires a model provider.")
        chosen = _enabled_provider(load_provider_registry(provider_config))
    return AdvancedRuntime(provider=chosen, catalog=brief_catalog())


def _enabled_provider(registry: ProviderRegistry) -> ModelProvider:
    enabled = [item for item in registry.providers() if item.spec.enabled]
    if not enabled:
        raise AdvancedError("AdvancedRuntime requires an enabled model provider.")
    return max(enabled, key=lambda item: (item.spec.priority, -item.spec.latency_rank))
