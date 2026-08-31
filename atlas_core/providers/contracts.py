from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ModelContentPart:
    """Provider-neutral multimodal input carried across the model boundary."""

    kind: Literal["text", "image", "document"]
    text: str | None = None
    data: bytes | None = None
    media_type: str | None = None
    source_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def text_part(cls, text: str, *, source_ref: str | None = None) -> "ModelContentPart":
        return cls(kind="text", text=text, source_ref=source_ref)

    @classmethod
    def binary(cls, kind: Literal["image", "document"], data: bytes, media_type: str, *, source_ref: str | None = None, metadata: dict[str, Any] | None = None) -> "ModelContentPart":
        return cls(kind=kind, data=data, media_type=media_type, source_ref=source_ref, metadata=dict(metadata or {}))


@dataclass(frozen=True)
class ModelRequest:
    capability_id: str
    system: str
    input: str
    content: tuple[ModelContentPart, ...] = ()
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

    def estimate_cost_usd(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> float | None:
        if (
            self.input_cost_per_million is None
            or self.output_cost_per_million is None
        ):
            return None
        return (
            max(0, input_tokens) * self.input_cost_per_million
            + max(0, output_tokens) * self.output_cost_per_million
        ) / 1_000_000.0


class ModelProvider(Protocol):
    spec: ProviderSpec

    def generate(self, request: ModelRequest) -> ModelResponse: ...
