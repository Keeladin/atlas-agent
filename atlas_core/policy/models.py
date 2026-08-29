from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PolicyDecision = Literal["NO", "YES", "CONFIRM"]
VALID_DECISIONS = frozenset({"NO", "YES", "CONFIRM"})


def normalize_scope(value: str) -> str:
    raw = str(value or "").strip().strip("/")
    if not raw:
        raise ValueError("scope must not be empty")
    parts = raw.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("scope contains invalid path segments")
    return "/".join(parts)


def normalize_operation(value: str) -> str:
    operation = str(value or "").strip()
    if not operation:
        raise ValueError("operation must not be empty")
    if operation != "*" and any(char.isspace() for char in operation):
        raise ValueError("operation must not contain whitespace")
    return operation


@dataclass(frozen=True)
class PolicyRule:
    event_id: str
    sequence: int
    principal_id: str
    scope: str
    operation: str
    decision: PolicyDecision
    reason: str | None
    created_at: str


@dataclass(frozen=True)
class PolicyResolution:
    principal_id: str
    scope: str
    operation: str
    decision: PolicyDecision
    revision: int
    matched_scope: str | None = None
    matched_operation: str | None = None
    event_id: str | None = None
    defaulted: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "principal_id": self.principal_id,
            "scope": self.scope,
            "operation": self.operation,
            "decision": self.decision,
            "revision": self.revision,
            "matched_scope": self.matched_scope,
            "matched_operation": self.matched_operation,
            "event_id": self.event_id,
            "defaulted": self.defaulted,
        }
