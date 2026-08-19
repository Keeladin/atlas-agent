from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from atlas_core.authority import authority_allows
from atlas_core.capabilities import CapabilityOutcome, CapabilityRegistryError, CapabilityRequest
from atlas_core.context import ContextBuilder
from atlas_core.events import RuntimeEvent
from atlas_core.providers import ModelRequest, ModelRoutingError
from atlas_core.tasks import InvalidTransitionError, StepRecord
from atlas_core.schema_validation import SchemaValidationError, validate_json
from atlas_core.verification import VerificationResult
from .runtime_types import RuntimeResult, RecoveryResult

class RuntimeFinishMixin:
    def _finish_frame(
        self,
        step: StepRecord,
        spec: Any,
        execution_id: str,
        context: dict[str, Any],
        outcome: CapabilityOutcome,
    ) -> None:
        if outcome.status == "pass" and spec.output_schema:
            try:
                validate_json(outcome.output, spec.output_schema, path="$.output")
            except SchemaValidationError as exc:
                outcome = CapabilityOutcome(
                    "rework",
                    output=outcome.output,
                    output_kind=outcome.output_kind,
                    receipt=outcome.receipt,
                    metrics=outcome.metrics,
                    error=f"output schema validation failed: {exc}",
                    claims=outcome.claims,
                )
                self._emit(
                    step.task_id,
                    "capability.output_schema_failed",
                    step_id=step.id,
                    execution_id=execution_id,
                    payload={
                        "capability": spec.id,
                        "capability_version": spec.version,
                        "error": str(exc),
                    },
                )

        if (
            outcome.status == "pass"
            and spec.side_effects
            and not (
                isinstance(outcome.receipt, dict)
                and outcome.receipt.get("ok") is True
            )
        ):
            outcome = CapabilityOutcome(
                "fail",
                output=outcome.output,
                output_kind=outcome.output_kind,
                receipt=outcome.receipt,
                metrics=outcome.metrics,
                error="side-effecting capability returned no successful receipt",
                claims=outcome.claims,
            )
            self._emit(
                step.task_id,
                "side_effect.unverified",
                step_id=step.id,
                execution_id=execution_id,
                payload={"capability": spec.id},
            )

        output_ids: list[str] = []
        if outcome.output is not None:
            size = len(
                json.dumps(
                    outcome.output,
                    ensure_ascii=False,
                    default=str,
                )
            )
            if (
                spec.budget.max_output_chars is not None
                and size > spec.budget.max_output_chars
            ):
                outcome = CapabilityOutcome(
                    "rework",
                    receipt=outcome.receipt,
                    metrics=outcome.metrics,
                    error=(
                        "output exceeds explicit budget: "
                        f"{size}>{spec.budget.max_output_chars}"
                    ),
                    claims=outcome.claims,
                )
            else:
                artifact = self.store.put_artifact(
                    step.task_id,
                    step_id=step.id,
                    kind=outcome.output_kind or spec.output_kind,
                    payload=outcome.output,
                    metadata={
                        "capability": spec.id,
                        "execution_id": execution_id,
                        "outcome_status": outcome.status,
                    },
                )
                output_ids.append(artifact.id)

        receipt_artifact_id: str | None = None
        if outcome.receipt:
            receipt_artifact = self.store.put_artifact(
                step.task_id,
                step_id=step.id,
                kind="execution_receipt",
                payload=outcome.receipt,
                metadata={
                    "capability": spec.id,
                    "execution_id": execution_id,
                },
            )
            receipt_artifact_id = receipt_artifact.id

        verifier_artifact_id: str | None = None
        verification: VerificationResult | None = None
        final_status = outcome.status
        verification_context = dict(context)
        verification_context["execution_receipt"] = outcome.receipt
        details: dict[str, Any] = {}
        gate: VerificationResult | None = None
        if outcome.status == "pass" and spec.verification_required:
            try:
                verification = self.verifiers.verify(
                    spec.verifier_id or "",
                    spec,
                    outcome.output,
                    verification_context,
                )
            except Exception as exc:
                verification = VerificationResult("fail", f"verifier failed: {exc}")
            details["capability_verifier"] = {
                "status": verification.status,
                "summary": verification.summary,
                **verification.details,
            }
            if verification.status != "pass":
                final_status = verification.status
        if final_status == "pass":
            task = self.store.get_task(step.task_id)
            gate = self.outcome_gate.evaluate(
                spec=spec,
                output=outcome.output,
                context=verification_context,
                step=step,
                task=task,
            )
            details["outcome_gate"] = {
                "status": gate.status,
                "summary": gate.summary,
                **gate.details,
            }
            if gate.status != "pass":
                verification = gate
                final_status = gate.status
        persist_verification = verification is not None and (
            spec.verification_required or verification.status != "pass"
        )
        gate_details = details.get("outcome_gate") or {}
        if gate is not None and (
            gate_details.get("layer") == "semantic" or "semantic" in gate_details
        ):
            persist_verification = True
            if verification is None:
                verification = gate
        if persist_verification:
            verifier_payload = {
                "status": verification.status if verification is not None else final_status,
                "summary": (
                    verification.summary
                    if verification is not None
                    else "verification completed"
                ),
                "details": details or (verification.details if verification is not None else {}),
            }
            verifier = self.store.put_artifact(
                step.task_id,
                step_id=step.id,
                kind="verification_result",
                payload=verifier_payload,
                metadata={
                    "verifier_id": spec.verifier_id,
                    "execution_id": execution_id,
                    "outcome_gate": True,
                },
            )
            verifier_artifact_id = verifier.id
            self._emit(
                step.task_id,
                "verification.completed",
                step_id=step.id,
                execution_id=execution_id,
                payload=verifier_payload,
            )

        execution_error = outcome.error
        if verification is not None and verification.status != "pass":
            execution_error = execution_error or verification.summary
        execution = self.store.finish_execution(
            execution_id,
            status=final_status,
            output_artifact_ids=output_ids,
            verifier_artifact_id=verifier_artifact_id,
            receipt=outcome.receipt,
            metrics=outcome.metrics,
            error=execution_error,
        )

        if final_status in spec.retry_policy.retry_on:
            if execution.attempt >= spec.budget.max_attempts:
                current_step = self.store.get_step(step.id)
                if current_step.status not in {"failed", "pass", "skipped"}:
                    self.store.set_step_status(step.id, "failed")
                self._emit(
                    step.task_id,
                    "retry.exhausted",
                    step_id=step.id,
                    execution_id=execution_id,
                    payload={"max_attempts": spec.budget.max_attempts},
                )
            elif spec.idempotent or not spec.side_effects:
                current_step = self.store.get_step(step.id)
                if current_step.status == "blocked":
                    self.store.set_step_status(step.id, "pending")
            else:
                current_step = self.store.get_step(step.id)
                if current_step.status not in {"failed", "pass", "skipped"}:
                    self.store.set_step_status(step.id, "failed")
                self._emit(
                    step.task_id,
                    "retry.blocked",
                    step_id=step.id,
                    execution_id=execution_id,
                    payload={
                        "reason": (
                            "non-idempotent side effect cannot be retried automatically"
                        )
                    },
                )
        elif (
            final_status in {"rework", "abstain"}
            and final_status not in spec.retry_policy.retry_on
        ):
            current_step = self.store.get_step(step.id)
            if current_step.status not in {"failed", "pass", "skipped"}:
                self.store.set_step_status(step.id, "failed")

        fallback_evidence = list(output_ids)
        if receipt_artifact_id is not None:
            fallback_evidence.append(receipt_artifact_id)
        if not fallback_evidence:
            fallback_evidence.extend(execution.input_artifact_ids)
        evidence_ids = tuple(fallback_evidence)
        for claim in outcome.claims:
            claim_kind = str(claim.get("kind", "inferred"))
            claim_evidence = tuple(
                claim.get("evidence_artifact_ids") or evidence_ids
            )
            self.store.add_claim(
                step.task_id,
                step_id=step.id,
                kind=claim_kind,
                subject=str(claim.get("subject") or spec.id),
                value=claim.get("value"),
                evidence_artifact_ids=claim_evidence,
                confidence=claim.get("confidence"),
            )

        if final_status == "pass":
            criterion_evidence = list(output_ids)
            if receipt_artifact_id is not None:
                criterion_evidence.append(receipt_artifact_id)
            if verifier_artifact_id is not None:
                criterion_evidence.append(verifier_artifact_id)
            self._accept_declared_criteria(
                step,
                criterion_evidence,
                spec.metadata,
            )

        self.store.create_checkpoint(
            step.task_id,
            reason=(
                f"step {step.id} execution {execution.attempt} -> {final_status}"
            ),
        )
        self._emit(
            step.task_id,
            "capability.completed",
            step_id=step.id,
            execution_id=execution_id,
            payload={
                "capability": spec.id,
                "status": final_status,
                "outputs": output_ids,
            },
        )

    def _accept_declared_criteria(
        self,
        step: StepRecord,
        evidence_artifact_ids: list[str],
        metadata: dict[str, Any],
    ) -> None:
        criteria = self.store.list_criteria(step.task_id)
        ordinals = set(step.metadata.get("satisfies_criteria", ())) | set(
            metadata.get("satisfies_criteria", ())
        )
        if (
            step.metadata.get("accept_all_criteria")
            or metadata.get("accept_all_criteria")
        ):
            ordinals = {criterion.ordinal for criterion in criteria}
        if ordinals and not evidence_artifact_ids:
            raise ValueError(
                "A step cannot satisfy success criteria without durable evidence."
            )
        for criterion in criteria:
            if criterion.ordinal in ordinals and criterion.status != "accepted":
                self.store.set_criterion_status(
                    criterion.id,
                    "accepted",
                    evidence_artifact_ids=evidence_artifact_ids,
                    note=f"satisfied by step {step.id}",
                )

    def _settle_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        steps = self.store.list_steps(task_id)
        if any(step.status == "failed" for step in steps):
            if task.status not in {"failed", "cancelled", "completed"}:
                self.store.set_task_status(task_id, "failed")
                self._emit(
                    task_id,
                    "task.failed",
                    payload={"reason": "one or more required steps failed"},
                )
            return
        decision = self.completion.evaluate(task_id)
        if decision.complete:
            if task.status != "completed":
                self.store.set_task_status(task_id, "completed")
                self.store.create_checkpoint(task_id, reason="task completed")
                self._emit(task_id, "task.completed")
            return
        if decision.status == "failed" and task.status not in {
            "failed",
            "cancelled",
            "completed",
        }:
            self.store.set_task_status(task_id, "failed")
            self.store.create_checkpoint(task_id, reason="completion rejected")
            self._emit(
                task_id,
                "task.failed",
                payload={"reason": "; ".join(decision.reasons) or "completion rejected"},
            )
            return
        if not self.store.ready_steps(task_id) and task.status == "active":
            self.store.set_task_status(task_id, "waiting")
