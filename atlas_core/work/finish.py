from __future__ import annotations

import json
import hashlib
from typing import Any

from atlas_core.capabilities import CapabilityOutcome
from atlas_core.capabilities.execution import CapabilityExecutionProfile
from atlas_core.schema_validation import SchemaValidationError, validate_json
from .records import StepRecord
from atlas_core.verification import VerificationResult

from .contract import ContractCapability, WorkContract
from atlas_core.evidence import qualifies_as_source_evidence


class WorkFinishMixin:
    def _finish_frame(
        self,
        step: StepRecord,
        pin: ContractCapability,
        contract: WorkContract,
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
                    output_provenance_category=outcome.output_provenance_category,
                    artifacts=outcome.artifacts,
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
                output_provenance_category=outcome.output_provenance_category,
                artifacts=outcome.artifacts,
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
                    output_provenance_category=outcome.output_provenance_category,
                    artifacts=outcome.artifacts,
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
                    provenance_category=outcome.output_provenance_category,
                )
                output_ids.append(artifact.id)

        for declared in outcome.artifacts:
            artifact = self.store.put_artifact(
                step.work_id,
                step_id=step.id,
                kind=declared.kind,
                payload=declared.payload,
                metadata={
                    **declared.metadata,
                    "capability": pin.capability_id,
                    "execution_id": execution_id,
                    "outcome_status": outcome.status,
                },
                provenance_category=declared.provenance_category,
            )
            output_ids.append(artifact.id)

        receipt_payload = dict(outcome.receipt)
        if receipt_payload:
            receipt_payload.update({
                "work_id": step.work_id,
                "step_id": step.id,
                "execution_id": execution_id,
                "capability_id": pin.capability_id,
                "artifact_ids": list(output_ids),
                "mapped_outcome": outcome.status,
            })

        receipt_artifact_id: str | None = None
        if receipt_payload:
            receipt_artifact = self.store.put_artifact(
                step.work_id,
                step_id=step.id,
                kind="execution_receipt",
                payload=receipt_payload,
                metadata={
                    "capability": pin.capability_id,
                    "execution_id": execution_id,
                },
                provenance_category="execution_receipt",
            )
            receipt_artifact_id = receipt_artifact.id

        verifier_artifact_id: str | None = None
        verification: VerificationResult | None = None
        final_status = outcome.status
        verification_context = dict(context)
        verification_context["execution_receipt"] = receipt_payload
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
                provenance_category="verifier_result",
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
        manifest = self.store.context_manifest_for_execution(execution_id)
        criteria_by_ordinal = {
            item.ordinal: item for item in self.store.list_criteria(step.work_id)
        }
        fallback_claim_evidence = list(output_ids)
        if receipt_artifact_id is not None:
            fallback_claim_evidence.append(receipt_artifact_id)
        if not fallback_claim_evidence:
            running_execution = self.store.get_execution(execution_id)
            fallback_claim_evidence.extend(running_execution.input_artifact_ids)
        for claim in outcome.claims:
            claim_kind = str(claim.get("kind", "inferred"))
            claim_evidence = (
                tuple(claim["evidence_artifact_ids"])
                if "evidence_artifact_ids" in claim
                else tuple(fallback_claim_evidence)
            )
            linked_ordinals = tuple(claim.get("criterion_ordinals", ()))
            authorized = {
                binding.criterion_ordinal for binding in contract.criterion_bindings
                if binding.contract_capability_ordinal == step.contract_capability_ordinal
            }
            if any(item not in authorized for item in linked_ordinals):
                final_status = "rework"
                execution_error = "claim links to an unauthorized criterion"
                linked_ordinals = ()
            self.store.add_claim(
                step.work_id,
                step_id=step.id,
                kind=claim_kind,
                subject=str(claim.get("subject") or pin.capability_id),
                value=claim.get("value"),
                evidence_artifact_ids=claim_evidence,
                confidence=claim.get("confidence"),
                execution_id=execution_id,
                context_manifest_id=None if manifest is None else manifest.id,
                criterion_ids=tuple(criteria_by_ordinal[item].id for item in linked_ordinals),
            )

        grounded_evidence: dict[int, tuple[str, ...]] = {}
        grounded_verdicts: dict[int, str] = {}
        if final_status == "pass":
            for criterion in criteria_by_ordinal.values():
                if criterion.satisfaction_policy != "evidence_grounded":
                    continue
                if not any(
                    binding.criterion_ordinal == criterion.ordinal
                    and binding.contract_capability_ordinal == step.contract_capability_ordinal
                    for binding in contract.criterion_bindings
                ):
                    continue
                linked = self.store.criterion_claims(criterion.id, execution_id=execution_id)
                qualifying = tuple(
                    claim for claim in linked
                    if claim.kind in {"observed", "retrieved", "calculated", "executed"}
                    and claim.evidence_artifact_ids
                )
                source_ids = tuple(
                    artifact_id
                    for artifact_id in dict.fromkeys(
                        artifact_id
                        for claim in qualifying
                        for artifact_id in claim.evidence_artifact_ids
                    )
                    if qualifies_as_source_evidence(self.store.get_artifact(artifact_id))
                )
                qualifying = tuple(
                    claim for claim in qualifying
                    if any(artifact_id in source_ids for artifact_id in claim.evidence_artifact_ids)
                )
                document = {
                    "contract_sha256": contract.sha256,
                    "contract_capability_ordinal": step.contract_capability_ordinal,
                    "criterion": {"ordinal": criterion.ordinal, "text": criterion.text},
                    "execution_id": execution_id,
                    "claims": [
                        {"id": claim.id, "kind": claim.kind, "subject": claim.subject, "value": claim.value,
                         "evidence": [{"id": artifact_id, "sha256": self.store.get_artifact(artifact_id).sha256,
                                       "payload": self.store.get_artifact(artifact_id).payload}
                                      for artifact_id in claim.evidence_artifact_ids]}
                        for claim in qualifying
                    ],
                    "deliverable_artifacts": [
                        {"id": artifact_id, "sha256": self.store.get_artifact(artifact_id).sha256,
                         "payload": self.store.get_artifact(artifact_id).payload}
                        for artifact_id in output_ids
                    ],
                }
                encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
                input_sha256 = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                if not qualifying:
                    criterion_result = VerificationResult("rework", "criterion has no qualifying linked grounded claims")
                elif self.grounded_criterion_verifier is None:
                    criterion_result = VerificationResult("abstain", "independent criterion verifier is unavailable")
                else:
                    criterion_result = self.grounded_criterion_verifier.verify(profile, document)
                criterion_artifact = self.store.put_artifact(
                    step.work_id, step_id=step.id, kind="criterion_verification_result",
                    payload={"status": criterion_result.status, "summary": criterion_result.summary,
                             "input_sha256": input_sha256, "criterion_ordinal": criterion.ordinal,
                             "evaluated_claim_ids": [claim.id for claim in qualifying],
                             "details": criterion_result.details},
                    metadata={"execution_id": execution_id, "criterion_id": criterion.id},
                    provenance_category="verifier_result",
                )
                self.store.add_criterion_verification(
                    work_id=step.work_id, criterion_id=criterion.id,
                    contract_capability_ordinal=step.contract_capability_ordinal or 0,
                    step_id=step.id, execution_id=execution_id, status=criterion_result.status,
                    input_sha256=input_sha256, artifact_id=criterion_artifact.id,
                )
                if criterion_result.status == "pass":
                    grounded_evidence[criterion.ordinal] = source_ids
                    grounded_verdicts[criterion.ordinal] = criterion_artifact.id
                else:
                    self.store.set_criterion_status(
                        criterion.id,
                        "rejected" if criterion_result.status == "fail" else "pending",
                        evidence_artifact_ids=(source_ids if criterion_result.status == "fail" else ()),
                        note=criterion_result.summary,
                        verification_artifact_id=criterion_artifact.id,
                    )
                    final_status = criterion_result.status
                    execution_error = execution_error or criterion_result.summary

        execution = self.store.finish_execution(
            execution_id,
            status=final_status,
            output_artifact_ids=output_ids,
            verifier_artifact_id=verifier_artifact_id,
            receipt=receipt_payload,
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
        if final_status == "pass":
            criterion_evidence = list(output_ids)
            if receipt_artifact_id is not None:
                criterion_evidence.append(receipt_artifact_id)
            if verifier_artifact_id is not None:
                criterion_evidence.append(verifier_artifact_id)
            self._accept_declared_criteria(
                step,
                criterion_evidence,
                contract,
                grounded_evidence,
                grounded_verdicts,
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
        contract: WorkContract,
        grounded_evidence: dict[int, tuple[str, ...]],
        grounded_verdicts: dict[int, str],
    ) -> None:
        criteria = self.store.list_criteria(step.work_id)
        ordinals = {
            binding.criterion_ordinal for binding in contract.criterion_bindings
            if binding.contract_capability_ordinal == step.contract_capability_ordinal
        }
        if ordinals and not evidence_artifact_ids:
            raise ValueError(
                "A step cannot satisfy success criteria without durable evidence."
            )
        for criterion in criteria:
            if criterion.ordinal in ordinals and criterion.status != "accepted":
                selected_evidence = (
                    list(grounded_evidence.get(criterion.ordinal, ()))
                    if criterion.satisfaction_policy == "evidence_grounded"
                    else evidence_artifact_ids
                )
                if criterion.satisfaction_policy == "evidence_grounded" and not selected_evidence:
                    continue
                self.store.set_criterion_status(
                    criterion.id,
                    "accepted",
                    evidence_artifact_ids=selected_evidence,
                    note=f"satisfied by step {step.id}",
                    verification_artifact_id=(
                        grounded_verdicts.get(criterion.ordinal)
                        if criterion.satisfaction_policy == "evidence_grounded"
                        else None
                    ),
                )
