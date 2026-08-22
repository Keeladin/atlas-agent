from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from atlas_core.work.records import StepRecord, WorkState

from atlas_core.capabilities.execution import CapabilityExecutionProfile
from atlas_core.deliverable import (
    INTERNAL_ARTIFACT_KINDS,
    check_deliverable,
    has_quality_criteria,
    infer_deliverable,
    output_text,
)
from atlas_core.context import WorkPersistence
from atlas_core.evidence import qualifies_as_source_evidence


VerificationStatus = Literal["pass", "rework", "abstain", "fail", "blocked"]


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


Verifier = Callable[[CapabilityExecutionProfile, Any, dict[str, Any]], VerificationResult]


def _repetitive_output_reason(text: str) -> str | None:
    """Fail closed when a model answer is a loop, not a result."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 12:
        return None
    counts: dict[str, int] = {}
    hypothesis = 0
    for line in lines:
        key = " ".join(line.casefold().split())
        if "could it be" in key or key.startswith("hypothesis:"):
            hypothesis += 1
        if len(key) < 16:
            continue
        counts[key] = counts.get(key, 0) + 1
    if hypothesis >= 8:
        return "output is repetitively looping and is not a usable answer"
    if not counts:
        return None
    repeated = max(counts.values())
    if repeated >= 8 or repeated / len(lines) >= 0.35:
        return "output is repetitively looping and is not a usable answer"
    return None


class VerifierRegistry:
    def __init__(self) -> None:
        self._verifiers: dict[str, Verifier] = {}
        self.register("core.nonempty", self._nonempty)
        self.register("core.receipt", self._receipt)
        self.register("core.deliverable", self._deliverable)

    def register(self, verifier_id: str, verifier: Verifier, *, replace: bool = False) -> None:
        if verifier_id in self._verifiers and not replace:
            raise ValueError(f"Verifier already registered: {verifier_id}")
        self._verifiers[verifier_id] = verifier

    def verify(self, verifier_id: str, spec: CapabilityExecutionProfile, output: Any, context: dict[str, Any]) -> VerificationResult:
        try:
            verifier = self._verifiers[verifier_id]
        except KeyError as exc:
            raise KeyError(f"Unknown verifier: {verifier_id}") from exc
        return verifier(spec, output, context)

    @staticmethod
    def _nonempty(spec: CapabilityExecutionProfile, output: Any, context: dict[str, Any]) -> VerificationResult:
        if output is None:
            return VerificationResult("rework", "capability returned no usable output")
        if isinstance(output, str):
            text = output.strip()
            if not text:
                return VerificationResult("rework", "capability returned no usable output")
            reason = _repetitive_output_reason(text)
            if reason:
                return VerificationResult("rework", reason)
        return VerificationResult("pass", "output present")

    @staticmethod
    def _receipt(spec: CapabilityExecutionProfile, output: Any, context: dict[str, Any]) -> VerificationResult:
        receipt = context.get("execution_receipt")
        ok = isinstance(receipt, dict) and bool(receipt.get("ok"))
        return VerificationResult("pass" if ok else "fail", "side-effect receipt confirmed" if ok else "side effect lacks a successful receipt")

    @staticmethod
    def _deliverable(spec: CapabilityExecutionProfile, output: Any, context: dict[str, Any]) -> VerificationResult:
        task = context.get("task") or {}
        contract = infer_deliverable(
            str(task.get("objective") or ""),
            tuple(task.get("success_criteria") or ()),
        )
        ok, summary, produced = check_deliverable(contract, output)
        return VerificationResult(
            "pass" if ok else "rework",
            summary,
            {
                "contract": contract.as_dict(),
                "produced_type": produced,
                "capability": spec.capability_id,
            },
        )


@dataclass(frozen=True)
class CompletionDecision:
    complete: bool
    status: str
    reasons: tuple[str, ...]


def step_claims_user_criteria(step: StepRecord, spec: CapabilityExecutionProfile) -> bool:
    if step.metadata.get("internal_planning"):
        return False
    if step.metadata.get("accept_all_criteria") or spec.metadata.get("accept_all_criteria"):
        return True
    ordinals = step.metadata.get("satisfies_criteria") or spec.metadata.get("satisfies_criteria")
    return bool(ordinals)


class OutcomeGate:
    """Independent contract check after a capability produces an artifact.

    Layer 1 is deterministic: expected deliverable exists and is the right type.
    Layer 2 is an optional narrow semantic verifier. The producer does not accept
    its own homework; this gate runs after capability execution.
    """

    def __init__(self, semantic: SemanticOutcomeVerifier | None = None) -> None:
        self.semantic = semantic

    def evaluate(
        self,
        *,
        profile: CapabilityExecutionProfile,
        output: Any,
        context: dict[str, Any],
        step: "StepRecord",
        work: "WorkState",
    ) -> VerificationResult:
        if not step_claims_user_criteria(step, profile):
            return VerificationResult("pass", "step does not claim work success criteria")
        contract = infer_deliverable(work.objective, work.success_criteria)
        ok, summary, produced = check_deliverable(contract, output)
        details = {
            "contract": contract.as_dict(),
            "produced_type": produced,
            "layer": "deterministic",
        }
        if not ok:
            return VerificationResult("rework", summary, details)
        if (
            self.semantic is not None
            and profile.executor_kind == "model"
            and (
                contract.requires_semantic_check
                or has_quality_criteria(work.success_criteria)
            )
        ):
            semantic = self.semantic.verify(profile, output, context, contract=contract, work=work)
            details["semantic"] = {
                "status": semantic.status,
                "summary": semantic.summary,
                **semantic.details,
            }
            if semantic.status in {"rework", "fail"}:
                details["layer"] = "semantic"
                return VerificationResult(semantic.status, semantic.summary, details)
            if semantic.status == "abstain" and has_quality_criteria(work.success_criteria):
                details["layer"] = "semantic"
                return VerificationResult("abstain", semantic.summary, details)
            if semantic.status == "pass":
                details["layer"] = "semantic"
                return VerificationResult("pass", semantic.summary, details)
        return VerificationResult("pass", summary, details)


class SemanticOutcomeVerifier:
    """Narrow independent check that the artifact is the thing the user asked for."""

    _SYSTEM = (
        "You verify whether an artifact satisfies the user's requested deliverable. "
        "Return only one JSON object with keys status, requested_type, produced_type, summary. "
        "status must be pass or rework. "
        "pass only if the artifact is the requested thing itself. "
        "If the user asked for a story, letter, or other composition and the artifact "
        "is analysis, investigation notes, or a report about the request, status is rework. "
        "Do not rewrite the artifact."
    )

    def __init__(self, model_router: Any) -> None:
        self.model_router = model_router

    def verify(
        self,
        spec: CapabilityExecutionProfile,
        output: Any,
        context: dict[str, Any],
        *,
        contract: Any,
        work: "WorkState",
    ) -> VerificationResult:
        from atlas_core.providers import ModelRequest, ModelRoutingError

        payload = {
            "objective": work.objective,
            "success_criteria": list(work.success_criteria),
            "deliverable_contract": contract.as_dict(),
            "artifact": output_text(output)[:8_000],
        }
        prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        try:
            route = self.model_router.select(
                spec.capability_id,
                context_chars=len(prompt),
                privacy=spec.privacy,
                eligible_providers=spec.eligible_providers,
            )
            response = route.provider.generate(
                ModelRequest(
                    spec.capability_id,
                    self._SYSTEM,
                    prompt,
                    max_output_chars=min(2_000, spec.budget.max_output_chars or 2_000),
                    metadata={
                        "purpose": "outcome_verification",
                        "work_id": work.id,
                    },
                )
            )
        except ModelRoutingError as exc:
            return VerificationResult(
                "abstain",
                f"outcome verifier could not be routed: {exc}",
                {"layer": "semantic"},
            )
        except Exception as exc:
            return VerificationResult(
                "abstain",
                f"outcome verifier failed: {exc}",
                {"layer": "semantic"},
            )
        parsed = _parse_outcome_verdict(response.text)
        if parsed is None:
            return VerificationResult(
                "abstain",
                "outcome verifier returned unusable JSON",
                {"layer": "semantic", "raw": response.text[:500]},
            )
        status = parsed.get("status")
        if status not in {"pass", "rework"}:
            return VerificationResult(
                "abstain",
                "outcome verifier returned an invalid status",
                {"layer": "semantic", "raw": parsed},
            )
        summary = str(parsed.get("summary") or "").strip() or (
            "artifact satisfies the user contract"
            if status == "pass"
            else "artifact does not satisfy the user contract"
        )
        return VerificationResult(
            status,
            summary,
            {
                "layer": "semantic",
                "requested_type": parsed.get("requested_type"),
                "produced_type": parsed.get("produced_type"),
            },
        )


class GroundedCriterionVerifier:
    """Independent semantic coverage check; its verdict is not source evidence."""

    _SYSTEM = (
        "Independently verify whether the supplied grounded claims and their source "
        "evidence are sufficient to satisfy the exact success criterion. Return only "
        "JSON with status and summary. status must be pass, rework, abstain, or fail. "
        "Use fail only when the evidence contradicts the criterion or proves it impossible. "
        "A single grounded claim is not sufficient unless it actually covers the whole "
        "criterion. The generated deliverable and producer confidence are not evidence."
    )

    def __init__(self, model_router: Any) -> None:
        self.model_router = model_router

    def verify(self, profile: CapabilityExecutionProfile, document: dict[str, Any]) -> VerificationResult:
        from atlas_core.providers import ModelRequest, ModelRoutingError

        prompt = json.dumps(document, ensure_ascii=False, sort_keys=True)
        try:
            route = self.model_router.select(
                profile.capability_id,
                context_chars=len(prompt),
                privacy=profile.privacy,
                eligible_providers=profile.eligible_providers,
            )
            response = route.provider.generate(ModelRequest(
                profile.capability_id,
                self._SYSTEM,
                prompt,
                max_output_chars=2_000,
                metadata={"purpose": "grounded_criterion_verification"},
            ))
        except ModelRoutingError as exc:
            return VerificationResult("abstain", f"criterion verifier could not be routed: {exc}")
        except Exception as exc:
            return VerificationResult("abstain", f"criterion verifier failed: {exc}")
        parsed = _parse_outcome_verdict(response.text)
        if parsed is None or parsed.get("status") not in {"pass", "rework", "abstain", "fail"}:
            return VerificationResult("abstain", "criterion verifier returned unusable JSON")
        return VerificationResult(
            parsed["status"],
            str(parsed.get("summary") or "criterion verification completed"),
            {"criterion_ordinal": document.get("criterion", {}).get("ordinal")},
        )


def _parse_outcome_verdict(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


class CompletionVerifier:
    """Work completion gate independent from any model's claim of completion."""

    def __init__(self, store: WorkPersistence) -> None:
        self.store = store

    def evaluate(self, work_id: str) -> CompletionDecision:
        reasons: list[str] = []
        work = self.store.get_work(work_id)
        steps = self.store.list_steps(work_id)
        criteria = self.store.list_criteria(work_id)
        pending_approvals = self.store.list_approvals(work_id, status="pending")
        if not steps:
            reasons.append("work has no executable steps")
        nonterminal_steps = [step.id for step in steps if step.status not in {"pass", "skipped"}]
        if nonterminal_steps:
            reasons.append(f"steps not accepted: {nonterminal_steps}")
        rejected = [criterion.text for criterion in criteria if criterion.status == "rejected"]
        if rejected:
            return CompletionDecision(False, "failed", tuple([f"rejected criteria: {rejected}"]))
        unresolved = [criterion.text for criterion in criteria if criterion.status != "accepted"]
        if unresolved:
            reasons.append(f"criteria not accepted: {unresolved}")
        if pending_approvals:
            reasons.append(f"pending approvals: {[item.id for item in pending_approvals]}")
        for criterion in criteria:
            if criterion.status != "accepted" or criterion.satisfaction_policy != "evidence_grounded":
                continue
            if criterion.semantic_verification == "required" and not criterion.verification_artifact_id:
                reasons.append(f"grounded criterion lacks decisive verification: {criterion.text}")
                continue
            history = self.store.list_criterion_verifications(criterion.id)
            decisive = next(
                (item for item in history if item.artifact_id == criterion.verification_artifact_id),
                None,
            )
            if decisive is None or decisive.status != "pass":
                reasons.append(f"grounded criterion lacks a passing decisive verdict: {criterion.text}")
            claims = self.store.criterion_claims(criterion.id)
            source_ids = {
                artifact_id for claim in claims
                if claim.kind in {"observed", "retrieved", "calculated", "executed"}
                for artifact_id in claim.evidence_artifact_ids
                if qualifies_as_source_evidence(self.store.get_artifact(artifact_id))
            }
            if not criterion.evidence_artifact_ids or not set(criterion.evidence_artifact_ids).issubset(source_ids):
                reasons.append(f"grounded criterion evidence is not backed by linked claims: {criterion.text}")
        if reasons:
            return CompletionDecision(False, "incomplete", tuple(reasons))
        mismatch = self._deliverable_mismatch(work)
        if mismatch:
            return CompletionDecision(False, "failed", (mismatch,))
        return CompletionDecision(True, "complete", ())

    def _deliverable_mismatch(self, work: "WorkState") -> str | None:
        contract = infer_deliverable(work.objective, work.success_criteria)
        if not contract.is_user_facing:
            return None
        evidence_ids: list[str] = []
        for criterion in self.store.list_criteria(work.id):
            if criterion.status == "accepted":
                evidence_ids.extend(criterion.evidence_artifact_ids)
        payloads: list[Any] = []
        for artifact_id in dict.fromkeys(evidence_ids):
            artifact = self.store.get_artifact(artifact_id)
            if artifact.kind in INTERNAL_ARTIFACT_KINDS:
                continue
            payloads.append(artifact.payload)
        if payloads and any(check_deliverable(contract, payload)[0] for payload in payloads):
            return None
        return (
            "accepted evidence does not satisfy the requested deliverable: "
            f"{contract.must_produce}"
        )
