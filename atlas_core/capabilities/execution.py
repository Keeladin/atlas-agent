from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    ContextPolicy,
    DataClassification,
    ExecutionBudget,
    ExecutorKind,
    ModelOutcomePolicy,
    PrivacyRoute,
    RetryPolicy,
)
from .bindings import CapabilityBinding


_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class CapabilityExecutionProfile:
    """How this deployment performs one capability. Not capability identity."""

    capability_id: str
    implementation: CapabilityBinding | None = None
    tools: tuple[str, ...] = ()
    verifier_id: str | None = None
    verification_required: bool = True
    executor_kind: ExecutorKind = "deterministic"
    model_outcome_policy: ModelOutcomePolicy = "deliverable_only"
    version: str = "1.0.0"
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    context_policy: ContextPolicy = field(default_factory=ContextPolicy)
    context_profile: str = "execute"
    budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    output_kind: str = "capability_result"
    requires_artifact_kinds: tuple[str, ...] = ()
    eligible_providers: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    idempotent: bool = True
    parallel_safe: bool = False
    privacy: PrivacyRoute = "cloud_allowed"
    data_classification: DataClassification = "internal"
    tags: tuple[str, ...] = ()
    deprecated: bool = False
    replaced_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    name: str | None = None
    objective: str | None = None

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be empty")
        if not _SEMVER.match(self.version):
            raise ValueError("Execution profile version must be SemVer (major.minor.patch)")
        if self.executor_kind not in {"deterministic", "tool", "model", "composite", "human"}:
            raise ValueError(f"Unsupported executor kind: {self.executor_kind}")
        if self.model_outcome_policy not in {"deliverable_only", "claim_bearing"}:
            raise ValueError("Unsupported model outcome policy")
        if self.verification_required and self.executor_kind != "human" and not self.verifier_id:
            raise ValueError("Verified execution profiles must name a verifier_id.")
        if self.privacy not in {"local_only", "cloud_allowed", "cloud_preferred"}:
            raise ValueError(f"Unsupported privacy route: {self.privacy}")
        if self.data_classification not in {"public", "internal", "sensitive"}:
            raise ValueError(f"Unsupported data classification: {self.data_classification}")
        if self.deprecated and self.replaced_by is not None and not self.replaced_by.strip():
            raise ValueError("replaced_by must not be blank")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("execution profile tools must not contain duplicates")
        if self.implementation is not None and self.implementation.capability_id != self.capability_id:
            raise ValueError("execution profile implementation must match capability_id")
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
    def available(self) -> bool:
        if self.executor_kind in {"model", "human"}:
            return True
        return self.implementation is not None

    @property
    def display_name(self) -> str:
        return self.name or self.capability_id

    @property
    def ref(self) -> str:
        return f"{self.capability_id}@{self.version}"
