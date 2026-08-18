from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from atlas_core.capabilities.contracts import CapabilitySpec
from atlas_core.tasks.store import TaskStore


VerificationStatus = Literal["pass", "rework", "abstain", "fail", "blocked"]


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


Verifier = Callable[[CapabilitySpec, Any, dict[str, Any]], VerificationResult]


class VerifierRegistry:
    def __init__(self) -> None:
        self._verifiers: dict[str, Verifier] = {}
        self.register("core.nonempty", self._nonempty)
        self.register("core.receipt", self._receipt)

    def register(self, verifier_id: str, verifier: Verifier, *, replace: bool = False) -> None:
        if verifier_id in self._verifiers and not replace:
            raise ValueError(f"Verifier already registered: {verifier_id}")
        self._verifiers[verifier_id] = verifier

    def verify(self, verifier_id: str, spec: CapabilitySpec, output: Any, context: dict[str, Any]) -> VerificationResult:
        try:
            verifier = self._verifiers[verifier_id]
        except KeyError as exc:
            raise KeyError(f"Unknown verifier: {verifier_id}") from exc
        return verifier(spec, output, context)

    @staticmethod
    def _nonempty(spec: CapabilitySpec, output: Any, context: dict[str, Any]) -> VerificationResult:
        ok = output is not None and (not isinstance(output, str) or bool(output.strip()))
        return VerificationResult("pass" if ok else "rework", "output present" if ok else "capability returned no usable output")

    @staticmethod
    def _receipt(spec: CapabilitySpec, output: Any, context: dict[str, Any]) -> VerificationResult:
        receipt = context.get("execution_receipt")
        ok = isinstance(receipt, dict) and bool(receipt.get("ok"))
        return VerificationResult("pass" if ok else "fail", "side-effect receipt confirmed" if ok else "side effect lacks a successful receipt")


@dataclass(frozen=True)
class CompletionDecision:
    complete: bool
    status: str
    reasons: tuple[str, ...]


class CompletionVerifier:
    """Task completion gate independent from any model's claim of completion."""

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def evaluate(self, task_id: str) -> CompletionDecision:
        reasons: list[str] = []
        steps = self.store.list_steps(task_id)
        criteria = self.store.list_criteria(task_id)
        pending_approvals = self.store.list_approvals(task_id, status="pending")
        if not steps:
            reasons.append("task has no executable steps")
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
        if reasons:
            return CompletionDecision(False, "incomplete", tuple(reasons))
        return CompletionDecision(True, "complete", ())
