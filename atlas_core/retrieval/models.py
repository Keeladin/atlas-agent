from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EmbeddingSpec:
    provider: str
    model: str
    model_revision: str
    package_version: str
    dimensions: int
    normalization: str
    representation_version: str = "text-semantic@1"

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @property
    def identity(self) -> tuple[Any, ...]:
        return (
            self.provider, self.model, self.model_revision, self.package_version,
            self.dimensions, self.normalization, self.representation_version,
        )


@dataclass(frozen=True)
class RankedCandidate:
    item_id: str
    rank: int
    source: str
    raw_score: float | None = None


@dataclass(frozen=True)
class FusionResult:
    item_id: str
    score: float
    ranks: dict[str, int]
