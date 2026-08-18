from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelRequest:
    capability_id: str
    system: str
    input: str
    max_output_chars: int | None = None
    temperature: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    text: str
    provider_key: str
    model: str
    raw: dict[str, Any]
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    model: str
    provider_kind: str
    capabilities: dict[str, float]
    local: bool = False
    enabled: bool = True
    max_context_chars: int = 128_000
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    latency_rank: int = 50
    priority: int = 50
    metadata: dict[str, Any] = field(default_factory=dict)

    def score_for(self, capability_id: str) -> float | None:
        value = self.capabilities.get(capability_id)
        return None if value is None else float(value)


class ModelProvider(Protocol):
    spec: ProviderSpec

    def generate(self, request: ModelRequest) -> ModelResponse: ...
