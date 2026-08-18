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

class RuntimeLifecycleMixin:
    def _emit(
        self,
        task_id: str,
        name: str,
        *,
        step_id: str | None = None,
        execution_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        data = payload or {}
        self.store.append_event(
            task_id,
            name=name,
            step_id=step_id,
            execution_id=execution_id,
            payload=data,
        )
        self.event_bus.emit(
            RuntimeEvent(name, task_id, step_id, execution_id, data)
        )

    def run_until_blocked(self, task_id: str) -> RuntimeResult:
        task = self.store.get_task(task_id)
        if task.status in {"completed", "failed", "cancelled"}:
            return RuntimeResult(
                task_id,
                task.status,
                0,
                len(self.store.list_executions(task_id)),
                "task already terminal",
            )
        if task.status == "planned":
            self.store.set_task_status(task_id, "active")
            self._emit(task_id, "task.started")

        cycles = 0
        while cycles < self.budget.max_cycles:
            cycles += 1
            before = len(self.store.list_executions(task_id))
            if before >= self.budget.max_executions:
                self.store.set_task_status(task_id, "failed")
                self._emit(
                    task_id,
                    "task.failed",
                    payload={"reason": "task execution budget exhausted"},
                )
                return RuntimeResult(
                    task_id,
                    "failed",
                    cycles,
                    before,
                    "task execution budget exhausted",
                )

            remaining = self.budget.max_executions - before
            progressed = self.run_once(task_id, max_new_executions=remaining)
            after = len(self.store.list_executions(task_id))
            task = self.store.get_task(task_id)
            if task.status in {"completed", "failed", "cancelled"}:
                return RuntimeResult(
                    task_id,
                    task.status,
                    cycles,
                    after,
                    "task reached terminal state",
                )
            if not progressed:
                if task.status != "waiting":
                    self.store.set_task_status(task_id, "waiting")
                self._emit(
                    task_id,
                    "task.paused",
                    payload={"reason": "no executable ready steps"},
                )
                return RuntimeResult(
                    task_id,
                    "waiting",
                    cycles,
                    after,
                    "no executable ready steps",
                )

        if self.store.get_task(task_id).status == "active":
            self.store.set_task_status(task_id, "waiting")
        self._emit(
            task_id,
            "task.paused",
            payload={"reason": "runtime cycle budget reached"},
        )
        return RuntimeResult(
            task_id,
            "waiting",
            cycles,
            len(self.store.list_executions(task_id)),
            "runtime cycle budget reached",
        )

    def run_once(
        self,
        task_id: str,
        *,
        max_new_executions: int | None = None,
    ) -> bool:
        task = self.store.get_task(task_id)
        if task.status == "waiting":
            self.store.set_task_status(task_id, "active")
            self._emit(task_id, "task.resumed")

        self._release_approval_blocks(task_id)
        ready = list(self.store.ready_steps(task_id))
        if max_new_executions is not None:
            ready = ready[: max(0, max_new_executions)]
        if not ready:
            self._settle_task(task_id)
            return False

        parallel: list[StepRecord] = []
        serial: list[StepRecord] = []
        for step in ready:
            try:
                spec = self.capabilities.get(step.capability or "").spec
                is_parallel = spec.parallel_safe
            except CapabilityRegistryError:
                is_parallel = False
            (parallel if is_parallel else serial).append(step)

        progressed = False
        for step in serial:
            try:
                progressed = self._execute_step(step) or progressed
            except InvalidTransitionError:
                # Another process may have claimed this step after readiness was read.
                continue

        if parallel:
            workers = max(
                1,
                min(self.budget.max_parallel_workers, len(parallel)),
            )
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="atlas-step",
            ) as pool:
                futures = {
                    pool.submit(self._execute_step, step): step
                    for step in parallel
                }
                for future in as_completed(futures):
                    try:
                        progressed = bool(future.result()) or progressed
                    except InvalidTransitionError:
                        continue

        self._settle_task(task_id)
        return progressed

    def _spent_cost_usd(self, task_id: str) -> float:
        total = 0.0
        for execution in self.store.list_executions(task_id):
            value = execution.metrics.get("estimated_cost_usd")
            if value is not None:
                try:
                    total += float(value)
                except (TypeError, ValueError):
                    continue
        return total

    def resume_blocked(self, task_id: str) -> int:
        """Make safely retryable blocked steps pending for a new explicit run."""
        if self.store.get_task(task_id).status in {"completed", "failed", "cancelled"}:
            return 0
        pending_approval_steps = {
            approval.step_id
            for approval in self.store.list_approvals(task_id, status="pending")
            if approval.step_id is not None
        }
        resumed = 0
        for step in self.store.list_steps(task_id):
            if step.status != "blocked" or step.id in pending_approval_steps:
                continue
            try:
                spec = self.capabilities.get(step.capability or "").spec
            except CapabilityRegistryError:
                continue
            attempts = self.store.list_executions(task_id, step_id=step.id)
            if len(attempts) >= spec.budget.max_attempts:
                self.store.set_step_status(step.id, "failed")
                continue
            if spec.side_effects and not spec.idempotent:
                self.store.set_step_status(step.id, "failed")
                self._emit(
                    task_id,
                    "retry.blocked",
                    step_id=step.id,
                    payload={"reason": "non-idempotent blocked action requires explicit recovery design"},
                )
                continue
            self.store.set_step_status(step.id, "pending")
            resumed += 1
            self._emit(task_id, "step.retry_ready", step_id=step.id)
        if resumed and self.store.get_task(task_id).status == "waiting":
            self.store.set_task_status(task_id, "active")
        if resumed:
            self.store.create_checkpoint(task_id, reason="explicit blocked-step resume")
        self._settle_task(task_id)
        return resumed

    def recover_interrupted(self, task_id: str) -> RecoveryResult:
        """Explicitly resolve executions left running by an interrupted process."""
        task = self.store.get_task(task_id)
        if task.status in {"completed", "cancelled"}:
            return RecoveryResult(task_id, 0, 0, task.status)

        recovered = 0
        failed_closed = 0
        for execution in self.store.list_executions(task_id):
            if execution.status != "running":
                continue
            try:
                binding = self.capabilities.get(execution.capability)
                spec = binding.spec
            except CapabilityRegistryError:
                spec = None

            if spec is None or (spec.side_effects and not spec.idempotent):
                self.store.finish_execution(
                    execution.id,
                    status="fail",
                    error=(
                        "interrupted execution cannot be replayed safely; "
                        "external side-effect state may be unknown"
                    ),
                )
                failed_closed += 1
                self._emit(
                    task_id,
                    "recovery.failed_closed",
                    step_id=execution.step_id,
                    execution_id=execution.id,
                    payload={"capability": execution.capability},
                )
                continue

            self.store.finish_execution(
                execution.id,
                status="abstain",
                error="execution interrupted before a terminal outcome was recorded",
            )
            attempts = self.store.list_executions(
                task_id,
                step_id=execution.step_id,
            )
            if len(attempts) < spec.budget.max_attempts:
                self.store.set_step_status(execution.step_id, "pending")
                recovered += 1
                self._emit(
                    task_id,
                    "recovery.retry_ready",
                    step_id=execution.step_id,
                    execution_id=execution.id,
                    payload={"capability": execution.capability},
                )
            else:
                self.store.set_step_status(execution.step_id, "failed")
                failed_closed += 1
                self._emit(
                    task_id,
                    "recovery.attempt_limit",
                    step_id=execution.step_id,
                    execution_id=execution.id,
                    payload={"capability": execution.capability},
                )

        self.store.create_checkpoint(task_id, reason="explicit interrupted-execution recovery")
        if failed_closed:
            current = self.store.get_task(task_id)
            if current.status not in {"failed", "cancelled", "completed"}:
                self.store.set_task_status(task_id, "failed")
        elif recovered and self.store.get_task(task_id).status == "waiting":
            self.store.set_task_status(task_id, "active")
        self._settle_task(task_id)
        return RecoveryResult(
            task_id,
            recovered,
            failed_closed,
            self.store.get_task(task_id).status,
        )

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
                self._emit(
                    task_id,
                    "step.failed",
                    step_id=step.id,
                    payload={"reason": "required approval denied"},
                )
            elif any(item.status == "approved" for item in decisions):
                self.store.set_step_status(step.id, "pending")
                self._emit(task_id, "approval.applied", step_id=step.id)

    def _approved_for_step(
        self,
        step: StepRecord,
        required_authority: str,
    ) -> Any | None:
        approved = [
            approval
            for approval in self.store.list_approvals(
                step.task_id,
                status="approved",
            )
            if approval.step_id == step.id
            and approval.required_authority == required_authority
        ]
        return approved[-1] if approved else None

    def _request_authority_approval(
        self,
        step: StepRecord,
        required_authority: str,
        *,
        requested_action: str | None = None,
    ) -> None:
        pending = [
            approval
            for approval in self.store.list_approvals(
                step.task_id,
                status="pending",
            )
            if approval.step_id == step.id
            and approval.required_authority == required_authority
        ]
        if not pending:
            approval = self.store.request_approval(
                step.task_id,
                step_id=step.id,
                required_authority=required_authority,
                requested_action=(
                    requested_action
                    or f"Allow capability {step.capability} for step: {step.description}"
                ),
            )
            self._emit(
                step.task_id,
                "approval.requested",
                step_id=step.id,
                payload={
                    "approval_id": approval.id,
                    "required_authority": required_authority,
                },
            )
        if self.store.get_step(step.id).status != "blocked":
            self.store.set_step_status(step.id, "blocked")
