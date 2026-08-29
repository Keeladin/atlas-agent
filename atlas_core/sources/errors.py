from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


LocalSourceErrorCode = Literal[
    "root_unknown",
    "root_revision_unavailable",
    "root_selection_required",
    "operation_not_allowed",
    "invalid_path",
    "outside_root",
    "missing",
    "permission_denied",
    "symlink_rejected",
    "wrong_type",
    "special_object_rejected",
    "too_large",
    "unsupported_encoding",
    "unsupported_platform",
    "timeout",
    "cancelled",
    "drifted",
    "unreadable",
    "internal_invariant",
    "precondition_failed",
    "destination_conflict",
    "unsupported_object",
    "cross_device",
    "quarantine_conflict",
    "mutation_ambiguous",
    "recovery_required",
    "temporary_conflict",
    "verification_failed",
]


@dataclass
class LocalSourceError(Exception):
    code: LocalSourceErrorCode
    message: str
    root_id: str | None = None
    relative_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "root_id": self.root_id,
            "relative_path": self.relative_path,
            "details": self.details,
        }
