from __future__ import annotations
from typing import Any, Callable
from atlas_core.actions import ActionStore
from atlas_core.capabilities import CapabilityRuntime
from atlas_core.provenance import InvocationProvenance
from .store import WorkStore
from .validation import validate_workflow_steps

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

    def validate_steps(self, steps: list[dict[str, Any]]) -> None:
        validate_workflow_steps(self.capabilities.registry, steps)

    def create(self, objective: str, steps: list[dict[str, Any]], *, owner_principal_id: str,
               metadata: dict[str, Any] | None = None, artifact_class: str | None = None,
               workflow_class: str | None = None):
        self.validate_steps(steps)
        return self.store.create(objective, owner_principal_id, steps, metadata=metadata,
                                 artifact_class=artifact_class, workflow_class=workflow_class)

    def detail(self, work_id: str) -> dict[str, Any]:
        return {**self.store.get(work_id).as_dict(), "steps": [s.as_dict() for s in self.store.steps(work_id)], "adaptations": list(self.store.adaptations(work_id))}

    def revise(self, work_id: str, *, base_revision: int, from_ordinal: int, replacement_steps: list[dict[str, Any]],
               change_intent: str, reason: str, unchanged_goal: str, expected_impact: str) -> dict[str, Any]:
        current = self.store.get(work_id)
        prefix = [step for step in self.store.steps(work_id) if step.ordinal < from_ordinal]
        if any(step.status != "completed" for step in prefix):
            raise ValueError("revision prefix must already be completed")
        combined = [{"capability_id": step.capability_id, "description": step.description, "input": step.input} for step in prefix] + replacement_steps
        self.validate_steps(combined)
        self.store.revise(work_id, base_revision=base_revision, from_ordinal=from_ordinal, replacement_steps=replacement_steps,
                          change_intent=change_intent, reason=reason, unchanged_goal=unchanged_goal, expected_impact=expected_impact)
        return self.detail(current.work_id)

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

    def recover_incomplete(self) -> dict[str, int]:
        """Reconcile durable running steps after a runtime restart.

        A claimed step without an occurrence never reached the canonical action
        gate and may be queued safely. An uncertain occurrence is never replayed.
        """
        recovered={"queued":0,"completed":0,"waiting":0,"failed":0}
        for work in self.store.list(limit=10000):
            changed=False
            for step in self.store.steps(work.work_id):
                if step.status != "running": continue
                if not step.occurrence_id:
                    self.store.set_step(step.step_id,status="queued",error="recovered after runtime restart before action submission")
                    recovered["queued"]+=1;changed=True;continue
                occurrence=self.actions.get(step.occurrence_id)
                if occurrence.status=="succeeded":
                    self.store.set_step(step.step_id,status="completed",occurrence_id=occurrence.occurrence_id,output=occurrence.result)
                    recovered["completed"]+=1;changed=True
                elif occurrence.status in {"uncertain","executing"}:
                    self.store.set_step(step.step_id,status="waiting",occurrence_id=occurrence.occurrence_id,error="action outcome is uncertain after runtime restart; reconcile before retry")
                    self.store.set_work_status(work.work_id,"waiting");recovered["waiting"]+=1;changed=True
                elif occurrence.status in {"blocked","failed"}:
                    state="waiting" if self._retryable(occurrence) else "failed"
                    self.store.set_step(step.step_id,status=state,occurrence_id=occurrence.occurrence_id,error=occurrence.error or occurrence.error_code)
                    self.store.set_work_status(work.work_id,"paused" if state=="waiting" else "failed");recovered[state]+=1;changed=True
            if changed:
                current=self.store.steps(work.work_id)
                if current and all(step.status=="completed" for step in current): self.store.set_work_status(work.work_id,"completed")
                elif any(step.status=="queued" for step in current) and not any(step.status in {"waiting","failed","running"} for step in current): self.store.set_work_status(work.work_id,"queued")
        return recovered

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
            if step.status in {"completed", "cancelled"}:
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
    step_schema={"type":"object","required":["capability_id","input"],"properties":{"capability_id":{"type":"string","minLength":1},"description":{"type":"string","minLength":1},"input":{"type":"object"}},"additionalProperties":False}
    schema={"type":"object","required":["objective","steps"],"properties":{"objective":{"type":"string","minLength":1},"steps":{"type":"array","minItems":1,"items":step_schema},"run":{"type":"boolean"}},"additionalProperties":False}
    exact={"type":"object","required":["work_id"],"properties":{"work_id":{"type":"string","minLength":1}},"additionalProperties":False}
    revise_schema={"type":"object","required":["work_id","base_revision","from_ordinal","change_intent","reason","unchanged_goal","expected_impact","replacement_steps"],"properties":{
        "work_id":{"type":"string","minLength":1},"base_revision":{"type":"integer","minimum":1},"from_ordinal":{"type":"integer","minimum":1},
        "change_intent":{"type":"string","minLength":1},"reason":{"type":"string","minLength":1},"unchanged_goal":{"type":"string","minLength":1},
        "expected_impact":{"type":"string","minLength":1},"replacement_steps":{"type":"array","minItems":1,"items":step_schema}},"additionalProperties":False}
    get_schema=exact
    list_schema={"type":"object","properties":{"query":{"type":"string"},"cadence_id":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":200}},"additionalProperties":False}

    def owner(payload):
        value=payload.pop("__owner_principal_id",None);payload.pop("__invocation_surface",None);return value
    def create_execute(payload):
        principal=owner(payload)
        if not principal:return ActionResult(False,error_code="owner_context_missing",error="owner principal unavailable")
        try:
            work=runtime.create(payload["objective"],payload["steps"],owner_principal_id=principal)
            result=runtime.run(work.work_id) if payload.get("run",True) else runtime.detail(work.work_id)
            return ActionResult(True,result,{"ok":True,"operation":"work.create","work_id":work.work_id})
        except Exception as exc:return ActionResult(False,error_code="work_create_failed",error=str(exc),receipt={"ok":False,"operation":"work.create"})
    def control_execute(operation):
        def execute(payload):
            owner(payload);wid=payload["work_id"]
            try:
                result={"run":runtime.run,"resume":runtime.resume,"pause":lambda x:(runtime.pause(x),runtime.detail(x))[1],"cancel":lambda x:(runtime.cancel(x),runtime.detail(x))[1]}[operation](wid)
                return ActionResult(True,result,{"ok":True,"operation":f"work.{operation}","work_id":wid})
            except Exception as exc:return ActionResult(False,error_code=f"work_{operation}_failed",error=str(exc),receipt={"ok":False,"operation":f"work.{operation}"})
        return execute
    def revise_execute(payload):
        owner(payload);wid=payload.pop("work_id")
        try:
            result=runtime.revise(wid,base_revision=payload["base_revision"],from_ordinal=payload["from_ordinal"],replacement_steps=payload["replacement_steps"],change_intent=payload["change_intent"],reason=payload["reason"],unchanged_goal=payload["unchanged_goal"],expected_impact=payload["expected_impact"])
            return ActionResult(True,result,{"ok":True,"operation":"work.revise","work_id":wid,"revision":result["revision"]})
        except Exception as exc:return ActionResult(False,error_code="work_revise_failed",error=str(exc),receipt={"ok":False,"operation":"work.revise"})
    def get_execute(payload):
        owner(payload)
        try:return ActionResult(True,runtime.detail(payload["work_id"]),{"ok":True,"operation":"work.get","work_id":payload["work_id"]})
        except KeyError:return ActionResult(False,error_code="work_unknown",error="work not found",receipt={"ok":False,"operation":"work.get"})
    def list_execute(payload):
        owner(payload);rows=[item.as_dict() for item in runtime.store.list(limit=int(payload.get("limit") or 50),cadence_id=payload.get("cadence_id"))];query=str(payload.get("query") or "").strip().lower()
        if query:
            terms=[term for term in query.split() if term];rows=[row for row in rows if any(term in f"{row.get('objective','')} {row.get('display_ref') or ''} {row.get('status','')}".lower() for term in terms)]
        return ActionResult(True,rows,{"ok":True,"operation":"work.list","count":len(rows)})

    orchestration_meta={"scope_hint":"atlas/work","requires_owner_context":True,"work_composable":False}
    read_meta={"scope_hint":"atlas/work","requires_owner_context":True,"work_composable":False}
    registry.register(CapabilityRegistration(CapabilityDefinition("work.create","Create durable Work from an objective and validated capability steps.","create","internal",schema,source="work",tags=("work",)),lambda p:ScopeResolution("atlas/work",dict(p),f"Create Work: {p.get('objective','')}"),create_execute,metadata=orchestration_meta),replace=True)
    for cid,op in (("work.run","run"),("work.resume","resume"),("work.pause","pause"),("work.cancel","cancel")):
        registry.register(CapabilityRegistration(CapabilityDefinition(cid,f"{op.title()} durable Work through the canonical runtime.",op,"internal",exact,source="work",tags=("work","control")),lambda p,_op=op:ScopeResolution("atlas/work",dict(p),f"{_op.title()} Work: {p.get('work_id','')}"),control_execute(op),metadata=orchestration_meta),replace=True)
    registry.register(CapabilityRegistration(CapabilityDefinition("work.revise","Replace only the unfinished route of durable Work while preserving completed execution history and the prior plan.","revise","internal",revise_schema,source="work",tags=("work","adaptation")),lambda p:ScopeResolution("atlas/work",dict(p),f"Revise Work: {p.get('work_id','')}"),revise_execute,metadata=orchestration_meta),replace=True)
    registry.register(CapabilityRegistration(CapabilityDefinition("work.get","Read one Work item with its ordered steps, adaptations, statuses, and each step's recorded output.","get","none",get_schema,source="work",tags=("work","evidence")),lambda p:ScopeResolution("atlas/work",dict(p),f"Read Work: {p.get('work_id','')}"),get_execute,metadata=read_meta),replace=True)
    registry.register(CapabilityRegistration(CapabilityDefinition("work.list","List durable Work, optionally narrowed by objective text or originating standing duty.","list","none",list_schema,source="work",tags=("work",)),lambda p:ScopeResolution("atlas/work",dict(p),"List Work"),list_execute,metadata=read_meta),replace=True)


def _json_pointer(value: Any, pointer: str) -> Any:
    if pointer in {"", "/"}: return value
    if not pointer.startswith("/"): raise ValueError("work reference output must be a JSON pointer")
    current=value
    for raw in pointer[1:].split("/"):
        token=raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current,dict):
            if token not in current: raise ValueError(f"work reference path not found: {pointer}")
            current=current[token]
        elif isinstance(current,list):
            try: current=current[int(token)]
            except (ValueError,IndexError) as exc: raise ValueError(f"work reference path not found: {pointer}") from exc
        else: raise ValueError(f"work reference path not found: {pointer}")
    return current
