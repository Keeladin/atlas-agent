from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MemoryState = Literal["active", "superseded", "retracted"]


@dataclass(frozen=True)
class MemoryItem:
    item_id: str
    principal_id: str
    title: str
    content: str
    grounding_excerpt: str | None
    source_ref: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    state: MemoryState = "active"
    supersedes: str | None = None
    created_at: str = ""
    updated_at: str = ""
    retracted_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "principal_id": self.principal_id,
            "title": self.title,
            "content": self.content,
            "grounding_excerpt": self.grounding_excerpt,
            "source_ref": self.source_ref,
            "metadata": self.metadata,
            "state": self.state,
            "supersedes": self.supersedes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retracted_at": self.retracted_at,
        }
