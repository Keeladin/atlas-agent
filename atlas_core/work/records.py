from __future__ import annotations

from dataclasses import dataclass
from typing import Any


WORK_STATUSES = (
    "planned",
    "active",
    "waiting",
    "completed",
    "failed",
    "cancelled",
)
STEP_STATUSES = (
    "pending",
    "running",
    "pass",
    "rework",
    "blocked",
    "failed",
    "skipped",
)
EXECUTION_STATUSES = (
    "running",
    "pass",
    "rework",
    "abstain",
    "fail",
    "blocked",
)
CRITERION_STATUSES = ("pending", "accepted", "rejected", "unknown")
CLAIM_KINDS = (
    "observed",
    "retrieved",
    "calculated",
    "inferred",
    "suggested",
    "executed",
)
APPROVAL_STATUSES = ("pending", "approved", "denied", "cancelled")
CONFIRMATION_STATUSES = ("pending", "confirmed", "denied", "cancelled")


@dataclass(frozen=True)
class WorkState:
    """Durable Work row. Distinct from the composition-boundary WorkRecord."""

    id: str
    objective: str
    success_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    authority_scope: str
    status: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CriterionRecord:
    id: str
    work_id: str
    ordinal: int
    text: str
    status: str
    evidence_artifact_ids: tuple[str, ...]
    note: str | None
    updated_at: str


@dataclass(frozen=True)
class StepRecord:
    id: str
    work_id: str
    ordinal: int
    description: str
    capability: str | None
    capability_version: str | None
    status: str
    dependencies: tuple[str, ...]
    input_artifact_ids: tuple[str, ...]
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    work_id: str
    step_id: str | None
    kind: str
    payload: Any
    sha256: str
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class ExecutionRecord:
    id: str
    work_id: str
    step_id: str
    capability: str
    capability_version: str
    provider: str | None
    attempt: int
    status: str
    input_artifact_ids: tuple[str, ...]
    output_artifact_ids: tuple[str, ...]
    verifier_artifact_id: str | None
    receipt: dict[str, Any]
    metrics: dict[str, Any]
    error: str | None
    started_at: str
    ended_at: str | None


@dataclass(frozen=True)
class ContextManifestRecord:
    id: str
    work_id: str
    step_id: str
    execution_id: str
    capability: str
    capability_version: str
    assembler_version: str
    budget_tokens: int
    total_tokens: int
    manifest: dict[str, Any]
    sha256: str
    created_at: str


@dataclass(frozen=True)
class CheckpointRecord:
    id: str
    work_id: str
    reason: str
    snapshot: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class ClaimRecord:
    id: str
    work_id: str
    step_id: str | None
    kind: str
    subject: str
    value: Any
    evidence_artifact_ids: tuple[str, ...]
    confidence: float | None
    created_at: str


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    work_id: str
    step_id: str | None
    required_authority: str
    requested_action: str
    status: str
    decision_note: str | None
    created_at: str
    decided_at: str | None


@dataclass(frozen=True)
class ConfirmationRecord:
    id: str
    work_id: str
    step_id: str
    capability_id: str
    payload_sha256: str
    payload: dict[str, Any]
    summary: str
    status: str
    created_at: str
    decided_at: str | None


@dataclass(frozen=True)
class EventRecord:
    id: int
    work_id: str
    step_id: str | None
    execution_id: str | None
    name: str
    payload: dict[str, Any]
    created_at: str
