from __future__ import annotations
from typing import Any, Callable
from atlas_core.actions import ActionStore
from atlas_core.capabilities import CapabilityRuntime
from atlas_core.provenance import InvocationProvenance
from .store import WorkStore

class WorkRuntime:
    """Durable responsibility. Current owner policy is resolved at every actual step execution."""

    RETRYABLE_ERROR_CODES = {
        "representation_provider_unavailable", "representation_interpretation_failed",
        "representation_derivation_failed", "model_infer_failed",
        "mcp_unavailable", "mcp_tool_error", "files_error", "files_extract_text_failed",
        "host_filesystem_error", "knowledge_generation_busy", "library_materialize_failed",
    }

    def __init__(self, store: WorkStore, capabilities: CapabilityRuntime, actions: ActionStore,
                 cancel_hook: Callable[[Any], None] | None = None) -> None:
        self.store = store
        self.capabilities = capabilities
        self.actions = actions
        self.cancel_hook = cancel_hook

    def create(self, objective: str, steps: list[dict[str, Any]], *, owner_principal_id: str,
               metadata: dict[str, Any] | None = None, artifact_class: str | None = None,
               workflow_class: str | None = None):
        return self.store.create(objective, owner_principal_id, steps, metadata=metadata,
                                 artifact_class=artifact_class, workflow_class=workflow_class)

    def detail(self, work_id: str) -> dict[str, Any]:
        return {**self.store.get(work_id).as_dict(), "steps": [s.as_dict() for s in self.store.steps(work_id)]}

    def _resolve_input(self, work_id: str, ordinal: int, value: Any) -> Any:
        if isinstance(value, list):
            return [self._resolve_input(work_id, ordinal, item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"$ref"}:
            ref = value["$ref"]
            if not isinstance(ref, dict):
                raise ValueError("work reference must be an object")
            source_ordinal = int(ref.get("step") or 0)
            if source_ordinal < 1 or source_ordinal >= ordinal:
                raise ValueError("work reference must point to an earlier step")
            source = next((item for item in self.store.steps(work_id) if item.ordinal == source_ordinal), None)
            if source is None or source.status != "completed" or source.output is None:
                raise ValueError(f"work reference step {source_ordinal} is not completed")
            return _json_pointer(source.output, str(ref.get("output") or ""))
        return {key: self._resolve_input(work_id, ordinal, item) for key, item in value.items()}

    def _retryable(self, occurrence) -> bool:
        return (
            occurrence.status == "blocked"
            or occurrence.error_code in self.RETRYABLE_ERROR_CODES
            or bool((occurrence.receipt or {}).get("retryable"))
        )

    @staticmethod
    def _retryable_exception(exc: Exception) -> bool:
        return isinstance(exc, RuntimeError) and str(exc).startswith("capability unavailable:")

    def _record_execution_failure(self, work_id: str, step, occurrence) -> dict[str, Any]:
        error = occurrence.error or occurrence.error_code or occurrence.status
        if self._retryable(occurrence):
            self.store.set_step(step.step_id, status="waiting", occurrence_id=occurrence.occurrence_id, error=error)
            self.store.set_work_status(work_id, "paused")
        else:
            self.store.set_step(step.step_id, status="failed", occurrence_id=occurrence.occurrence_id, error=error)
            self.store.set_work_status(work_id, "failed")
        return self.detail(work_id)

    def run(self, work_id: str) -> dict[str, Any]:
        work = self.store.get(work_id)
        if work.status in {"completed", "failed", "cancelled", "paused"}:
            return self.detail(work_id)
        self.store.set_work_status(work_id, "active")
        for step in self.store.steps(work_id):
            if step.status == "completed":
                continue
            if step.occurrence_id:
                occurrence = self.actions.get(step.occurrence_id)
                if occurrence.status == "succeeded":
                    self.store.set_step(step.step_id, status="completed", output=occurrence.result)
                    continue
                if occurrence.status == "uncertain":
                    self.store.set_step(step.step_id, status="waiting")
                    self.store.set_work_status(work_id, "waiting")
                    return self.detail(work_id)
                if occurrence.status in {"blocked", "failed"}:
                    return self._record_execution_failure(work_id, step, occurrence)
            if not self.store.claim_step(step.step_id):
                current = self.store.step(step.step_id)
                if current.status == "completed":
                    continue
                return self.detail(work_id)
            try:
                resolved_input = self._resolve_input(work_id, step.ordinal, step.input)
                occurrence = self.capabilities.invoke(
                    step.capability_id, resolved_input,
                    provenance=InvocationProvenance(work.owner_principal_id, "human", "work"),
                    work_id=work_id, step_id=step.step_id,
                )
            except Exception as exc:
                if self._retryable_exception(exc):
                    self.store.set_step(step.step_id, status="waiting", error=str(exc))
                    self.store.set_work_status(work_id, "paused")
                else:
                    self.store.set_step(step.step_id, status="failed", error=str(exc))
                    self.store.set_work_status(work_id, "failed")
                return self.detail(work_id)
            if occurrence.status == "succeeded":
                self.store.set_step(step.step_id, status="completed", occurrence_id=occurrence.occurrence_id, output=occurrence.result)
                continue
            if occurrence.status == "uncertain":
                self.store.set_step(step.step_id, status="waiting", occurrence_id=occurrence.occurrence_id)
                self.store.set_work_status(work_id, "waiting")
                return self.detail(work_id)
            return self._record_execution_failure(work_id, step, occurrence)
        self.store.set_work_status(work_id, "completed")
        return self.detail(work_id)

    def pause(self, work_id: str):
        return self.store.set_work_status(work_id, "paused")

    def resume(self, work_id: str):
        work = self.store.get(work_id)
        if work.status == "failed":
            self.store.reset_failed(work_id)
        elif work.status == "paused":
            self.store.reset_retryable(work_id)
        else:
            self.store.set_work_status(work_id, "active")
        return self.run(work_id)

    def cancel(self, work_id: str):
        work = self.store.get(work_id)
        if self.cancel_hook is not None:
            self.cancel_hook(work)
        return self.store.cancel(work_id)


def register_work_capabilities(registry, runtime:WorkRuntime)->None:
    from atlas_core.actions import ActionResult
    from atlas_core.capabilities import CapabilityDefinition,CapabilityRegistration,ScopeResolution
    schema={"type":"object","required":["objective","steps"],"properties":{"objective":{"type":"string"},"steps":{"type":"array","minItems":1,"items":{"type":"object","required":["capability_id","input"],"properties":{"capability_id":{"type":"string"},"description":{"type":"string"},"input":{"type":"object"}},"additionalProperties":False}},"run":{"type":"boolean"}},"additionalProperties":False}
    def resolve(payload):return ScopeResolution("atlas/work",dict(payload),f"Create Work: {payload.get('objective','')}")
    def execute(payload):
        owner=payload.pop("__owner_principal_id",None)
        if not owner:return ActionResult(False,error_code="owner_context_missing",error="owner principal unavailable")
        work=runtime.create(payload["objective"],payload["steps"],owner_principal_id=owner);result=runtime.run(work.work_id) if payload.get("run",True) else runtime.detail(work.work_id);return ActionResult(True,result,{"ok":True,"operation":"work.create","work_id":work.work_id})
    # owner context is trusted execution context injected after the occurrence payload has been attested.
    registry.register(CapabilityRegistration(CapabilityDefinition("work.create","Create durable Work from an objective and explicit capability steps.","create","internal",schema,source="work",tags=("work",)),resolve,execute,metadata={"scope_hint":"atlas/work","requires_owner_context":True}),replace=True)


def _json_pointer(value: Any, pointer: str) -> Any:
    if pointer in {"", "/"}:
        return value
    if not pointer.startswith("/"):
        raise ValueError("work reference output must be a JSON pointer")
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"work reference path not found: {pointer}")
            current = current[token]
        elif isinstance(current, list):
            try: current = current[int(token)]
            except (ValueError, IndexError) as exc: raise ValueError(f"work reference path not found: {pointer}") from exc
        else:
            raise ValueError(f"work reference path not found: {pointer}")
    return current
