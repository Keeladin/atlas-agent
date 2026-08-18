from __future__ import annotations

from dataclasses import dataclass

from atlas_core.capabilities.contracts import CapabilitySpec
from .contracts import ModelProvider
from .registry import ProviderRegistry


class ModelRoutingError(RuntimeError):
    pass


@dataclass(frozen=True)
class RouteDecision:
    provider: ModelProvider
    reason: str
    score: tuple[float, int, int, int, int]


class ModelRouter:
    """Route model work by measured capability competence and operational constraints."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry
        self._score_overrides: dict[tuple[str, str], float] = {}

    def record_eval_score(self, provider_key: str, capability_id: str, score: float) -> None:
        if not 0.0 <= score <= 1.0:
            raise ValueError("Eval score must be between 0 and 1.")
        self.registry.get(provider_key)
        self._score_overrides[(provider_key, capability_id)] = float(score)

    def competence(self, provider: ModelProvider, capability_id: str) -> float | None:
        override = self._score_overrides.get((provider.spec.key, capability_id))
        return override if override is not None else provider.spec.score_for(capability_id)

    def select(self, capability: CapabilitySpec, *, context_chars: int, exclude_provider_keys: tuple[str, ...] = ()) -> RouteDecision:
        candidates: list[tuple[tuple[float, int, int, int, int], ModelProvider]] = []
        allowlist = set(capability.eligible_providers)
        excluded = set(exclude_provider_keys)
        for provider in self.registry.providers():
            spec = provider.spec
            if not spec.enabled or spec.key in excluded:
                continue
            if allowlist and spec.key not in allowlist:
                continue
            if context_chars > spec.max_context_chars:
                continue
            if capability.privacy == "local_only" and not spec.local:
                continue
            competence = self.competence(provider, capability.id)
            if competence is None:
                continue
            locality = 1 if spec.local else 0
            cloud_preference = 1 if capability.privacy == "cloud_preferred" and not spec.local else 0
            score = (competence, cloud_preference, spec.priority, locality, -spec.latency_rank)
            candidates.append((score, provider))
        if not candidates:
            raise ModelRoutingError(f"No provider satisfies capability {capability.id!r} and its constraints.")
        score, provider = max(candidates, key=lambda item: item[0])
        return RouteDecision(
            provider=provider,
            reason=f"capability={capability.id}; competence={score[0]:.3f}; privacy={capability.privacy}; context_chars={context_chars}",
            score=score,
        )
