from __future__ import annotations

from atlas_core.authority import authority_allows
from atlas_core.capabilities import (
    CapabilityOutcome,
    CapabilityRegistration,
    CapabilityRequest,
    ContextPolicy,
    ExecutionBudget,
    RetryPolicy,
)
from atlas_core.capabilities.execution import CapabilityExecutionProfile
from atlas_core.schema_validation import SchemaValidationError, validate_json
from .records import StepRecord

from .confirmation import (
    confirmation_digest,
    confirmation_document,
    confirmation_summary,
    direct_request_artifact_ids,
)
from .contract import ContractCapability, WorkContract
from .resolve import ResolveReport, ResolvedCapability
from .surface import ExecutionSurface, project_surface

_DETERMINISTIC_KINDS = frozenset({"deterministic", "tool", "composite"})
_SUPPORTED_KINDS = frozenset({"deterministic", "tool", "composite", "human", "model"})


class WorkExecutionMixin:
    def _execute_step(
        self,
        step: StepRecord,
        contract: WorkContract,
        report: ResolveReport,
    ) -> bool:
        work_id = contract.work_id
        if not step.capability:
            self.store.set_step_status(step.id, "failed")
            self._emit(
                work_id,
                "step.failed",
                step_id=step.id,
                payload={"reason": "step has no capability"},
            )
            return True

        pin = contract.capability(step.capability)
        if not pin.armed:
            self.store.set_step_status(step.id, "failed")
            self._emit(
                work_id,
                "step.failed",
                step_id=step.id,
                payload={"reason": "capability is unarmed"},
            )
            return True

        mismatch = next(
            (
                item
                for item in report.mismatches
                if item.capability_id == pin.capability_id
            ),
            None,
        )
        if mismatch is not None:
            execution = self.store.begin_execution(
                work_id,
                step_id=step.id,
                capability=pin.capability_id,
                capability_version=pin.profile_version or "0.0.0",
            )
            self.store.finish_execution(
                execution.id,
                status="fail",
                error=f"resolve mismatch: {mismatch.reason}",
            )
            return True

        resolved = report.resolved.capabilities.get(pin.capability_id)
        if resolved is None:
            execution = self.store.begin_execution(
                work_id,
                step_id=step.id,
                capability=pin.capability_id,
                capability_version=pin.profile_version or "0.0.0",
            )
            self.store.finish_execution(
                execution.id,
                status="fail",
                error="resolve mismatch: handler_missing",
            )
            return True

        approved_override = self._approved_for_step(step, pin.required_authority)
        if pin.executor_kind == "human":
            if approved_override is None:
                self._request_authority_approval(
                    step,
                    pin.required_authority,
                    requested_action=step.description,
                )
                return True
            if pin.confirmation == "required":
                gated = self._gate_payload_confirmation(step, pin)
                if gated != "proceed":
                    return True
            return self._complete_human_step(step, pin, approved_override)

        if (
            not authority_allows(contract.authority_scope, pin.required_authority)
            and approved_override is None
        ):
            self._request_authority_approval(step, pin.required_authority)
            return True

        if pin.confirmation == "required":
            gated = self._gate_payload_confirmation(step, pin)
            if gated != "proceed":
                return True

        budget = pin.budget or ExecutionBudget()
        previous = self.store.list_executions(work_id, step_id=step.id)
        if len(previous) >= budget.max_attempts:
            self.store.set_step_status(step.id, "failed")
            self._emit(
                work_id,
                "step.failed",
                step_id=step.id,
                payload={
                    "reason": "capability attempt limit reached",
                    "max_attempts": budget.max_attempts,
                },
            )
            return True

        input_ids = self.store.dependency_output_artifact_ids(step.id)
        execution = self.store.begin_execution(
            work_id,
            step_id=step.id,
            capability=pin.capability_id,
            capability_version=pin.profile_version or "0.0.0",
            input_artifact_ids=input_ids,
        )
        self._emit(
            work_id,
            "capability.started",
            step_id=step.id,
            execution_id=execution.id,
            payload={
                "capability": pin.capability_id,
                "capability_version": pin.profile_version,
                "provider": None,
                "attempt": execution.attempt,
            },
        )

        if pin.executor_kind not in _SUPPORTED_KINDS:
            self.store.finish_execution(
                execution.id,
                status="fail",
                error="executor not implemented",
            )
            self._emit(
                work_id,
                "capability.completed",
                step_id=step.id,
                execution_id=execution.id,
                payload={"capability": pin.capability_id, "status": "fail"},
            )
            self.store.create_checkpoint(
                work_id,
                reason=f"step {step.id} executor not implemented",
            )
            return True

        profile = execution_profile_from_pin(pin)
        registration = CapabilityRegistration(
            pin.definition, profile, resolved.handler
        )
        surface = project_surface(
            resolved,
            work_id=work_id,
            step_id=step.id,
            authority_scope=contract.authority_scope,
            kernel=self.tools,
        )
        try:
            tool_descriptors = tuple(
                surface.descriptor(reference).as_context_dict()
                for reference in pin.tools
            )
            previous_manifest_id = None
            failure_reason = None
            if previous:
                prior_manifest = self.store.context_manifest_for_execution(
                    previous[-1].id
                )
                previous_manifest_id = prior_manifest.id if prior_manifest else None
                if previous[-1].status in {"rework", "abstain", "fail", "blocked"}:
                    failure_reason = previous[-1].error or previous[-1].status
            request_ids = direct_request_artifact_ids(self.store, step)
            pack = self.context_builder.build(
                work_id,
                step.id,
                artifact_ids=input_ids,
                execution_id=execution.id,
                registration=registration,
                max_chars=budget.max_context_chars,
                tool_descriptors=tool_descriptors,
                required_artifact_ids=request_ids,
                previous_manifest_id=previous_manifest_id,
                failure_reason=failure_reason,
            )
            try:
                validate_json(
                    pack.payload.get("invocation_input"),
                    pin.input_schema or {},
                    path="$.invocation_input",
                )
            except SchemaValidationError as exc:
                raise ValueError(f"input schema validation failed: {exc}") from exc
            manifest_record = self.store.write_context_manifest(
                work_id,
                step_id=step.id,
                execution_id=execution.id,
                capability=pin.capability_id,
                capability_version=pin.profile_version or "0.0.0",
                assembler_version=pack.manifest.assembler_version,
                budget_tokens=pack.manifest.budget_tokens,
                total_tokens=pack.manifest.total_tokens,
                manifest=pack.manifest.as_dict(),
                manifest_id=pack.manifest.manifest_id,
            )
            self._emit(
                work_id,
                "context.manifest.written",
                step_id=step.id,
                execution_id=execution.id,
                payload={
                    "manifest_id": manifest_record.id,
                    "sha256": manifest_record.sha256,
                    "capability": pin.capability_id,
                    "capability_version": pin.profile_version,
                    "tokens": pack.manifest.total_tokens,
                    "budget": pack.manifest.budget_tokens,
                },
            )
        except Exception as exc:
            self.store.finish_execution(
                execution.id,
                status="fail",
                error=f"context assembly failed: {exc}",
            )
            self._emit(
                work_id,
                "capability.completed",
                step_id=step.id,
                execution_id=execution.id,
                payload={"capability": pin.capability_id, "status": "fail"},
            )
            self.store.create_checkpoint(
                work_id,
                reason=f"step {step.id} context assembly failed",
            )
            return True

        if pin.executor_kind == "model":
            outcome = self._execute_model(
                contract=contract,
                pin=pin,
                pack=pack,
                execution_id=execution.id,
                step_id=step.id,
                previous=previous,
            )
        elif pin.executor_kind in _DETERMINISTIC_KINDS:
            outcome = self._invoke_handler(
                step=step,
                pin=pin,
                resolved=resolved,
                surface=surface,
                pack=pack,
                execution=execution,
                input_ids=input_ids,
            )
        else:
            outcome = CapabilityOutcome(
                "fail",
                error="executor not implemented",
            )

        self._finish_frame(
            step,
            pin,
            profile,
            execution.id,
            pack.payload,
            outcome,
        )
        return True

    def _execute_model(
        self,
        *,
        contract: WorkContract,
        pin: ContractCapability,
        pack,
        execution_id: str,
        step_id: str,
        previous,
    ) -> CapabilityOutcome:
        consumer = getattr(self, "model_consumer", None)
        if consumer is None:
            return CapabilityOutcome("fail", error="executor not implemented")

        def set_provider(key: str) -> None:
            self.store.set_execution_provider(execution_id, key)
            self._emit(
                contract.work_id,
                "provider.selected",
                step_id=step_id,
                execution_id=execution_id,
                payload={"provider": key},
            )

        return consumer.execute(
            contract=contract,
            pin=pin,
            pack=pack,
            execution_id=execution_id,
            previous=previous,
            work_executions=self.store.list_executions(contract.work_id),
            set_provider=set_provider,
        )

    def _invoke_handler(
        self,
        *,
        step: StepRecord,
        pin: ContractCapability,
        resolved: ResolvedCapability,
        surface: ExecutionSurface,
        pack,
        execution,
        input_ids: tuple[str, ...],
    ) -> CapabilityOutcome:
        if surface is None:
            return CapabilityOutcome("fail", error="execution surface is required")
        if resolved.handler is None:
            return CapabilityOutcome("fail", error="capability has no handler")
        direct_ids = tuple(step.input_artifact_ids)
        direct_set = set(direct_ids)
        dependency_ids = tuple(
            artifact_id
            for artifact_id in input_ids
            if artifact_id not in direct_set
        )
        request = CapabilityRequest(
            step.work_id,
            step.id,
            pin.capability_id,
            pack.payload,
            input_ids,
            execution.attempt,
            capability_version=pin.profile_version or "0.0.0",
            direct_input_artifact_ids=direct_ids,
            dependency_artifact_ids=dependency_ids,
            idempotency_key=f"{step.work_id}:{step.id}:{pin.capability_id}",
            surface=surface,
        )
        try:
            return resolved.handler(request)
        except Exception as exc:
            return CapabilityOutcome("fail", error=str(exc))

    def _complete_human_step(
        self,
        step: StepRecord,
        pin: ContractCapability,
        approval: object,
    ) -> bool:
        budget = pin.budget or ExecutionBudget()
        previous = self.store.list_executions(step.work_id, step_id=step.id)
        if len(previous) >= budget.max_attempts:
            self.store.set_step_status(step.id, "failed")
            return True
        input_ids = self.store.dependency_output_artifact_ids(step.id)
        execution = self.store.begin_execution(
            step.work_id,
            step_id=step.id,
            capability=pin.capability_id,
            capability_version=pin.profile_version or "0.0.0",
            provider="human",
            input_artifact_ids=input_ids,
        )
        self._emit(
            step.work_id,
            "capability.started",
            step_id=step.id,
            execution_id=execution.id,
            payload={"capability": pin.capability_id, "provider": "human"},
        )
        outcome = CapabilityOutcome(
            "pass",
            output={
                "approval_id": approval.id,
                "decision": approval.status,
                "note": approval.decision_note,
                "requested_action": approval.requested_action,
            },
            output_kind=pin.output_kind or "human_decision",
            receipt={"ok": True, "approval_id": approval.id},
            claims=(
                {
                    "kind": "executed",
                    "subject": f"human_gate.{pin.capability_id}",
                    "value": approval.status,
                },
            ),
        )
        profile = execution_profile_from_pin(pin)
        self._finish_frame(step, pin, profile, execution.id, {}, outcome)
        return True

    def _gate_payload_confirmation(self, step: StepRecord, pin: ContractCapability) -> str:
        document = confirmation_document(self.store, step, pin)
        _, digest = confirmation_digest(document)
        matching = [
            item
            for item in self.store.list_confirmations(
                step.work_id, step_id=step.id
            )
            if item.payload_sha256 == digest
        ]
        if any(item.status == "denied" for item in matching):
            self.store.set_step_status(step.id, "failed")
            self._emit(
                step.work_id,
                "step.failed",
                step_id=step.id,
                payload={
                    "reason": "payload confirmation denied",
                    "payload_sha256": digest,
                },
            )
            return "denied"
        if any(item.status == "confirmed" for item in matching):
            return "proceed"
        pending = [item for item in matching if item.status == "pending"]
        if not pending:
            record = self.store.request_confirmation(
                step.work_id,
                step_id=step.id,
                capability_id=pin.capability_id,
                payload=document,
                summary=confirmation_summary(
                    pin.capability_id, document.get("invocation_input")
                ),
            )
            self._emit(
                step.work_id,
                "confirmation.requested",
                step_id=step.id,
                payload={
                    "confirmation_id": record.id,
                    "payload_sha256": digest,
                    "summary": record.summary,
                },
            )
        if self.store.get_step(step.id).status != "blocked":
            self.store.set_step_status(step.id, "blocked")
        return "wait"


def execution_profile_from_pin(pin: ContractCapability) -> CapabilityExecutionProfile:
    """CapabilityRegistration-shaped view of a frozen pin. Not a live lookup."""

    if not pin.armed or not pin.profile_version or pin.executor_kind is None:
        raise ValueError("cannot build an execution profile from an unarmed pin")
    return CapabilityExecutionProfile(
        capability_id=pin.capability_id,
        implementation=pin.binding,
        tools=tuple(pin.tools),
        verifier_id=pin.verifier_id,
        verification_required=pin.verification_required,
        executor_kind=pin.executor_kind,
        version=pin.profile_version,
        retry_policy=pin.retry_policy or RetryPolicy(),
        context_policy=pin.context_policy or ContextPolicy(),
        context_profile=pin.context_profile or "execute",
        budget=pin.budget or ExecutionBudget(),
        input_schema=dict(pin.input_schema or {}),
        output_schema=dict(pin.output_schema or {}),
        output_kind=pin.output_kind or "capability_result",
        requires_artifact_kinds=pin.requires_artifact_kinds,
        eligible_providers=pin.eligible_providers,
        side_effects=pin.side_effects,
        idempotent=pin.idempotent,
        parallel_safe=pin.parallel_safe,
        privacy=pin.privacy or "cloud_allowed",
        data_classification=pin.data_classification or "internal",
    )
