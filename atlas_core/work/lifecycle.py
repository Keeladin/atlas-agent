from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from atlas_core.events import RuntimeEvent
from atlas_core.runtime_types import RecoveryResult, RuntimeResult
from atlas_core.tasks import InvalidTransitionError, StepRecord

from .contract import WorkContract
from .resolve import ResolveReport
from .work import UNAVAILABLE, WorkError


class WorkLifecycleMixin:
    def _emit(
        self,
        work_id: str,
        name: str,
        *,
        step_id: str | None = None,
        execution_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        data = payload or {}
        self.store.append_event(
            work_id,
            name=name,
            step_id=step_id,
            execution_id=execution_id,
            payload=data,
        )
        self.event_bus.emit(
            RuntimeEvent(name, work_id, step_id, execution_id, data)
        )

    def run_until_blocked(
        self,
        contract: WorkContract,
        report: ResolveReport,
    ) -> RuntimeResult:
        work_id = contract.work_id
        _require_report_matches_contract(contract, report)
        task = self.store.get_task(work_id)
        if task.status in {"completed", "failed", "cancelled"}:
            return RuntimeResult(
                work_id,
                task.status,
                0,
                len(self.store.list_executions(work_id)),
                "task already terminal",
            )
        if report.unarmed:
            return RuntimeResult(work_id, task.status, 0, 0, UNAVAILABLE)
        if self._membership_violation(contract):
            return self._fail_membership(work_id)

        budget = contract.work_budget
        if task.status == "planned":
            self.store.set_task_status(work_id, "active")
            self._emit(work_id, "task.started")

        cycles = 0
        while cycles < budget.max_cycles:
            cycles += 1
            before = len(self.store.list_executions(work_id))
            if before >= budget.max_executions:
                self.store.set_task_status(work_id, "failed")
                self._emit(
                    work_id,
                    "task.failed",
                    payload={"reason": "task execution budget exhausted"},
                )
                return RuntimeResult(
                    work_id,
                    "failed",
                    cycles,
                    before,
                    "task execution budget exhausted",
                )

            remaining = budget.max_executions - before
            progressed = self.run_once(
                contract, report, max_new_executions=remaining
            )
            after = len(self.store.list_executions(work_id))
            task = self.store.get_task(work_id)
            if task.status in {"completed", "failed", "cancelled"}:
                return RuntimeResult(
                    work_id,
                    task.status,
                    cycles,
                    after,
                    "task reached terminal state",
                )
            if not progressed:
                if task.status != "waiting":
                    self.store.set_task_status(work_id, "waiting")
                self._emit(
                    work_id,
                    "task.paused",
                    payload={"reason": "no executable ready steps"},
                )
                return RuntimeResult(
                    work_id,
                    "waiting",
                    cycles,
                    after,
                    "no executable ready steps",
                )

        if self.store.get_task(work_id).status == "active":
            self.store.set_task_status(work_id, "waiting")
        self._emit(
            work_id,
            "task.paused",
            payload={"reason": "runtime cycle budget reached"},
        )
        return RuntimeResult(
            work_id,
            "waiting",
            cycles,
            len(self.store.list_executions(work_id)),
            "runtime cycle budget reached",
        )

    def run_once(
        self,
        contract: WorkContract,
        report: ResolveReport,
        *,
        max_new_executions: int | None = None,
    ) -> bool:
        work_id = contract.work_id
        _require_report_matches_contract(contract, report)
        if report.unarmed:
            return False
        if self._membership_violation(contract):
            self._fail_membership(work_id)
            return False

        task = self.store.get_task(work_id)
        if task.status == "waiting":
            self.store.set_task_status(work_id, "active")
            self._emit(work_id, "task.resumed")

        self._release_approval_blocks(work_id)
        self._fail_mismatches(work_id, report)
        ready = list(self.store.ready_steps(work_id))
        if max_new_executions is not None:
            ready = ready[: max(0, max_new_executions)]
        if not ready:
            self._settle_work(work_id)
            return False

        parallel: list[StepRecord] = []
        serial: list[StepRecord] = []
        for step in ready:
            pin = contract.capability(step.capability or "")
            (parallel if pin.parallel_safe else serial).append(step)

        progressed = False
        for step in serial:
            try:
                progressed = (
                    self._execute_step(step, contract, report) or progressed
                )
            except InvalidTransitionError:
                continue

        if parallel:
            workers = max(
                1,
                min(contract.work_budget.max_parallel_workers, len(parallel)),
            )
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="atlas-work-step",
            ) as pool:
                futures = {
                    pool.submit(self._execute_step, step, contract, report): step
                    for step in parallel
                }
                for future in as_completed(futures):
                    try:
                        progressed = bool(future.result()) or progressed
                    except InvalidTransitionError:
                        continue

        self._settle_work(work_id)
        return progressed

    def resume_blocked(
        self,
        contract: WorkContract,
        report: ResolveReport,
    ) -> int:
        work_id = contract.work_id
        _require_report_matches_contract(contract, report)
        if self.store.get_task(work_id).status in {"completed", "failed", "cancelled"}:
            return 0
        pending_approval_steps = {
            approval.step_id
            for approval in self.store.list_approvals(work_id, status="pending")
            if approval.step_id is not None
        }
        resumed = 0
        for step in self.store.list_steps(work_id):
            if step.status != "blocked" or step.id in pending_approval_steps:
                continue
            try:
                pin = contract.capability(step.capability or "")
            except WorkError:
                continue
            if not pin.armed or pin.budget is None:
                continue
            if step.capability not in report.resolved.capabilities:
                self.store.set_step_status(step.id, "failed")
                continue
            attempts = self.store.list_executions(work_id, step_id=step.id)
            if len(attempts) >= pin.budget.max_attempts:
                self.store.set_step_status(step.id, "failed")
                continue
            if pin.side_effects and not pin.idempotent:
                self.store.set_step_status(step.id, "failed")
                self._emit(
                    work_id,
                    "retry.blocked",
                    step_id=step.id,
                    payload={
                        "reason": (
                            "non-idempotent blocked action requires explicit recovery design"
                        )
                    },
                )
                continue
            self.store.set_step_status(step.id, "pending")
            resumed += 1
            self._emit(work_id, "step.retry_ready", step_id=step.id)
        if resumed and self.store.get_task(work_id).status == "waiting":
            self.store.set_task_status(work_id, "active")
        if resumed:
            self.store.create_checkpoint(work_id, reason="explicit blocked-step resume")
        self._settle_work(work_id)
        return resumed

    def recover_interrupted(
        self,
        contract: WorkContract,
        report: ResolveReport,
    ) -> RecoveryResult:
        work_id = contract.work_id
        _require_report_matches_contract(contract, report)
        task = self.store.get_task(work_id)
        if task.status in {"completed", "cancelled"}:
            return RecoveryResult(work_id, 0, 0, task.status)

        recovered = 0
        failed_closed = 0
        for execution in self.store.list_executions(work_id):
            if execution.status != "running":
                continue
            pin = None
            try:
                pin = contract.capability(execution.capability)
            except WorkError:
                pin = None
            resolved = report.resolved.capabilities.get(execution.capability)
            unsafe = (
                pin is None
                or not pin.armed
                or pin.budget is None
                or resolved is None
                or (pin.side_effects and not pin.idempotent)
            )
            if unsafe:
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
                    work_id,
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
                work_id,
                step_id=execution.step_id,
            )
            if len(attempts) < pin.budget.max_attempts:
                self.store.set_step_status(execution.step_id, "pending")
                recovered += 1
                self._emit(
                    work_id,
                    "recovery.retry_ready",
                    step_id=execution.step_id,
                    execution_id=execution.id,
                    payload={"capability": execution.capability},
                )
            else:
                self.store.set_step_status(execution.step_id, "failed")
                failed_closed += 1
                self._emit(
                    work_id,
                    "recovery.attempt_limit",
                    step_id=execution.step_id,
                    execution_id=execution.id,
                    payload={"capability": execution.capability},
                )

        self.store.create_checkpoint(
            work_id, reason="explicit interrupted-execution recovery"
        )
        if failed_closed:
            current = self.store.get_task(work_id)
            if current.status not in {"failed", "cancelled", "completed"}:
                self.store.set_task_status(work_id, "failed")
        elif recovered and self.store.get_task(work_id).status == "waiting":
            self.store.set_task_status(work_id, "active")
        self._settle_work(work_id)
        return RecoveryResult(
            work_id,
            recovered,
            failed_closed,
            self.store.get_task(work_id).status,
        )

    def _membership_violation(self, contract: WorkContract) -> bool:
        steps = self.store.list_steps(contract.work_id)
        if any(not step.capability for step in steps):
            return True
        step_ids = {step.capability for step in steps}
        contract_ids = {pin.capability_id for pin in contract.capabilities}
        return step_ids != contract_ids

    def _fail_membership(self, work_id: str) -> RuntimeResult:
        task = self.store.get_task(work_id)
        if task.status not in {"failed", "cancelled", "completed"}:
            self.store.set_task_status(work_id, "failed")
            self._emit(
                work_id,
                "task.failed",
                payload={"reason": "work steps do not match the contract"},
            )
        return RuntimeResult(
            work_id,
            "failed",
            0,
            len(self.store.list_executions(work_id)),
            "work steps do not match the contract",
        )

    def _fail_mismatches(self, work_id: str, report: ResolveReport) -> None:
        by_capability = {
            step.capability: step
            for step in self.store.list_steps(work_id)
            if step.capability
        }
        for mismatch in report.mismatches:
            step = by_capability.get(mismatch.capability_id)
            if step is None or step.status not in {"pending", "rework", "blocked"}:
                continue
            execution = self.store.begin_execution(
                work_id,
                step_id=step.id,
                capability=mismatch.capability_id,
                capability_version=step.capability_version or "0.0.0",
            )
            self.store.finish_execution(
                execution.id,
                status="fail",
                error=f"resolve mismatch: {mismatch.reason}",
            )

    def _release_approval_blocks(self, work_id: str) -> None:
        approvals = self.store.list_approvals(work_id)
        by_step: dict[str, list[Any]] = {}
        for approval in approvals:
            if approval.step_id is not None:
                by_step.setdefault(approval.step_id, []).append(approval)
        for step in self.store.list_steps(work_id):
            if step.status != "blocked":
                continue
            decisions = by_step.get(step.id, [])
            if any(item.status == "denied" for item in decisions):
                self.store.set_step_status(step.id, "failed")
                self._emit(
                    work_id,
                    "step.failed",
                    step_id=step.id,
                    payload={"reason": "required approval denied"},
                )
            elif any(item.status == "approved" for item in decisions):
                self.store.set_step_status(step.id, "pending")
                self._emit(work_id, "approval.applied", step_id=step.id)

    def _approved_for_step(self, step: StepRecord, required_authority: str) -> Any | None:
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

    def _settle_work(self, work_id: str) -> None:
        task = self.store.get_task(work_id)
        steps = self.store.list_steps(work_id)
        if any(step.status == "failed" for step in steps):
            if task.status not in {"failed", "cancelled", "completed"}:
                self.store.set_task_status(work_id, "failed")
                self._emit(
                    work_id,
                    "task.failed",
                    payload={"reason": "one or more required steps failed"},
                )
            return
        decision = self.completion.evaluate(work_id)
        if decision.complete:
            if task.status != "completed":
                self.store.set_task_status(work_id, "completed")
                self.store.create_checkpoint(work_id, reason="task completed")
                self._emit(work_id, "task.completed")
            return
        if decision.status == "failed" and task.status not in {
            "failed",
            "cancelled",
            "completed",
        }:
            self.store.set_task_status(work_id, "failed")
            self.store.create_checkpoint(work_id, reason="completion rejected")
            self._emit(
                work_id,
                "task.failed",
                payload={"reason": "; ".join(decision.reasons) or "completion rejected"},
            )
            return
        if not self.store.ready_steps(work_id) and task.status == "active":
            self.store.set_task_status(work_id, "waiting")


def _require_report_matches_contract(
    contract: WorkContract, report: ResolveReport
) -> None:
    resolved = report.resolved.contract
    if resolved.work_id != contract.work_id or resolved.sha256 != contract.sha256:
        raise WorkError("resolve report does not belong to this contract")
