from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from atlas_core.authority import authority_allows
from atlas_core.capabilities import CapabilityOutcome, CapabilityRegistry, CapabilityRequest
from atlas_core.context import ContextBuilder
from atlas_core.events import EventBus, RuntimeEvent
from atlas_core.providers import ModelRequest, ModelRouter
from atlas_core.tasks import StepRecord, TaskStore
from atlas_core.verification import CompletionVerifier, VerifierRegistry


@dataclass(frozen=True)
class RuntimeBudget:
    max_executions: int = 1000
    max_cycles: int = 1000
    max_model_calls: int = 200
    max_parallel_workers: int = 4


@dataclass(frozen=True)
class RuntimeResult:
    task_id: str
    status: str
    cycles: int
    executions: int
    reason: str


class TaskRuntime:
    """Durable execution engine for Atlas 2.0.

    Execution frames are bounded; task depth is not. The runtime can stop after
    any frame, persist state, and resume later without relying on model context.
    """

    def __init__(self, *, store: TaskStore, capabilities: CapabilityRegistry, verifiers: VerifierRegistry | None = None, model_router: ModelRouter | None = None, event_bus: EventBus | None = None, budget: RuntimeBudget | None = None) -> None:
        self.store = store
        self.capabilities = capabilities
        self.verifiers = verifiers or VerifierRegistry()
        self.model_router = model_router
        self.event_bus = event_bus or EventBus()
        self.budget = budget or RuntimeBudget()
        self.context_builder = ContextBuilder(store)
        self.completion = CompletionVerifier(store)

    def _emit(self, task_id: str, name: str, *, step_id: str | None = None, execution_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
        self.store.append_event(task_id, name=name, step_id=step_id, execution_id=execution_id, payload=payload or {})
        self.event_bus.emit(RuntimeEvent(name, task_id, step_id, execution_id, payload or {}))

    def run_until_blocked(self, task_id: str) -> RuntimeResult:
        task = self.store.get_task(task_id)
        if task.status in {"completed", "failed", "cancelled"}:
            return RuntimeResult(task_id, task.status, 0, len(self.store.list_executions(task_id)), "task already terminal")
        if task.status == "planned":
            self.store.set_task_status(task_id, "active")
            self._emit(task_id, "task.started")
        cycles = 0
        while cycles < self.budget.max_cycles:
            cycles += 1
            before = len(self.store.list_executions(task_id))
            if before >= self.budget.max_executions:
                self.store.set_task_status(task_id, "failed")
                self._emit(task_id, "task.failed", payload={"reason": "task execution budget exhausted"})
                return RuntimeResult(task_id, "failed", cycles, before, "task execution budget exhausted")
            progressed = self.run_once(task_id)
            after = len(self.store.list_executions(task_id))
            task = self.store.get_task(task_id)
            if task.status in {"completed", "failed", "cancelled"}:
                return RuntimeResult(task_id, task.status, cycles, after, "task reached terminal state")
            if not progressed:
                if task.status != "waiting":
                    self.store.set_task_status(task_id, "waiting")
                self._emit(task_id, "task.paused", payload={"reason": "no executable ready steps"})
                return RuntimeResult(task_id, "waiting", cycles, after, "no executable ready steps")
        if self.store.get_task(task_id).status == "active":
            self.store.set_task_status(task_id, "waiting")
        self._emit(task_id, "task.paused", payload={"reason": "runtime cycle budget reached"})
        return RuntimeResult(task_id, "waiting", cycles, len(self.store.list_executions(task_id)), "runtime cycle budget reached")

    def run_once(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        if task.status == "waiting":
            self.store.set_task_status(task_id, "active")
            self._emit(task_id, "task.resumed")
        self._release_approval_blocks(task_id)
        ready = list(self.store.ready_steps(task_id))
        if not ready:
            self._settle_task(task_id)
            return False
        parallel: list[StepRecord] = []
        serial: list[StepRecord] = []
        for step in ready:
            if not step.capability:
                serial.append(step)
                continue
            try:
                is_parallel = self.capabilities.get(step.capability).spec.parallel_safe
            except Exception:
                is_parallel = False
            (parallel if is_parallel else serial).append(step)
        progressed = False
        for step in serial:
            self._execute_step(step)
            progressed = True
        if parallel:
            workers = max(1, min(self.budget.max_parallel_workers, len(parallel)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="atlas-step") as pool:
                futures = {pool.submit(self._execute_step, step): step for step in parallel}
                for future in as_completed(futures):
                    future.result()
                    progressed = True
        self._settle_task(task_id)
        return progressed

    def _release_approval_blocks(self, task_id: str) -> None:
        approvals = self.store.list_approvals(task_id)
        by_step: dict[str, list[Any]] = {}
        for approval in approvals:
            if approval.step_id is not None:
                by_step.setdefault(approval.step_id, []).append(approval)
        for step in self.store.list_steps(task_id):
            if step.status != "blocked":
                continue
            decisions = by_step.get(step.id, [])
            if any(item.status == "denied" for item in decisions):
                self.store.set_step_status(step.id, "failed")
                self._emit(task_id, "step.failed", step_id=step.id, payload={"reason": "required approval denied"})
            elif any(item.status == "approved" for item in decisions):
                self.store.set_step_status(step.id, "pending")
                self._emit(task_id, "approval.applied", step_id=step.id)

    def _step_has_approved_override(self, step: StepRecord, required_authority: str) -> bool:
        return any(
            approval.step_id == step.id and approval.required_authority == required_authority
            for approval in self.store.list_approvals(step.task_id, status="approved")
        )

    def _request_authority_approval(self, step: StepRecord, required_authority: str) -> None:
        pending = [approval for approval in self.store.list_approvals(step.task_id, status="pending") if approval.step_id == step.id and approval.required_authority == required_authority]
        if not pending:
            approval = self.store.request_approval(step.task_id, step_id=step.id, required_authority=required_authority, requested_action=f"Allow capability {step.capability} for step: {step.description}")
            self._emit(step.task_id, "approval.requested", step_id=step.id, payload={"approval_id": approval.id, "required_authority": required_authority})
        if step.status != "blocked":
            self.store.set_step_status(step.id, "blocked")

    def _execute_step(self, step: StepRecord) -> None:
        if not step.capability:
            self.store.set_step_status(step.id, "failed")
            self._emit(step.task_id, "step.failed", step_id=step.id, payload={"reason": "step has no capability"})
            return
        binding = self.capabilities.get(step.capability)
        spec = binding.spec
        task = self.store.get_task(step.task_id)
        if not authority_allows(task.authority_scope, spec.required_authority) and not self._step_has_approved_override(step, spec.required_authority):
            self._request_authority_approval(step, spec.required_authority)
            return
        previous = self.store.list_executions(step.task_id, step_id=step.id)
        if len(previous) >= spec.budget.max_attempts:
            self.store.set_step_status(step.id, "failed")
            self._emit(step.task_id, "step.failed", step_id=step.id, payload={"reason": "capability attempt limit reached", "max_attempts": spec.budget.max_attempts})
            return
        input_ids = self.store.dependency_output_artifact_ids(step.id)
        pack = self.context_builder.build(step.task_id, step.id, artifact_ids=input_ids, profile=spec.context_profile, max_chars=spec.budget.max_context_chars)
        attempt = len(previous) + 1
        provider_key: str | None = None
        if spec.executor_kind == "model":
            if self.model_router is None:
                outcome = CapabilityOutcome("blocked", error="model capability has no configured model router")
            else:
                model_calls = sum(1 for execution in self.store.list_executions(step.task_id) if execution.provider)
                if model_calls >= self.budget.max_model_calls:
                    outcome = CapabilityOutcome("blocked", error="task model-call budget exhausted")
                else:
                    failed_providers = tuple(execution.provider for execution in previous if execution.status in {"abstain", "fail"} and execution.provider)
                    route = self.model_router.select(spec, context_chars=pack.chars, exclude_provider_keys=failed_providers)
                    provider_key = route.provider.spec.key
                    execution = self.store.begin_execution(step.task_id, step_id=step.id, capability=spec.id, provider=provider_key, input_artifact_ids=input_ids)
                    self._emit(step.task_id, "capability.started", step_id=step.id, execution_id=execution.id, payload={"capability": spec.id, "provider": provider_key, "attempt": attempt, "route": route.reason})
                    try:
                        response = route.provider.generate(ModelRequest(capability_id=spec.id, system=pack.payload["context_profile"]["instruction"], input=pack.as_text(), max_output_chars=spec.budget.max_output_chars, metadata={"task_id": step.task_id, "step_id": step.id}))
                        outcome = CapabilityOutcome("pass", output=response.text, output_kind=spec.output_kind, metrics=response.metrics, receipt={"ok": True, "provider": response.provider_key, "model": response.model})
                    except Exception as exc:
                        outcome = CapabilityOutcome("abstain", error=str(exc), receipt={"ok": False, "provider": provider_key})
                    self._finish_frame(step, spec, execution.id, pack.payload, outcome)
                    return
        elif spec.executor_kind == "human":
            self._request_authority_approval(step, spec.required_authority)
            return
        else:
            request = CapabilityRequest(step.task_id, step.id, spec.id, pack.payload, input_ids, attempt)
            try:
                outcome = binding.handler(request) if binding.handler is not None else CapabilityOutcome("fail", error="capability has no handler")
            except Exception as exc:
                outcome = CapabilityOutcome("fail", error=str(exc))
        execution = self.store.begin_execution(step.task_id, step_id=step.id, capability=spec.id, provider=provider_key, input_artifact_ids=input_ids)
        self._emit(step.task_id, "capability.started", step_id=step.id, execution_id=execution.id, payload={"capability": spec.id, "provider": provider_key, "attempt": attempt})
        self._finish_frame(step, spec, execution.id, pack.payload, outcome)

    def _finish_frame(self, step: StepRecord, spec, execution_id: str, context: dict[str, Any], outcome: CapabilityOutcome) -> None:
        output_ids: list[str] = []
        if outcome.output is not None:
            if spec.budget.max_output_chars is not None:
                size = len(json.dumps(outcome.output, ensure_ascii=False, default=str))
                if size > spec.budget.max_output_chars:
                    outcome = CapabilityOutcome("rework", error=f"output exceeds explicit budget: {size}>{spec.budget.max_output_chars}")
            if outcome.output is not None and outcome.status in {"pass", "rework", "abstain"}:
                artifact = self.store.put_artifact(step.task_id, step_id=step.id, kind=outcome.output_kind or spec.output_kind, payload=outcome.output, metadata={"capability": spec.id, "execution_id": execution_id})
                output_ids.append(artifact.id)
        verifier_artifact_id = None
        final_status = outcome.status
        if outcome.status == "pass" and spec.verification_required:
            verification_context = dict(context)
            verification_context["execution_receipt"] = outcome.receipt
            verification = self.verifiers.verify(spec.verifier_id or "", spec, outcome.output, verification_context)
            verifier = self.store.put_artifact(step.task_id, step_id=step.id, kind="verification_result", payload={"status": verification.status, "summary": verification.summary, "details": verification.details}, metadata={"verifier_id": spec.verifier_id, "execution_id": execution_id})
            verifier_artifact_id = verifier.id
            final_status = verification.status
            self._emit(step.task_id, "verification.completed", step_id=step.id, execution_id=execution_id, payload={"status": verification.status, "summary": verification.summary})
        execution = self.store.finish_execution(execution_id, status=final_status, output_artifact_ids=output_ids, verifier_artifact_id=verifier_artifact_id, receipt=outcome.receipt, metrics=outcome.metrics, error=outcome.error)
        if final_status in spec.retry_policy.retry_on and execution.attempt < spec.budget.max_attempts:
            if spec.idempotent or not spec.side_effects:
                current_step = self.store.get_step(step.id)
                if current_step.status == "blocked":
                    self.store.set_step_status(step.id, "pending")
            elif final_status != "pass":
                current_step = self.store.get_step(step.id)
                if current_step.status not in {"failed", "pass", "skipped"}:
                    self.store.set_step_status(step.id, "failed")
                self._emit(step.task_id, "retry.blocked", step_id=step.id, execution_id=execution_id, payload={"reason": "non-idempotent side effect cannot be retried automatically"})
        elif final_status == "rework" and final_status not in spec.retry_policy.retry_on:
            self.store.set_step_status(step.id, "failed")
        evidence_ids = tuple(output_ids) or tuple(execution.input_artifact_ids)
        for claim in outcome.claims:
            claim_kind = str(claim.get("kind", "inferred"))
            claim_evidence = tuple(claim.get("evidence_artifact_ids") or evidence_ids)
            self.store.add_claim(step.task_id, step_id=step.id, kind=claim_kind, subject=str(claim.get("subject") or spec.id), value=claim.get("value"), evidence_artifact_ids=claim_evidence, confidence=claim.get("confidence"))
        if final_status == "pass":
            self._accept_declared_criteria(step, output_ids, spec.metadata)
        self.store.create_checkpoint(step.task_id, reason=f"step {step.id} execution {execution.attempt} -> {final_status}")
        self._emit(step.task_id, "capability.completed", step_id=step.id, execution_id=execution_id, payload={"capability": spec.id, "status": final_status, "outputs": output_ids})

    def _accept_declared_criteria(self, step: StepRecord, output_ids: list[str], metadata: dict[str, Any]) -> None:
        criteria = self.store.list_criteria(step.task_id)
        ordinals = set(step.metadata.get("satisfies_criteria", ())) | set(metadata.get("satisfies_criteria", ()))
        if step.metadata.get("accept_all_criteria") or metadata.get("accept_all_criteria"):
            ordinals = {criterion.ordinal for criterion in criteria}
        for criterion in criteria:
            if criterion.ordinal in ordinals and criterion.status != "accepted":
                self.store.set_criterion_status(criterion.id, "accepted", evidence_artifact_ids=output_ids, note=f"satisfied by step {step.id}")

    def _settle_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        steps = self.store.list_steps(task_id)
        if any(step.status == "failed" for step in steps):
            if task.status not in {"failed", "cancelled", "completed"}:
                self.store.set_task_status(task_id, "failed")
                self._emit(task_id, "task.failed", payload={"reason": "one or more required steps failed"})
            return
        decision = self.completion.evaluate(task_id)
        if decision.complete:
            if task.status != "completed":
                self.store.set_task_status(task_id, "completed")
                self.store.create_checkpoint(task_id, reason="task completed")
                self._emit(task_id, "task.completed")
            return
        if not self.store.ready_steps(task_id) and task.status == "active":
            self.store.set_task_status(task_id, "waiting")
