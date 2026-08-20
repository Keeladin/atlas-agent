from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from atlas_core.authority import validate_authority


ExecutorKind = Literal["deterministic", "tool", "model", "composite", "human"]
PrivacyRoute = Literal["local_only", "cloud_allowed", "cloud_preferred"]
DataClassification = Literal["public", "internal", "sensitive"]
ConfirmationRequirement = Literal["none", "required"]
CapabilitySideEffectClass = Literal["none", "reversible", "irreversible", "external_effect"]

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


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
        "task.objective",
        "task.success_criteria",
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


@dataclass(frozen=True)
class CapabilitySpec:
    """Versioned Atlas capability contract: stable product meaning.

    This is the reconciled form of the advisory's Specialist Contract: Atlas
    retains one persistent identity and specialist behaviour is expressed as a
    bounded capability execution profile instead of a second agent ontology.

    This spec does not encode vendor tool names, MCP discovery, provider
    selection, or mode permissions. Deployment implementations live on
    ``CapabilityBinding``. ``allowed_tools`` remains a runtime execution-frame
    allow-list of ToolDescriptor refs, not capability identity.
    """

    id: str
    description: str
    executor_kind: ExecutorKind
    version: str = "1.0.0"
    name: str | None = None
    objective: str | None = None
    required_authority: str = "read"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    output_kind: str = "capability_result"
    requires_artifact_kinds: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    side_effect_class: CapabilitySideEffectClass | None = None
    confirmation: ConfirmationRequirement = "none"
    context_profile: str = "execute"
    context_policy: ContextPolicy = field(default_factory=ContextPolicy)
    eligible_providers: tuple[str, ...] = ()
    verifier_id: str | None = None
    verification_required: bool = True
    idempotent: bool = True
    parallel_safe: bool = False
    privacy: PrivacyRoute = "cloud_allowed"
    data_classification: DataClassification = "internal"
    budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    tags: tuple[str, ...] = ()
    deprecated: bool = False
    replaced_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Capability id must not be empty.")
        if not self.description.strip():
            raise ValueError("Capability description must not be empty.")
        if not _SEMVER.match(self.version):
            raise ValueError("Capability version must be SemVer (major.minor.patch).")
        validate_authority(self.required_authority)
        if self.executor_kind not in {"deterministic", "tool", "model", "composite", "human"}:
            raise ValueError(f"Unsupported executor kind: {self.executor_kind}")
        if self.verification_required and self.executor_kind != "human" and not self.verifier_id:
            raise ValueError("Verified capabilities must name a verifier_id.")
        if self.privacy not in {"local_only", "cloud_allowed", "cloud_preferred"}:
            raise ValueError(f"Unsupported privacy route: {self.privacy}")
        if self.data_classification not in {"public", "internal", "sensitive"}:
            raise ValueError(f"Unsupported data classification: {self.data_classification}")
        if self.deprecated and self.replaced_by is not None and not self.replaced_by.strip():
            raise ValueError("replaced_by must not be blank")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("allowed_tools must not contain duplicates")
        if self.confirmation not in {"none", "required"}:
            raise ValueError(f"Unsupported confirmation requirement: {self.confirmation}")
        if self.side_effect_class is not None and self.side_effect_class not in {
            "none", "reversible", "irreversible", "external_effect",
        }:
            raise ValueError(f"Unsupported side_effect_class: {self.side_effect_class}")
        # Preserve the older explicit char budget as a hard upper safety ceiling
        # while context_policy becomes the declarative source of assembly policy.
        if self.context_policy.approximate_char_budget > self.budget.max_context_chars:
            object.__setattr__(
                self,
                "context_policy",
                ContextPolicy(
                    max_tokens=max(128, self.budget.max_context_chars // 4),
                    max_memory_items=self.context_policy.max_memory_items,
                    max_artifact_items=self.context_policy.max_artifact_items,
                    max_recent_steps=self.context_policy.max_recent_steps,
                    min_relevance_score=self.context_policy.min_relevance_score,
                    per_item_token_cap=self.context_policy.per_item_token_cap,
                    allow_full_artifact=self.context_policy.allow_full_artifact,
                    must_include=self.context_policy.must_include,
                    must_exclude=self.context_policy.must_exclude,
                    hybrid_weights=self.context_policy.hybrid_weights,
                ),
            )

    @property
    def display_name(self) -> str:
        return self.name or self.id

    @property
    def effective_objective(self) -> str:
        return self.objective or self.description

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def effective_side_effect_class(self) -> str:
        if self.side_effect_class is not None:
            return self.side_effect_class
        if not self.side_effects:
            return "none"
        return "reversible" if self.idempotent else "irreversible"


@dataclass(frozen=True)
class CapabilityRequest:
    task_id: str
    step_id: str
    capability_id: str
    context: dict[str, Any]
    input_artifact_ids: tuple[str, ...]
    attempt: int
    capability_version: str = "1.0.0"
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
