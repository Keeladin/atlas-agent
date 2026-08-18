from __future__ import annotations

from dataclasses import dataclass


AUTHORITY_LEVELS = (
    "read",
    "interpret",
    "recommend",
    "modify_internal",
    "communicate",
    "execute_external",
)
_AUTHORITY_RANK = {name: index for index, name in enumerate(AUTHORITY_LEVELS)}


class AuthorityError(PermissionError):
    """Raised when a task does not have authority for a capability."""


def validate_authority(level: str) -> str:
    normalized = level.strip()
    if normalized not in _AUTHORITY_RANK:
        raise ValueError(f"Unsupported authority level: {level!r}")
    return normalized


def authority_allows(granted: str, required: str) -> bool:
    granted = validate_authority(granted)
    required = validate_authority(required)
    return _AUTHORITY_RANK[granted] >= _AUTHORITY_RANK[required]


@dataclass(frozen=True)
class AuthorityDecision:
    granted: str
    required: str
    allowed: bool
    reason: str


def decide_authority(granted: str, required: str) -> AuthorityDecision:
    granted = validate_authority(granted)
    required = validate_authority(required)
    allowed = authority_allows(granted, required)
    return AuthorityDecision(
        granted=granted,
        required=required,
        allowed=allowed,
        reason=(
            "authority satisfied"
            if allowed
            else f"capability requires {required}; task grants {granted}"
        ),
    )


def require_authority(granted: str, required: str) -> AuthorityDecision:
    decision = decide_authority(granted, required)
    if not decision.allowed:
        raise AuthorityError(decision.reason)
    return decision
