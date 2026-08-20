from __future__ import annotations

from pathlib import Path

from atlas_core.advanced.brief import TaskBrief
from atlas_core.authority import authority_allows, validate_authority
from atlas_core.capabilities import CapabilityRegistry
from atlas_core.capabilities.definition import CapabilityDefinition, catalog, lookup
from atlas_core.runtime import RuntimeBudget, RuntimeResult, TaskRuntime
from atlas_core.tasks import TaskStore
from atlas_core.tools import ToolGateway
from atlas_core.verification import VerifierRegistry

from .frame import RuntimeFrame, assemble_frame
from .profile import ExecutionProfileIndex
from .work import WorkId, WorkRecord


DEFAULT_WORK_DB = Path("instance/atlas-work.db")
UNAVAILABLE = "implementation unavailable"


class WorkError(RuntimeError):
    pass


class WorkRuntime:
    """Work composition boundary. TaskRuntime is the execution engine."""

    def __init__(
        self,
        *,
        engine: TaskRuntime,
        definitions: tuple[CapabilityDefinition, ...],
        profiles: ExecutionProfileIndex,
        tool_gateway: ToolGateway,
    ) -> None:
        self._engine = engine
        self._definitions = {item.id: item for item in definitions}
        self._profiles = profiles
        self._tool_gateway = tool_gateway

    def accept(self, brief: TaskBrief, authority_scope: str) -> WorkId:
        granted = validate_authority(authority_scope)
        if not authority_allows(granted, brief.required_authority):
            raise WorkError(
                "authority_scope "
                f"{granted!r} does not satisfy required_authority {brief.required_authority!r}"
            )
        selected = tuple(self._require_definition(item) for item in brief.capabilities)
        for definition in selected:
            if not authority_allows(granted, definition.required_authority):
                raise WorkError(
                    f"authority_scope {granted!r} does not satisfy {definition.id} "
                    f"required_authority {definition.required_authority!r}"
                )

        store = self._engine.store
        task = store.create_task(
            objective=brief.objective,
            success_criteria=(brief.expected_effect,),
            constraints=brief.constraints,
            authority_scope=granted,
            metadata={
                "source": "task_brief",
                "brief": brief.as_dict(),
            },
        )
        capability_ids = tuple(item.id for item in selected)
        frame = assemble_frame(
            work_id=task.id,
            capabilities=capability_ids,
            authority_scope=granted,
            definitions=self._definitions,
            profiles=self._profiles,
        )
        brief_artifact = store.put_artifact(
            task.id,
            kind="task_brief",
            payload=brief.as_dict(),
            metadata={"purpose": "accepted_brief"},
        )
        frame_artifact = store.put_artifact(
            task.id,
            kind="runtime_frame",
            payload=frame.as_dict(),
            metadata={"purpose": "execution_frame"},
        )
        for definition in selected:
            store.add_step(
                task.id,
                description=definition.description,
                capability=definition.id,
                capability_version="1.0.0",
                input_artifact_ids=(brief_artifact.id, frame_artifact.id),
                metadata={"accept_all_criteria": True},
            )
        store.create_checkpoint(task.id, reason="work accepted from task brief")
        return task.id

    def run(self, work_id: WorkId) -> RuntimeResult:
        frame = self.frame(work_id)
        blocked = self._execution_block(frame)
        if blocked is not None:
            record = self.get(work_id)
            return RuntimeResult(work_id, record.status, 0, 0, blocked)
        return self._engine.run_until_blocked(work_id)

    def get(self, work_id: WorkId) -> WorkRecord:
        task = self._engine.store.get_task(work_id)
        capabilities = tuple(
            step.capability
            for step in self._engine.store.list_steps(work_id)
            if step.capability
        )
        return WorkRecord(
            id=task.id,
            objective=task.objective,
            status=task.status,
            authority_scope=task.authority_scope,
            capabilities=capabilities,
        )

    def frame(self, work_id: WorkId) -> RuntimeFrame:
        self._engine.store.get_task(work_id)
        for artifact in reversed(self._engine.store.list_artifacts(work_id)):
            if artifact.kind == "runtime_frame" and isinstance(artifact.payload, dict):
                return RuntimeFrame.from_dict(artifact.payload)
        raise WorkError(f"Work {work_id} has no runtime frame")

    def _require_definition(self, capability_id: str) -> CapabilityDefinition:
        definition = self._definitions.get(capability_id)
        if definition is None:
            raise WorkError(f"Unknown capability: {capability_id}")
        return definition

    def _execution_block(self, frame: RuntimeFrame) -> str | None:
        if not frame.capabilities:
            return UNAVAILABLE
        for capability_id in frame.capabilities:
            definition = self._definitions.get(capability_id)
            if definition is None:
                return f"Unknown capability: {capability_id}"
            if not authority_allows(frame.authority_scope, definition.required_authority):
                return (
                    f"authority_scope {frame.authority_scope!r} does not satisfy "
                    f"{definition.required_authority!r}"
                )
            profile = self._profiles.get(capability_id)
            if profile is None or not profile.available:
                return UNAVAILABLE
            if self._profiles.handler(capability_id) is None and profile.executor_kind != "model":
                return UNAVAILABLE
        return None


def build_work_runtime(
    *,
    db_path: str | Path = DEFAULT_WORK_DB,
    tool_gateway: ToolGateway | None = None,
    profiles: ExecutionProfileIndex | None = None,
    budget: RuntimeBudget | None = None,
) -> WorkRuntime:
    """Only composition root for WorkRuntime."""

    store = TaskStore(db_path)
    store.initialize()
    profile_index = profiles if profiles is not None else ExecutionProfileIndex()
    gateway = tool_gateway if tool_gateway is not None else ToolGateway()
    capabilities = CapabilityRegistry()
    _register_executable_profiles(capabilities, profile_index)
    engine = TaskRuntime(
        store=store,
        capabilities=capabilities,
        verifiers=VerifierRegistry(),
        tool_gateway=gateway,
        budget=budget,
    )
    return WorkRuntime(
        engine=engine,
        definitions=catalog(),
        profiles=profile_index,
        tool_gateway=gateway,
    )


def _register_executable_profiles(
    registry: CapabilityRegistry,
    profiles: ExecutionProfileIndex,
) -> None:
    for profile in profiles.all():
        if not profile.available:
            continue
        definition = lookup(profile.capability_id)
        if definition is None:
            continue
        handler = profiles.handler(profile.capability_id)
        if handler is None and profile.executor_kind in {"deterministic", "tool", "composite"}:
            continue
        registry.register(definition, profile, handler)
