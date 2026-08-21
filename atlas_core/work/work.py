from __future__ import annotations

from dataclasses import dataclass


WorkId = str

UNAVAILABLE = "implementation unavailable"


class WorkError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkRecord:
    """Durable work item. Backed by the TaskRuntime store, not Chat storage."""

    id: WorkId
    objective: str
    status: str
    authority_scope: str
    capabilities: tuple[str, ...]
