from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    owner_principal_id: str
    conversation_id: str
    owner_turn_id: str
    grounding_excerpt: str
    text: str
    kind: str
    status: str
    resolution_kind: str | None
    resolution_ref: str | None
    revision: int
    satisfiable_until: str | None
    lapsed_at: str | None
    temporal_grounding_excerpt: str | None
    temporal_anchor_at: str | None
    temporal_anchor_timezone: str | None
    created_at: str
    resolved_at: str | None
    supersedes: str | None
    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class IntakeResult:
    owner_turn_id: str
    status: str
    attempts: int
    obligation_ids: tuple[str, ...]
    unmapped_spans: tuple[str, ...]
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "owner_turn_id": self.owner_turn_id,
            "status": self.status,
            "attempts": self.attempts,
            "obligation_ids": list(self.obligation_ids),
            "unmapped_spans": list(self.unmapped_spans),
            "error_code": self.error_code,
        }
