from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from atlas_core.authority import validate_authority


ExecutorKind = Literal["deterministic", "tool", "model", "composite", "human"]


@dataclass(frozen=True)
class ExecutionBudget:
    max_attempts: int = 3
    timeout_seconds: int | None = None
    max_context_chars: int = 48_000
    max_output_chars: int | None = None
    max_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.max_context_chars < 1:
            raise ValueError("max_context_chars must be >= 1")


@dataclass(frozen=True)
class RetryPolicy:
    retry_on: tuple[str, ...] = ("rework", "abstain")
    stop_on: tuple[str, ...] = ("pass", "fail", "blocked")

    def __post_init__(self) -> None:
        valid = {"pass", "rework", "abstain", "fail", "blocked"}
        retry = set(self.retry_on)
        stop = set(self.stop_on)
        unknown = (retry | stop) - valid
        if unknown:
            raise ValueError(f"Unknown retry-policy statuses: {sorted(unknown)}")
        if "pass" in retry or "fail" in retry:
            raise ValueError("pass/fail are terminal and cannot be automatic retry states")
        if retry & stop:
            raise ValueError("retry_on and stop_on must not overlap")


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    description: str
    executor_kind: ExecutorKind
    required_authority: str = "read"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    output_kind: str = "capability_result"
    side_effects: tuple[str, ...] = ()
    context_profile: str = "execute"
    eligible_providers: tuple[str, ...] = ()
    verifier_id: str | None = None
    verification_required: bool = True
    idempotent: bool = True
    parallel_safe: bool = False
    privacy: Literal["local_only", "cloud_allowed", "cloud_preferred"] = "cloud_allowed"
    budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Capability id must not be empty.")
        if not self.description.strip():
            raise ValueError("Capability description must not be empty.")
        validate_authority(self.required_authority)
        if self.executor_kind not in {"deterministic", "tool", "model", "composite", "human"}:
            raise ValueError(f"Unsupported executor kind: {self.executor_kind}")
        if self.verification_required and self.executor_kind != "human" and not self.verifier_id:
            raise ValueError("Verified capabilities must name a verifier_id.")


@dataclass(frozen=True)
class CapabilityRequest:
    task_id: str
    step_id: str
    capability_id: str
    context: dict[str, Any]
    input_artifact_ids: tuple[str, ...]
    attempt: int
    direct_input_artifact_ids: tuple[str, ...] = ()
    dependency_artifact_ids: tuple[str, ...] = ()
    idempotency_key: str | None = None


@dataclass(frozen=True)
class CapabilityOutcome:
    status: Literal["pass", "rework", "abstain", "fail", "blocked"]
    output: Any = None
    output_kind: str | None = None
    receipt: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    claims: tuple[dict[str, Any], ...] = ()
