from __future__ import annotations

import json
from typing import Any

from atlas_core.capabilities import CapabilityOutcome
from atlas_core.capabilities.execution import CapabilityExecutionProfile
from atlas_core.schema_validation import SchemaValidationError, validate_json
from .records import StepRecord
from atlas_core.verification import VerificationResult

from .contract import ContractCapability


class WorkFinishMixin:
    def _finish_frame(
        self,
        step: StepRecord,
        pin: ContractCapability,
        profile: CapabilityExecutionProfile,
        execution_id: str,
        context: dict[str, Any],
        outcome: CapabilityOutcome,
    ) -> None:
        if outcome.status == "pass" and pin.output_schema:
            try:
                validate_json(outcome.output, pin.output_schema, path="$.output")
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
                    step.work_id,
                    "capability.output_schema_failed",
                    step_id=step.id,
                    execution_id=execution_id,
                    payload={
                        "capability": pin.capability_id,
                        "capability_version": pin.profile_version,
                        "error": str(exc),
                    },
                )

        if (
            outcome.status == "pass"
            and pin.side_effects
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
                step.work_id,
                "side_effect.unverified",
                step_id=step.id,
                execution_id=execution_id,
                payload={"capability": pin.capability_id},
            )

        output_ids: list[str] = []
        budget = pin.budget
        if outcome.output is not None:
            size = len(
                json.dumps(
                    outcome.output,
                    ensure_ascii=False,
                    default=str,
                )
            )
            if (
                budget is not None
                and budget.max_output_chars is not None
                and size > budget.max_output_chars
            ):
                outcome = CapabilityOutcome(
                    "rework",
                    receipt=outcome.receipt,
                    metrics=outcome.metrics,
                    error=(
                        "output exceeds explicit budget: "
                        f"{size}>{budget.max_output_chars}"
                    ),
                    claims=outcome.claims,
                )
            else:
                artifact = self.store.put_artifact(
                    step.work_id,
                    step_id=step.id,
                    kind=outcome.output_kind or pin.output_kind,
                    payload=outcome.output,
                    metadata={
                        "capability": pin.capability_id,
                        "execution_id": execution_id,
                        "outcome_status": outcome.status,
                    },
                )
                output_ids.append(artifact.id)

        receipt_artifact_id: str | None = None
        if outcome.receipt:
            receipt_artifact = self.store.put_artifact(
                step.work_id,
                step_id=step.id,
                kind="execution_receipt",
                payload=outcome.receipt,
                metadata={
                    "capability": pin.capability_id,
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
        if outcome.status == "pass" and pin.verification_required:
            try:
                verification = self.verifiers.verify(
                    pin.verifier_id or "",
                    profile,
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
            work = self.store.get_work(step.work_id)
            gate = self.outcome_gate.evaluate(
                profile=profile,
                output=outcome.output,
                context=verification_context,
                step=step,
                work=work,
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
            pin.verification_required or verification.status != "pass"
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
                step.work_id,
                step_id=step.id,
                kind="verification_result",
                payload=verifier_payload,
                metadata={
                    "verifier_id": pin.verifier_id,
                    "execution_id": execution_id,
                    "outcome_gate": True,
                },
            )
            verifier_artifact_id = verifier.id
            self._emit(
                step.work_id,
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

        retry_policy = pin.retry_policy
        retry_on = retry_policy.retry_on if retry_policy is not None else ("rework", "abstain")
        max_attempts = budget.max_attempts if budget is not None else 3
        if final_status in retry_on:
            if execution.attempt >= max_attempts:
                current_step = self.store.get_step(step.id)
                if current_step.status not in {"failed", "pass", "skipped"}:
                    self.store.set_step_status(step.id, "failed")
                self._emit(
                    step.work_id,
                    "retry.exhausted",
                    step_id=step.id,
                    execution_id=execution_id,
                    payload={"max_attempts": max_attempts},
                )
            elif pin.idempotent or not pin.side_effects:
                current_step = self.store.get_step(step.id)
                if current_step.status == "blocked":
                    self.store.set_step_status(step.id, "pending")
            else:
                current_step = self.store.get_step(step.id)
                if current_step.status not in {"failed", "pass", "skipped"}:
                    self.store.set_step_status(step.id, "failed")
                self._emit(
                    step.work_id,
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
            and final_status not in retry_on
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
                step.work_id,
                step_id=step.id,
                kind=claim_kind,
                subject=str(claim.get("subject") or pin.capability_id),
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
                profile.metadata,
            )

        self.store.create_checkpoint(
            step.work_id,
            reason=(
                f"step {step.id} execution {execution.attempt} -> {final_status}"
            ),
        )
        self._emit(
            step.work_id,
            "capability.completed",
            step_id=step.id,
            execution_id=execution_id,
            payload={
                "capability": pin.capability_id,
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
        criteria = self.store.list_criteria(step.work_id)
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
