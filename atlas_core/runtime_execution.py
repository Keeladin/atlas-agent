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
from .runtime_types import RuntimeResult, RecoveryResult

class RuntimeExecutionMixin:
    def _execute_step(self, step: StepRecord) -> bool:
        if not step.capability:
            self.store.set_step_status(step.id, "failed")
            self._emit(
                step.task_id,
                "step.failed",
                step_id=step.id,
                payload={"reason": "step has no capability"},
            )
            return True

        try:
            binding = self.capabilities.get(step.capability)
        except CapabilityRegistryError as exc:
            execution = self.store.begin_execution(
                step.task_id,
                step_id=step.id,
                capability=step.capability,
            )
            self._emit(
                step.task_id,
                "capability.started",
                step_id=step.id,
                execution_id=execution.id,
                payload={"capability": step.capability, "attempt": execution.attempt},
            )
            self.store.finish_execution(
                execution.id,
                status="fail",
                error=str(exc),
            )
            self._emit(
                step.task_id,
                "capability.completed",
                step_id=step.id,
                execution_id=execution.id,
                payload={"capability": step.capability, "status": "fail"},
            )
            return True

        spec = binding.spec
        task = self.store.get_task(step.task_id)
        approved_override = self._approved_for_step(
            step,
            spec.required_authority,
        )

        if spec.executor_kind == "human":
            if approved_override is None:
                self._request_authority_approval(
                    step,
                    spec.required_authority,
                    requested_action=step.description,
                )
                return True
            return self._complete_human_step(step, spec, approved_override)

        if (
            not authority_allows(task.authority_scope, spec.required_authority)
            and approved_override is None
        ):
            self._request_authority_approval(step, spec.required_authority)
            return True

        previous = self.store.list_executions(step.task_id, step_id=step.id)
        if len(previous) >= spec.budget.max_attempts:
            self.store.set_step_status(step.id, "failed")
            self._emit(
                step.task_id,
                "step.failed",
                step_id=step.id,
                payload={
                    "reason": "capability attempt limit reached",
                    "max_attempts": spec.budget.max_attempts,
                },
            )
            return True

        input_ids = self.store.dependency_output_artifact_ids(step.id)
        execution = self.store.begin_execution(
            step.task_id,
            step_id=step.id,
            capability=spec.id,
            input_artifact_ids=input_ids,
        )
        self._emit(
            step.task_id,
            "capability.started",
            step_id=step.id,
            execution_id=execution.id,
            payload={
                "capability": spec.id,
                "provider": None,
                "attempt": execution.attempt,
            },
        )

        try:
            pack = self.context_builder.build(
                step.task_id,
                step.id,
                artifact_ids=input_ids,
                profile=spec.context_profile,
                max_chars=spec.budget.max_context_chars,
            )
        except Exception as exc:
            self.store.finish_execution(
                execution.id,
                status="fail",
                error=f"context assembly failed: {exc}",
            )
            self._emit(
                step.task_id,
                "capability.completed",
                step_id=step.id,
                execution_id=execution.id,
                payload={"capability": spec.id, "status": "fail"},
            )
            self.store.create_checkpoint(
                step.task_id,
                reason=f"step {step.id} context assembly failed",
            )
            return True

        if spec.executor_kind == "model":
            outcome = self._execute_model_frame(
                step,
                spec,
                execution.id,
                pack,
                previous,
            )
        else:
            direct_ids = tuple(step.input_artifact_ids)
            direct_set = set(direct_ids)
            dependency_ids = tuple(
                artifact_id
                for artifact_id in input_ids
                if artifact_id not in direct_set
            )
            request = CapabilityRequest(
                step.task_id,
                step.id,
                spec.id,
                pack.payload,
                input_ids,
                execution.attempt,
                direct_input_artifact_ids=direct_ids,
                dependency_artifact_ids=dependency_ids,
                idempotency_key=f"{step.task_id}:{step.id}:{spec.id}",
            )
            try:
                outcome = (
                    binding.handler(request)
                    if binding.handler is not None
                    else CapabilityOutcome(
                        "fail",
                        error="capability has no handler",
                    )
                )
            except Exception as exc:
                outcome = CapabilityOutcome("fail", error=str(exc))

        self._finish_frame(
            step,
            spec,
            execution.id,
            pack.payload,
            outcome,
        )
        return True

    def _execute_model_frame(
        self,
        step: StepRecord,
        spec: Any,
        execution_id: str,
        pack: Any,
        previous: tuple[Any, ...],
    ) -> CapabilityOutcome:
        if self.model_router is None:
            return CapabilityOutcome(
                "blocked",
                error="model capability has no configured model router",
            )
        model_calls = sum(
            1
            for item in self.store.list_executions(step.task_id)
            if item.provider
        )
        if model_calls >= self.budget.max_model_calls:
            return CapabilityOutcome(
                "blocked",
                error="task model-call budget exhausted",
            )
        failed_providers = tuple(
            item.provider
            for item in previous
            if item.status in {"abstain", "fail"} and item.provider
        )
        try:
            route = self.model_router.select(
                spec,
                context_chars=pack.chars,
                exclude_provider_keys=failed_providers,
            )
        except ModelRoutingError as exc:
            return CapabilityOutcome("blocked", error=str(exc))

        estimated_input_tokens = max(1, (pack.chars + 3) // 4)
        estimated_output_tokens = max(
            1,
            ((spec.budget.max_output_chars or 8_000) + 3) // 4,
        )
        projected_call_cost = route.provider.spec.estimate_cost_usd(
            input_tokens=estimated_input_tokens,
            output_tokens=estimated_output_tokens,
        )
        if (
            spec.budget.max_cost_usd is not None
            and projected_call_cost is not None
            and projected_call_cost > spec.budget.max_cost_usd
        ):
            return CapabilityOutcome(
                "blocked",
                error=(
                    "projected provider cost exceeds capability budget: "
                    f"{projected_call_cost:.6f}>{spec.budget.max_cost_usd:.6f}"
                ),
            )
        if (
            self.budget.max_cost_usd is not None
            and projected_call_cost is not None
            and self._spent_cost_usd(step.task_id) + projected_call_cost
            > self.budget.max_cost_usd
        ):
            return CapabilityOutcome(
                "blocked",
                error="projected provider cost exceeds remaining task budget",
            )

        self.store.set_execution_provider(execution_id, route.provider.spec.key)
        self._emit(
            step.task_id,
            "provider.selected",
            step_id=step.id,
            execution_id=execution_id,
            payload={
                "provider": route.provider.spec.key,
                "reason": route.reason,
            },
        )
        try:
            response = route.provider.generate(
                ModelRequest(
                    capability_id=spec.id,
                    system=pack.payload["context_profile"]["instruction"],
                    input=pack.as_text(),
                    max_output_chars=spec.budget.max_output_chars,
                    metadata={
                        "task_id": step.task_id,
                        "step_id": step.id,
                    },
                )
            )
            metrics = dict(response.metrics)
            actual_cost = route.provider.spec.estimate_cost_usd(
                input_tokens=int(metrics.get("input_tokens") or 0),
                output_tokens=int(metrics.get("output_tokens") or 0),
            )
            if actual_cost is not None:
                metrics["estimated_cost_usd"] = actual_cost
            return CapabilityOutcome(
                "pass",
                output=response.text,
                output_kind=spec.output_kind,
                metrics=metrics,
                receipt={
                    "ok": True,
                    "provider": response.provider_key,
                    "model": response.model,
                },
            )
        except Exception as exc:
            return CapabilityOutcome(
                "abstain",
                error=str(exc),
                receipt={
                    "ok": False,
                    "provider": route.provider.spec.key,
                },
            )

    def _complete_human_step(self, step: StepRecord, spec: Any, approval: Any) -> bool:
        previous = self.store.list_executions(step.task_id, step_id=step.id)
        if len(previous) >= spec.budget.max_attempts:
            self.store.set_step_status(step.id, "failed")
            return True
        input_ids = self.store.dependency_output_artifact_ids(step.id)
        execution = self.store.begin_execution(
            step.task_id,
            step_id=step.id,
            capability=spec.id,
            provider="human",
            input_artifact_ids=input_ids,
        )
        self._emit(
            step.task_id,
            "capability.started",
            step_id=step.id,
            execution_id=execution.id,
            payload={"capability": spec.id, "provider": "human"},
        )
        outcome = CapabilityOutcome(
            "pass",
            output={
                "approval_id": approval.id,
                "decision": approval.status,
                "note": approval.decision_note,
                "requested_action": approval.requested_action,
            },
            output_kind=spec.output_kind or "human_decision",
            receipt={"ok": True, "approval_id": approval.id},
            claims=(
                {
                    "kind": "executed",
                    "subject": f"human_gate.{spec.id}",
                    "value": approval.status,
                },
            ),
        )
        self._finish_frame(step, spec, execution.id, {}, outcome)
        return True
