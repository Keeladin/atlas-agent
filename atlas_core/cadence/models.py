from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class Cadence:
    cadence_id: str
    name: str
    objective: str
    schedule: dict[str, Any]
    steps: list[dict[str, Any]]
    owner_principal_id: str
    enabled: bool
    next_run_at: str | None
    last_run_at: str | None
    last_work_id: str | None
    created_at: str
    updated_at: str
    kind: str = "work_template"
    intake_root_id: str | None = None
    max_candidates: int = 25
    last_result: dict[str, Any] | None = None
    def as_dict(self) -> dict[str, Any]: return self.__dict__.copy()
