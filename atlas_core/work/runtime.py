from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from atlas_core.advanced.brief import TaskBrief
from atlas_core.capabilities.definition import CapabilityDefinition, catalog
from atlas_core.runtime_types import RecoveryResult, RuntimeBudget, RuntimeResult
from atlas_core.tasks import TaskStoreError, UnknownRecordError
from atlas_core.tasks.store_common import _new_id
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
from .store import WorkStore
from .work import UNAVAILABLE, WorkError, WorkId, WorkRecord


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
    ) -> None:
        self.store = store
        self._profiles = inventory
        self._tool_gateway = tools
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
        work_id = _new_id("task")
        contract = compile_contract(
            work_id=work_id,
            brief=brief,
            authority_scope=authority_scope,
            inventory=self._profiles,
            tools=self._tool_gateway,
            work_budget=self._budget,
        )
        store = self.store
        store.create_task(
            objective=brief.objective,
            success_criteria=contract.success_criteria,
            constraints=brief.constraints,
            authority_scope=contract.authority_scope,
            metadata={
                "source": "task_brief",
                "brief": brief.as_dict(),
                "contract_id": contract.contract_id,
            },
            task_id=work_id,
        )
        try:
            store.insert_work_contract(
                work_id=contract.work_id,
                contract_id=contract.contract_id,
                sha256=contract.sha256,
                payload=contract.as_payload(),
                compiled_at=contract.compiled_at,
            )
        except TaskStoreError as exc:
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
        contract = self.contract(work_id)
        report = ImplementationResolver().resolve(
            contract, self._profiles, self._tool_gateway
        )
        if report.unarmed:
            record = self.get(work_id)
            return RuntimeResult(work_id, record.status, 0, 0, UNAVAILABLE)
        return self._engine.run(contract, report)

    def resume(self, work_id: WorkId) -> int:
        contract = self.contract(work_id)
        report = ImplementationResolver().resolve(
            contract, self._profiles, self._tool_gateway
        )
        return self._engine.resume(contract, report)

    def recover(self, work_id: WorkId) -> RecoveryResult:
        contract = self.contract(work_id)
        report = ImplementationResolver().resolve(
            contract, self._profiles, self._tool_gateway
        )
        return self._engine.recover(contract, report)

    def get(self, work_id: WorkId) -> WorkRecord:
        task = self.store.get_task(work_id)
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
        except TaskStoreError as exc:
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
) -> WorkRuntime:
    """Only composition root for WorkRuntime."""

    store = WorkStore(db_path)
    store.initialize()
    store.initialize_work_schema()
    profile_index = profiles if profiles is not None else DeploymentInventory()
    gateway = tool_gateway if tool_gateway is not None else ToolGateway()
    verifier_registry = verifiers if verifiers is not None else VerifierRegistry()
    engine = WorkEngine(
        store=store,
        tools=gateway,
        verifiers=verifier_registry,
    )
    return WorkRuntime(
        store=store,
        inventory=profile_index,
        tools=gateway,
        engine=engine,
        budget=budget,
        verifiers=verifier_registry,
        definitions=catalog(),
    )
