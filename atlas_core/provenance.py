from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Surface = Literal["chat", "work", "cadence", "control", "system"]


@dataclass(frozen=True)
class InvocationProvenance:
    """Who initiated an invocation and through which authenticated surface.

    Provenance is not authority. Owner authority is resolved exclusively by
    OwnerPolicy after the concrete operation and resource scope are known.
    """

    principal_id: str
    principal_kind: str
    surface: Surface

    def __post_init__(self) -> None:
        if not self.principal_id.strip():
            raise ValueError("principal_id must not be empty")
        if not self.principal_kind.strip():
            raise ValueError("principal_kind must not be empty")
