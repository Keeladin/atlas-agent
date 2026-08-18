from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from atlas_core.authority import authority_allows
from atlas_core.capabilities import CapabilitySpec
from atlas_core.context import ContextBuilder
from atlas_core.providers import ModelRequest, ModelRouter
from atlas_core.tasks import TaskRecord, TaskStore


@dataclass(frozen=True)
class PlannedStep:
    key: str
    description: str
    capability: str
    dependencies: tuple[str, ...]
    satisfies_criteria: tuple[int, ...] = ()
    capability_version: str | None = None


@dataclass(frozen=True)
class TaskPlan:
    steps: tuple[PlannedStep, ...]
    notes: tuple[str, ...] = ()


class PlanError(RuntimeError):
    pass


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
        planning_capability: CapabilitySpec,
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
                "planner_version": self.planning_capability.version,
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
                    ],
                    "rules": [
                        "dependencies contain step keys, never prose",
                        "do not execute work",
                        "use only supplied capability ids/versions",
                    ],
                },
            },
            metadata={"purpose": "bounded durable planning input"},
        )
        planning_step = self.store.add_step(
            task.id,
            description="Create a bounded capability plan for this task.",
            capability=self.planning_capability.id,
            capability_version=self.planning_capability.version,
            input_artifact_ids=(planning_input.id,),
            metadata={"internal_planning": True},
        )
        if not authority_allows(
            authority_scope,
            self.planning_capability.required_authority,
        ):
            self.store.set_task_status(task.id, "failed")
            self.store.create_checkpoint(
                task.id,
                reason="planning authority insufficient",
            )
            raise PlanError(
                "Planning requires authority "
                f"{self.planning_capability.required_authority!r}; "
                f"task grants {authority_scope!r}."
            )
        if task.status == "planned":
            self.store.set_task_status(task.id, "active")
        execution = self.store.begin_execution(
            task.id,
            step_id=planning_step.id,
            capability=self.planning_capability.id,
            capability_version=self.planning_capability.version,
            input_artifact_ids=(planning_input.id,),
        )
        try:
            pack = self.context_builder.build(
                task.id,
                planning_step.id,
                artifact_ids=(planning_input.id,),
                required_artifact_ids=(planning_input.id,),
                execution_id=execution.id,
                capability=self.planning_capability,
                max_chars=self.planning_capability.budget.max_context_chars,
            )
            manifest_record = self.store.write_context_manifest(
                task.id,
                step_id=planning_step.id,
                execution_id=execution.id,
                capability=self.planning_capability.id,
                capability_version=self.planning_capability.version,
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
                    "capability_version": self.planning_capability.version,
                    "tokens": pack.manifest.total_tokens,
                    "budget": pack.manifest.budget_tokens,
                },
            )
            route = self.model_router.select(
                self.planning_capability,
                context_chars=pack.chars,
            )
            projected_cost = route.provider.spec.estimate_cost_usd(
                input_tokens=pack.tokens,
                output_tokens=max(
                    1,
                    ((self.planning_capability.budget.max_output_chars or 8_000) + 3) // 4,
                ),
            )
            if (
                self.planning_capability.budget.max_cost_usd is not None
                and projected_cost is not None
                and projected_cost > self.planning_capability.budget.max_cost_usd
            ):
                raise PlanError(
                    "Projected planning cost exceeds capability budget: "
                    f"{projected_cost:.6f}>"
                    f"{self.planning_capability.budget.max_cost_usd:.6f}"
                )
            self.store.set_execution_provider(execution.id, route.provider.spec.key)
            response = route.provider.generate(
                ModelRequest(
                    self.planning_capability.id,
                    json.dumps(pack.payload["system"], ensure_ascii=False, sort_keys=True),
                    pack.as_text(),
                    max_output_chars=self.planning_capability.budget.max_output_chars,
                    metadata={
                        "task_id": task.id,
                        "step_id": planning_step.id,
                        "context_manifest_id": manifest_record.id,
                        "capability_version": self.planning_capability.version,
                    },
                )
            )
            if (
                self.planning_capability.budget.max_output_chars is not None
                and len(response.text) > self.planning_capability.budget.max_output_chars
            ):
                raise PlanError(
                    "Planner output exceeds explicit capability output budget."
                )
            plan = self._pin_and_validate_plan(
                self.parse_plan(response.text),
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
                    record = self.store.add_step(
                        task.id,
                        description=item.description,
                        capability=item.capability,
                        capability_version=item.capability_version,
                        dependencies=[ids[dep] for dep in item.dependencies],
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
        return TaskPlan(tuple(pinned), plan.notes)

    @staticmethod
    def parse_plan(text: str) -> TaskPlan:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlanError("Planner did not return valid JSON.") from exc
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
            steps.append(
                PlannedStep(
                    key,
                    description,
                    capability,
                    dependencies,
                    satisfies,
                    capability_version,
                )
            )
        notes = tuple(str(x) for x in data.get("notes", []) if str(x).strip())
        return TaskPlan(tuple(steps), notes)
