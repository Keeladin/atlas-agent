from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ExecutorKind = Literal["deterministic", "tool", "model", "composite", "human"]
PrivacyRoute = Literal["local_only", "cloud_allowed", "cloud_preferred"]
DataClassification = Literal["public", "internal", "sensitive"]
ConfirmationRequirement = Literal["none", "required"]
CapabilitySideEffectClass = Literal["none", "reversible", "irreversible", "external_effect"]


@dataclass(frozen=True)
class HybridWeights:
    """Advisory retrieval weights carried by the capability contract.

    Atlas does not require vector retrieval today, but when a retrieval backend
    uses hybrid ranking these weights are part of the versioned contract rather
    than hidden prompt state.
    """

    semantic: float = 0.7
    recency: float = 0.2
    importance: float = 0.1

    def __post_init__(self) -> None:
        values = (self.semantic, self.recency, self.importance)
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("hybrid weights must each be between 0 and 1")
        if abs(sum(values) - 1.0) > 1e-9:
            raise ValueError("hybrid weights must sum to 1.0")


@dataclass(frozen=True)
class ContextPolicy:
    """Declarative context limits for one capability invocation.

    Token counts are approximate for provider-independent assembly. Exact
    provider tokenization remains provider telemetry, while this policy gives
    the assembler a deterministic pre-call ceiling.
    """

    max_tokens: int = 12_000
    max_memory_items: int = 8
    max_artifact_items: int = 6
    max_recent_steps: int = 5
    min_relevance_score: float = 0.65
    per_item_token_cap: int = 1_200
    allow_full_artifact: bool = True
    must_include: tuple[str, ...] = (
        "work.objective",
        "work.success_criteria",
        "step.description",
    )
    must_exclude: tuple[str, ...] = ("full_history", "unrelated_tasks")
    hybrid_weights: HybridWeights = field(default_factory=HybridWeights)

    def __post_init__(self) -> None:
        if self.max_tokens < 128:
            raise ValueError("context max_tokens must be >= 128")
        for name, value in (
            ("max_memory_items", self.max_memory_items),
            ("max_artifact_items", self.max_artifact_items),
            ("max_recent_steps", self.max_recent_steps),
            ("per_item_token_cap", self.per_item_token_cap),
        ):
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
        if not 0.0 <= self.min_relevance_score <= 1.0:
            raise ValueError("min_relevance_score must be between 0 and 1")

    @property
    def approximate_char_budget(self) -> int:
        return self.max_tokens * 4


@dataclass(frozen=True)
class ExecutionBudget:
    max_attempts: int = 3
    timeout_seconds: int | None = None
    max_context_chars: int = 48_000
    max_output_chars: int | None = None
    max_cost_usd: float | None = None
    max_tool_calls: int | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.max_context_chars < 1:
            raise ValueError("max_context_chars must be >= 1")
        if self.timeout_seconds is not None and self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1 when supplied")
        if self.max_output_chars is not None and self.max_output_chars < 1:
            raise ValueError("max_output_chars must be >= 1 when supplied")
        if self.max_cost_usd is not None and self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be >= 0 when supplied")
        if self.max_tool_calls is not None and self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be >= 0 when supplied")


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


class ToolSurface(Protocol):
    """Handler-facing invoke contract for a pinned per-step tool surface.

    Defined here so capabilities does not import Work modules.
    """

    def invoke(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        version: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class CapabilityRequest:
    work_id: str
    step_id: str
    capability_id: str
    context: dict[str, Any]
    input_artifact_ids: tuple[str, ...]
    attempt: int
    capability_version: str = "1.0.0"
    direct_input_artifact_ids: tuple[str, ...] = ()
    dependency_artifact_ids: tuple[str, ...] = ()
    idempotency_key: str | None = None
    surface: ToolSurface | None = None


@dataclass(frozen=True)
class CapabilityOutcome:
    status: Literal["pass", "rework", "abstain", "fail", "blocked"]
    output: Any = None
    output_kind: str | None = None
    receipt: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    claims: tuple[dict[str, Any], ...] = ()
