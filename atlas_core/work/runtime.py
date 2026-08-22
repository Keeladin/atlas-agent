from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from atlas_core.advanced.brief import TaskBrief
from atlas_core.capabilities.definition import CapabilityDefinition, catalog
from atlas_core.runtime_types import RecoveryResult, RuntimeBudget, RuntimeResult
from .store_common import _new_id
from atlas_core.providers import ModelRouter
from atlas_core.tools import ToolGateway
from atlas_core.verification import VerifierRegistry

from .contract import (
    ContractCapability,
    WorkContract,
    compile_contract,
    work_contract_from_stored,
)
from .engine import WorkEngine
from .inventory import DeploymentInventory
from .resolve import ImplementationResolver
from .model import WorkModelConsumer
from .availability import refuse_if_unexecutable
from .control import (
    control_patch,
    is_archived,
    is_pause_requested,
    is_paused,
    running_executions,
)
from .store import InvalidTransitionError, UnknownRecordError, WorkStore, WorkStoreError
from .work import TERMINAL_STATUSES, UNAVAILABLE, WorkError, WorkId, WorkRecord


DEFAULT_WORK_DB = Path("instance/atlas-work.db")


class WorkRuntime:
    """Work composition boundary. WorkEngine executes accepted contracts."""

    def __init__(
        self,
        *,
        store: WorkStore,
        inventory: DeploymentInventory,
        tools: ToolGateway,
        engine: WorkEngine,
        budget: RuntimeBudget | None = None,
        verifiers: VerifierRegistry | None = None,
        definitions: tuple[CapabilityDefinition, ...] | None = None,
        providers=None,
    ) -> None:
        self.store = store
        self._profiles = inventory
        self._tool_gateway = tools
        self._providers = providers
        self._engine = engine
        self._budget = budget or RuntimeBudget()
        self._verifiers = verifiers or VerifierRegistry()
        self._definitions = {
            item.id: item for item in (definitions if definitions is not None else catalog())
        }

    def accept(
        self,
        brief: TaskBrief,
        authority_scope: str,
        *,
        inputs: Mapping[str, dict[str, Any]] | None = None,
    ) -> WorkId:
        requested = _validated_inputs(brief, inputs)
        work_id = _new_id("work")
        contract = compile_contract(
            work_id=work_id,
            brief=brief,
            authority_scope=authority_scope,
            inventory=self._profiles,
            tools=self._tool_gateway,
            providers=self._providers,
            work_budget=self._budget,
        )
        refuse_if_unexecutable(
            brief=brief,
            contract=contract,
            inventory=self._profiles,
            tools=self._tool_gateway,
        )
        store = self.store
        store.create_work(
            objective=brief.objective,
            success_criteria=contract.success_criteria,
            constraints=brief.constraints,
            authority_scope=contract.authority_scope,
            metadata={
                "source": "task_brief",
                "brief": brief.as_dict(),
                "contract_id": contract.contract_id,
            },
            work_id=work_id,
        )
        try:
            store.insert_work_contract(
                work_id=contract.work_id,
                contract_id=contract.contract_id,
                sha256=contract.sha256,
                payload=contract.as_payload(),
                compiled_at=contract.compiled_at,
            )
        except WorkStoreError as exc:
            raise WorkError(str(exc)) from exc
        store.put_artifact(
            work_id,
            kind="task_brief",
            payload=brief.as_dict(),
            metadata={"purpose": "accepted_brief"},
        )
        dependencies = _kind_match_dependencies(contract.capabilities)
        step_ids: dict[str, str] = {}
        for pin in contract.capabilities:
            input_ids: tuple[str, ...] = ()
            if pin.capability_id in requested:
                request = store.put_artifact(
                    work_id,
                    kind=f"{pin.capability_id.replace('.', '_')}_request",
                    payload=requested[pin.capability_id],
                    metadata={
                        "purpose": "accepted_request",
                        "capability": pin.capability_id,
                    },
                )
                input_ids = (request.id,)
            record = store.add_step(
                work_id,
                description=pin.definition.description,
                capability=pin.capability_id,
                capability_version=pin.profile_version,
                dependencies=[step_ids[dep] for dep in dependencies[pin.capability_id]],
                input_artifact_ids=input_ids,
                metadata={
                    "accept_all_criteria": True,
                    "allowed_tools": list(pin.tools),
                },
            )
            step_ids[pin.capability_id] = record.id
        store.create_checkpoint(work_id, reason="work accepted from task brief")
        return work_id

    def run(self, work_id: WorkId) -> RuntimeResult:
        state = self.store.get_work(work_id)
        if is_archived(state):
            raise WorkError("archived work cannot run")
        if is_paused(state) or is_pause_requested(state):
            self.store.merge_work_metadata(
                work_id,
                control_patch(paused=False, pause_requested=False),
            )
            self._engine._emit(work_id, "work.resumed", payload={"reason": "owner_requested"})
        contract = self.contract(work_id)
        report = ImplementationResolver().resolve(
            contract, self._profiles, self._tool_gateway
        )
        if report.unarmed:
            record = self.get(work_id)
            return RuntimeResult(work_id, record.status, 0, 0, UNAVAILABLE)
        return self._engine.run(contract, report)

    def resume(self, work_id: WorkId) -> int:
        state = self.store.get_work(work_id)
        if is_archived(state):
            raise WorkError("archived work cannot run")
        contract = self.contract(work_id)
        report = ImplementationResolver().resolve(
            contract, self._profiles, self._tool_gateway
        )
        return self._engine.resume(contract, report)

    def pause(self, work_id: WorkId) -> WorkRecord:
        state = self.store.get_work(work_id)
        if state.status in TERMINAL_STATUSES:
            raise WorkError(f"work is already {state.status}")
        if is_archived(state):
            raise WorkError("archived work cannot be paused")
        running = running_executions(self.store.list_executions(work_id))
        if running:
            if is_pause_requested(state) and not is_paused(state):
                return self.get(work_id)
            self.store.merge_work_metadata(
                work_id, control_patch(pause_requested=True, paused=False)
            )
            self._engine._emit(
                work_id,
                "work.pause_requested",
                payload={"reason": "owner_requested", "in_flight": True},
            )
            return self.get(work_id)
        if is_paused(state):
            return self.get(work_id)
        if state.status == "active":
            self.store.set_work_status(work_id, "waiting")
        self.store.merge_work_metadata(
            work_id, control_patch(paused=True, pause_requested=False)
        )
        self._engine._emit(
            work_id, "work.paused", payload={"reason": "owner_requested"}
        )
        return self.get(work_id)

    def archive(self, work_id: WorkId, *, archived: bool = True) -> WorkRecord:
        state = self.store.get_work(work_id)
        if running_executions(self.store.list_executions(work_id)):
            raise WorkError("cannot archive while an action is in flight")
        if archived and is_pause_requested(state):
            raise WorkError("cannot archive while a pause is in progress")
        try:
            self.store.merge_work_metadata(
                work_id, control_patch(archived=archived), require_idle=archived
            )
        except InvalidTransitionError as exc:
            raise WorkError(str(exc)) from exc
        self._engine._emit(
            work_id,
            "work.archived" if archived else "work.unarchived",
            payload={"reason": "owner_requested"},
        )
        return self.get(work_id)

    def delete(self, work_id: WorkId) -> None:
        state = self.store.get_work(work_id)
        if running_executions(self.store.list_executions(work_id)):
            raise WorkError("cannot delete while an action is in flight")
        if is_pause_requested(state):
            raise WorkError("cannot delete while a pause is in progress")
        try:
            self.store.delete_work(work_id, require_idle=True)
        except InvalidTransitionError as exc:
            raise WorkError(str(exc)) from exc

    def recover(self, work_id: WorkId) -> RecoveryResult:
        contract = self.contract(work_id)
        report = ImplementationResolver().resolve(
            contract, self._profiles, self._tool_gateway
        )
        return self._engine.recover(contract, report)

    def approve(self, approval_id: str, *, note: str | None = None):
        return self.store.decide_approval(approval_id, status="approved", note=note)

    def deny(self, approval_id: str, *, note: str | None = None):
        return self.store.decide_approval(approval_id, status="denied", note=note)

    def list_pending_confirmations(self, work_id: WorkId):
        return self.store.list_confirmations(work_id, status="pending")

    def confirm_payload(self, confirmation_id: str):
        return self.store.decide_confirmation(confirmation_id, status="confirmed")

    def deny_confirmation(self, confirmation_id: str):
        return self.store.decide_confirmation(confirmation_id, status="denied")

    def cancel_confirmation(self, confirmation_id: str):
        return self.store.decide_confirmation(confirmation_id, status="cancelled")

    def get(self, work_id: WorkId) -> WorkRecord:
        task = self.store.get_work(work_id)
        contract = self.contract(work_id)
        return WorkRecord(
            id=task.id,
            objective=task.objective,
            status=task.status,
            authority_scope=task.authority_scope,
            capabilities=tuple(pin.capability_id for pin in contract.capabilities),
        )

    def contract(self, work_id: WorkId) -> WorkContract:
        try:
            row = self.store.load_work_contract_row(work_id)
        except UnknownRecordError as exc:
            raise WorkError(str(exc)) from exc
        except WorkStoreError as exc:
            raise WorkError(str(exc)) from exc
        return work_contract_from_stored(
            work_id=row["work_id"],
            contract_id=row["contract_id"],
            sha256=row["sha256"],
            payload=row["payload"],
            compiled_at=row["compiled_at"],
        )


def _validated_inputs(
    brief: TaskBrief,
    inputs: Mapping[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if inputs is None:
        return {}
    allowed = set(brief.capabilities)
    requested: dict[str, dict[str, Any]] = {}
    for raw_key, payload in inputs.items():
        capability_id = str(raw_key)
        if capability_id not in allowed:
            raise WorkError(
                f"input is not in the accepted brief: {capability_id}"
            )
        if not isinstance(payload, dict):
            raise WorkError(f"input for {capability_id} must be an object")
        requested[capability_id] = payload
    return requested


def _kind_match_dependencies(
    pins: tuple[ContractCapability, ...],
) -> dict[str, tuple[str, ...]]:
    dependencies: dict[str, tuple[str, ...]] = {}
    for index, later in enumerate(pins):
        needed = {kind for kind in later.requires_artifact_kinds if kind}
        matched: list[str] = []
        if needed:
            for earlier in pins[:index]:
                if earlier.output_kind and earlier.output_kind in needed:
                    matched.append(earlier.capability_id)
        dependencies[later.capability_id] = tuple(matched)
    return dependencies


def build_work_runtime(
    *,
    db_path: str | Path = DEFAULT_WORK_DB,
    tool_gateway: ToolGateway | None = None,
    profiles: DeploymentInventory | None = None,
    budget: RuntimeBudget | None = None,
    verifiers: VerifierRegistry | None = None,
    model_router: ModelRouter | None = None,
) -> WorkRuntime:
    """Only composition root for WorkRuntime."""

    store = WorkStore(db_path)
    store.initialize()
    profile_index = profiles if profiles is not None else DeploymentInventory()
    gateway = tool_gateway if tool_gateway is not None else ToolGateway()
    verifier_registry = verifiers if verifiers is not None else VerifierRegistry()
    consumer = None if model_router is None else WorkModelConsumer(model_router)
    engine = WorkEngine(
        store=store,
        tools=gateway,
        verifiers=verifier_registry,
        model_consumer=consumer,
    )
    return WorkRuntime(
        store=store,
        inventory=profile_index,
        tools=gateway,
        engine=engine,
        budget=budget,
        verifiers=verifier_registry,
        definitions=catalog(),
        providers=None if model_router is None else model_router.registry,
    )
