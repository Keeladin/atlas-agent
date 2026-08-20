from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from atlas_core.authority import authority_allows
from atlas_core.capabilities import CapabilityRegistration
from atlas_core.context import ContextBuilder
from atlas_core.providers import ModelRequest, ModelRouter, ModelRoutingError
from atlas_core.tasks import TaskRecord, TaskStore

_FENCED_JSON_RE = re.compile(r"```(?:json|JSON)?\s*\r?\n?(.*?)```", re.DOTALL)
_PLANNER_JSON_ERRORS = {
    "Planner did not return valid JSON.",
    "Planner JSON must contain a steps array.",
    "Each plan step must be an object.",
}


@dataclass(frozen=True)
class PlannedStep:
    key: str
    description: str
    capability: str
    dependencies: tuple[str, ...]
    satisfies_criteria: tuple[int, ...] = ()
    capability_version: str | None = None
    input: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskPlan:
    steps: tuple[PlannedStep, ...]
    notes: tuple[str, ...] = ()


class PlanError(RuntimeError):
    pass


def _is_planner_json_error(exc: BaseException) -> bool:
    return isinstance(exc, PlanError) and str(exc) in _PLANNER_JSON_ERRORS


def _extract_json_value(text: str) -> Any:
    raw = (text or "").strip().lstrip("\ufeff")
    if not raw:
        raise PlanError("Planner did not return valid JSON.")
    candidates = [raw]
    for block in _FENCED_JSON_RE.findall(raw):
        inner = block.strip()
        if inner:
            candidates.append(inner)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    start = raw.find("{")
    if start >= 0:
        try:
            value, _end = json.JSONDecoder().raw_decode(raw, start)
            return value
        except json.JSONDecodeError:
            pass
    raise PlanError("Planner did not return valid JSON.")


class TaskPlanner:
    """Create a durable task and a source-grounded dependency plan.

    Planning is itself durable Atlas work and uses the same ContextBuilder as
    every other model invocation. No planner-specific prompt path may bypass the
    immutable ContextManifest boundary.
    """

    def __init__(
        self,
        *,
        store: TaskStore,
        model_router: ModelRouter,
        planning_capability: CapabilityRegistration,
        capability_manifest: list[dict[str, Any]],
    ) -> None:
        self.store = store
        self.model_router = model_router
        self.planning_capability = planning_capability
        self.capability_manifest = capability_manifest
        self.context_builder = ContextBuilder(store)
        self._capability_versions: dict[str, str] = {
            str(item.get("id") or "").strip(): str(item.get("version") or "1.0.0").strip()
            for item in capability_manifest
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        self._capability_index: dict[str, dict[str, Any]] = {
            str(item.get("id") or "").strip(): item
            for item in capability_manifest
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }

    def plan_and_create(
        self,
        *,
        objective: str,
        success_criteria: tuple[str, ...],
        constraints: tuple[str, ...] = (),
        authority_scope: str = "read",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[TaskRecord, TaskPlan]:
        task = self.store.create_task(
            objective=objective,
            success_criteria=success_criteria,
            constraints=constraints,
            authority_scope=authority_scope,
            metadata={
                **(metadata or {}),
                "planner": self.planning_capability.id,
                "planner_version": self.planning_capability.profile.version,
            },
        )
        planning_input = self.store.put_artifact(
            task.id,
            kind="planning_request",
            payload={
                "objective": objective,
                "success_criteria": list(success_criteria),
                "constraints": list(constraints),
                "authority_scope": authority_scope,
                "capabilities": self.capability_manifest,
                "planner_output_contract": {
                    "format": "JSON object",
                    "required_keys": ["steps", "notes"],
                    "step_keys": [
                        "key",
                        "description",
                        "capability",
                        "capability_version",
                        "dependencies",
                        "satisfies_criteria",
                        "input",
                    ],
                    "rules": [
                        "dependencies contain step keys, never prose",
                        "do not execute work",
                        "use only supplied capability ids/versions",
                        "satisfies_criteria contains only 1-based integer criterion ordinals, never criterion ids",
                        "when a capability input_schema lists required properties, include input with those properties",
                        "if the objective requests a user-facing artifact such as a story, letter, document, or exact phrase, include a step that produces that artifact; do not substitute analysis of the request",
                        "knowledge.search and knowledge.answer only query Atlas's ingested local corpus; they are not web search or general trivia",
                        "prefer reasoning.general for general identification or factual questions unless the user asked to search Atlas knowledge",
                        "knowledge.answer requires a dependency that produces knowledge_search_results",
                    ],
                },
            },
            metadata={"purpose": "bounded durable planning input"},
        )
        planning_step = self.store.add_step(
            task.id,
            description="Create a bounded capability plan for this task.",
            capability=self.planning_capability.id,
            capability_version=self.planning_capability.profile.version,
            input_artifact_ids=(planning_input.id,),
            metadata={"internal_planning": True},
        )
        if not authority_allows(
            authority_scope,
            self.planning_capability.definition.required_authority,
        ):
            self.store.set_task_status(task.id, "failed")
            self.store.create_checkpoint(
                task.id,
                reason="planning authority insufficient",
            )
            raise PlanError(
                "Planning requires authority "
                f"{self.planning_capability.definition.required_authority!r}; "
                f"task grants {authority_scope!r}."
            )
        if task.status == "planned":
            self.store.set_task_status(task.id, "active")
        execution = self.store.begin_execution(
            task.id,
            step_id=planning_step.id,
            capability=self.planning_capability.id,
            capability_version=self.planning_capability.profile.version,
            input_artifact_ids=(planning_input.id,),
        )
        try:
            _pack, manifest_record, route, response = self._planning_generate(
                task=task,
                planning_step=planning_step,
                execution=execution,
                artifact_ids=(planning_input.id,),
                required_artifact_id=planning_input.id,
            )
            repaired = False
            try:
                parsed = self.parse_plan(response.text)
            except PlanError as exc:
                if not _is_planner_json_error(exc):
                    raise
                rejected = self.store.put_artifact(
                    task.id,
                    step_id=planning_step.id,
                    kind="planner_json_rejected",
                    payload={"raw": response.text, "error": str(exc)},
                    metadata={"repair": "pending"},
                )
                self.store.finish_execution(
                    execution.id,
                    status="rework",
                    output_artifact_ids=(rejected.id,),
                    error=str(exc),
                    metrics=dict(response.metrics),
                )
                execution = self.store.begin_execution(
                    task.id,
                    step_id=planning_step.id,
                    capability=self.planning_capability.id,
                    capability_version=self.planning_capability.profile.version,
                    input_artifact_ids=(planning_input.id, rejected.id),
                )
                _pack, manifest_record, route, response = self._planning_generate(
                    task=task,
                    planning_step=planning_step,
                    execution=execution,
                    artifact_ids=(planning_input.id, rejected.id),
                    required_artifact_id=planning_input.id,
                    failure_reason=str(exc),
                    previous_manifest_id=manifest_record.id,
                )
                parsed = self.parse_plan(response.text)
                repaired = True
            plan = self._pin_and_validate_plan(
                parsed,
                criterion_count=len(success_criteria),
            )
            artifact = self.store.put_artifact(
                task.id,
                step_id=planning_step.id,
                kind="task_plan",
                payload={
                    "raw": response.text,
                    "steps": [
                        {
                            "key": item.key,
                            "description": item.description,
                            "capability": item.capability,
                            "capability_version": item.capability_version,
                            "dependencies": list(item.dependencies),
                            "satisfies_criteria": list(item.satisfies_criteria),
                        }
                        for item in plan.steps
                    ],
                    "notes": list(plan.notes),
                },
                metadata={
                    "provider": response.provider_key,
                    "model": response.model,
                    "route": route.reason,
                    "context_manifest_id": manifest_record.id,
                    "repaired": repaired,
                },
            )
            verification = self.store.put_artifact(
                task.id,
                step_id=planning_step.id,
                kind="verification_result",
                payload={
                    "status": "pass",
                    "summary": "plan contract, capability/version references, criterion coverage and dependency graph validated",
                    "details": {
                        "step_count": len(plan.steps),
                        "criterion_count": len(success_criteria),
                    },
                },
                metadata={
                    "verifier_id": "planning.structural",
                    "execution_id": execution.id,
                },
            )
            metrics = dict(response.metrics)
            actual_cost = route.provider.spec.estimate_cost_usd(
                input_tokens=int(metrics.get("input_tokens") or 0),
                output_tokens=int(metrics.get("output_tokens") or 0),
            )
            if actual_cost is not None:
                metrics["estimated_cost_usd"] = actual_cost
            self.store.finish_execution(
                execution.id,
                status="pass",
                output_artifact_ids=(artifact.id,),
                verifier_artifact_id=verification.id,
                receipt={
                    "ok": True,
                    "provider": response.provider_key,
                    "model": response.model,
                    "context_manifest_id": manifest_record.id,
                },
                metrics=metrics,
            )
            self.store.create_checkpoint(task.id, reason="planning accepted")
        except Exception as exc:
            current = self.store.get_execution(execution.id)
            if current.status == "running":
                self.store.finish_execution(
                    execution.id,
                    status="fail",
                    error=str(exc),
                )
            self.store.set_task_status(task.id, "failed")
            self.store.create_checkpoint(task.id, reason="planning failed")
            if isinstance(exc, PlanError):
                raise
            raise PlanError(f"Planning failed: {exc}") from exc

        ids: dict[str, str] = {}
        pending = list(plan.steps)
        while pending:
            progressed = False
            for item in list(pending):
                if all(dep in ids for dep in item.dependencies):
                    input_ids: tuple[str, ...] = ()
                    if item.input:
                        request = self.store.put_artifact(
                            task.id,
                            kind=f"{item.capability.replace('.', '_')}_request",
                            payload=item.input,
                            metadata={"purpose": "planned invocation input", "plan_key": item.key},
                        )
                        input_ids = (request.id,)
                    record = self.store.add_step(
                        task.id,
                        description=item.description,
                        capability=item.capability,
                        capability_version=item.capability_version,
                        dependencies=[ids[dep] for dep in item.dependencies],
                        input_artifact_ids=input_ids,
                        metadata={
                            "satisfies_criteria": list(item.satisfies_criteria),
                            "plan_key": item.key,
                        },
                    )
                    ids[item.key] = record.id
                    pending.remove(item)
                    progressed = True
            if not progressed:
                unresolved = {item.key: item.dependencies for item in pending}
                self.store.set_task_status(task.id, "failed")
                raise PlanError(
                    f"Plan contains missing or cyclic dependencies: {unresolved}"
                )
        return self.store.get_task(task.id), plan

    def _pin_and_validate_plan(self, plan: TaskPlan, *, criterion_count: int) -> TaskPlan:
        if not plan.steps:
            raise PlanError("Planner returned no executable steps.")
        keys = {step.key for step in plan.steps}
        pinned: list[PlannedStep] = []
        for step in plan.steps:
            expected_version = self._capability_versions.get(step.capability)
            if expected_version is None:
                raise PlanError(
                    f"Planner selected unregistered capability: {step.capability}"
                )
            selected_version = step.capability_version or expected_version
            if selected_version != expected_version:
                raise PlanError(
                    "Planner selected an unregistered capability version: "
                    f"{step.capability}@{selected_version}"
                )
            missing = [dep for dep in step.dependencies if dep not in keys]
            if missing:
                raise PlanError(
                    f"Plan step {step.key!r} has unknown dependencies: {missing}"
                )
            invalid_criteria = [
                ordinal
                for ordinal in step.satisfies_criteria
                if ordinal < 1 or ordinal > criterion_count
            ]
            if invalid_criteria:
                raise PlanError(
                    f"Plan step {step.key!r} references invalid criteria: {invalid_criteria}"
                )
            pinned.append(
                PlannedStep(
                    step.key,
                    step.description,
                    step.capability,
                    step.dependencies,
                    step.satisfies_criteria,
                    selected_version,
                    step.input,
                )
            )

        covered_criteria = {
            ordinal
            for step in pinned
            for ordinal in step.satisfies_criteria
        }
        missing_criteria = sorted(
            set(range(1, criterion_count + 1)) - covered_criteria
        )
        if missing_criteria:
            raise PlanError(
                "Plan does not cover all success criteria: "
                f"{missing_criteria}"
            )

        resolved: set[str] = set()
        remaining = {step.key: set(step.dependencies) for step in pinned}
        while remaining:
            ready = [key for key, deps in remaining.items() if deps <= resolved]
            if not ready:
                raise PlanError("Plan contains cyclic dependencies.")
            for key in ready:
                resolved.add(key)
                remaining.pop(key)
        self._assert_plan_feasible(tuple(pinned))
        return TaskPlan(tuple(pinned), plan.notes)

    def _assert_plan_feasible(self, steps: tuple[PlannedStep, ...]) -> None:
        by_key = {step.key: step for step in steps}
        for step in steps:
            contract = self._capability_index.get(step.capability) or {}
            required = tuple(
                str(kind)
                for kind in (contract.get("requires_artifact_kinds") or ())
                if str(kind).strip()
            )
            if required:
                produced: set[str] = set()
                for dep in step.dependencies:
                    dep_contract = self._capability_index.get(by_key[dep].capability) or {}
                    kind = str(dep_contract.get("output_kind") or "").strip()
                    if kind:
                        produced.add(kind)
                missing = [kind for kind in required if kind not in produced]
                if missing:
                    raise PlanError(
                        f"Plan step {step.key!r} requires artifact kinds {missing} "
                        "from a dependency, but none of its dependencies produce them."
                    )
            if str(contract.get("executor_kind") or "") != "model":
                continue
            try:
                self.model_router.select(
                    step.capability,
                    context_chars=256,
                    privacy=str(contract.get("privacy") or "cloud_allowed"),
                )
            except ModelRoutingError as exc:
                raise PlanError(
                    f"Plan step {step.key!r} is not executable with current providers: {exc}"
                ) from exc

    def _planning_generate(
        self,
        *,
        task: TaskRecord,
        planning_step,
        execution,
        artifact_ids: tuple[str, ...],
        required_artifact_id: str,
        failure_reason: str | None = None,
        previous_manifest_id: str | None = None,
    ) -> tuple[Any, Any, Any, Any]:
        pack = self.context_builder.build(
            task.id,
            planning_step.id,
            artifact_ids=artifact_ids,
            required_artifact_ids=(required_artifact_id,),
            execution_id=execution.id,
            registration=self.planning_capability,
            max_chars=self.planning_capability.profile.budget.max_context_chars,
            previous_manifest_id=previous_manifest_id,
            failure_reason=failure_reason,
        )
        manifest_record = self.store.write_context_manifest(
            task.id,
            step_id=planning_step.id,
            execution_id=execution.id,
            capability=self.planning_capability.id,
            capability_version=self.planning_capability.profile.version,
            assembler_version=pack.manifest.assembler_version,
            budget_tokens=pack.manifest.budget_tokens,
            total_tokens=pack.manifest.total_tokens,
            manifest=pack.manifest.as_dict(),
            manifest_id=pack.manifest.manifest_id,
        )
        self.store.append_event(
            task.id,
            step_id=planning_step.id,
            execution_id=execution.id,
            name="context.manifest.written",
            payload={
                "manifest_id": manifest_record.id,
                "sha256": manifest_record.sha256,
                "capability": self.planning_capability.id,
                "capability_version": self.planning_capability.profile.version,
                "tokens": pack.manifest.total_tokens,
                "budget": pack.manifest.budget_tokens,
            },
        )
        route = self.model_router.select(
            self.planning_capability.id,
            context_chars=pack.chars,
            privacy=self.planning_capability.profile.privacy,
            eligible_providers=self.planning_capability.profile.eligible_providers,
        )
        projected_cost = route.provider.spec.estimate_cost_usd(
            input_tokens=pack.tokens,
            output_tokens=max(
                1,
                ((self.planning_capability.profile.budget.max_output_chars or 8_000) + 3) // 4,
            ),
        )
        if (
            self.planning_capability.profile.budget.max_cost_usd is not None
            and projected_cost is not None
            and projected_cost > self.planning_capability.profile.budget.max_cost_usd
        ):
            raise PlanError(
                "Projected planning cost exceeds capability budget: "
                f"{projected_cost:.6f}>"
                f"{self.planning_capability.profile.budget.max_cost_usd:.6f}"
            )
        self.store.set_execution_provider(execution.id, route.provider.spec.key)
        response = route.provider.generate(
            ModelRequest(
                self.planning_capability.id,
                json.dumps(pack.payload["system"], ensure_ascii=False, sort_keys=True),
                pack.as_text(),
                max_output_chars=self.planning_capability.profile.budget.max_output_chars,
                metadata={
                    "task_id": task.id,
                    "step_id": planning_step.id,
                    "context_manifest_id": manifest_record.id,
                    "capability_version": self.planning_capability.profile.version,
                    "response_format": {"type": "json_object"},
                    "plan_repair": bool(failure_reason),
                },
            )
        )
        if (
            self.planning_capability.profile.budget.max_output_chars is not None
            and len(response.text) > self.planning_capability.profile.budget.max_output_chars
        ):
            raise PlanError(
                "Planner output exceeds explicit capability output budget."
            )
        return pack, manifest_record, route, response

    @staticmethod
    def parse_plan(text: str) -> TaskPlan:
        data = _extract_json_value(text)
        if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
            raise PlanError("Planner JSON must contain a steps array.")
        steps: list[PlannedStep] = []
        seen: set[str] = set()
        for item in data["steps"]:
            if not isinstance(item, dict):
                raise PlanError("Each plan step must be an object.")
            key = str(item.get("key") or "").strip()
            description = str(item.get("description") or "").strip()
            capability = str(item.get("capability") or "").strip()
            capability_version = (
                str(item.get("capability_version") or "").strip() or None
            )
            if not key or not description or not capability or key in seen:
                raise PlanError(
                    "Plan steps require unique key, description and capability."
                )
            seen.add(key)
            dependencies = tuple(
                str(x) for x in item.get("dependencies", []) if str(x).strip()
            )
            satisfies = tuple(int(x) for x in item.get("satisfies_criteria", []))
            raw_input = item.get("input")
            if raw_input is not None and not isinstance(raw_input, dict):
                raise PlanError(f"Plan step {key!r} input must be an object.")
            steps.append(
                PlannedStep(
                    key,
                    description,
                    capability,
                    dependencies,
                    satisfies,
                    capability_version,
                    raw_input,
                )
            )
        notes = tuple(str(x) for x in data.get("notes", []) if str(x).strip())
        return TaskPlan(tuple(steps), notes)
