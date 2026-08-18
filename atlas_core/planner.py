from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from atlas_core.capabilities import CapabilitySpec
from atlas_core.providers import ModelRequest, ModelRouter
from atlas_core.tasks import TaskRecord, TaskStore


@dataclass(frozen=True)
class PlannedStep:
    key: str
    description: str
    capability: str
    dependencies: tuple[str, ...]
    satisfies_criteria: tuple[int, ...] = ()


@dataclass(frozen=True)
class TaskPlan:
    steps: tuple[PlannedStep, ...]
    notes: tuple[str, ...] = ()


class PlanError(RuntimeError):
    pass


class TaskPlanner:
    """Bounded planning capability that creates durable task graphs from strict JSON."""

    SYSTEM = (
        "You are Atlas's bounded planner. Return JSON only with keys steps and notes. "
        "steps is an array of objects: key, description, capability, dependencies, satisfies_criteria. "
        "dependencies contains step keys, never prose. Do not execute work. Do not invent capabilities outside the supplied manifest."
    )

    def __init__(self, *, store: TaskStore, model_router: ModelRouter, planning_capability: CapabilitySpec, capability_manifest: list[dict[str, Any]]) -> None:
        self.store = store
        self.model_router = model_router
        self.planning_capability = planning_capability
        self.capability_manifest = capability_manifest

    def plan_and_create(
        self,
        *,
        objective: str,
        success_criteria: tuple[str, ...],
        constraints: tuple[str, ...] = (),
        authority_scope: str = "read",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[TaskRecord, TaskPlan]:
        prompt = json.dumps({
            "objective": objective,
            "success_criteria": list(success_criteria),
            "constraints": list(constraints),
            "authority_scope": authority_scope,
            "capabilities": self.capability_manifest,
        }, ensure_ascii=False)
        route = self.model_router.select(self.planning_capability, context_chars=len(prompt))
        response = route.provider.generate(ModelRequest(self.planning_capability.id, self.SYSTEM, prompt))
        plan = self.parse_plan(response.text)
        task = self.store.create_task(objective=objective, success_criteria=success_criteria, constraints=constraints, authority_scope=authority_scope, metadata={**(metadata or {}), "planned_by": response.provider_key})
        ids: dict[str, str] = {}
        pending = list(plan.steps)
        while pending:
            progressed = False
            for step in list(pending):
                if all(dep in ids for dep in step.dependencies):
                    record = self.store.add_step(
                        task.id,
                        description=step.description,
                        capability=step.capability,
                        dependencies=[ids[dep] for dep in step.dependencies],
                        metadata={"satisfies_criteria": list(step.satisfies_criteria), "plan_key": step.key},
                    )
                    ids[step.key] = record.id
                    pending.remove(step)
                    progressed = True
            if not progressed:
                unresolved = {step.key: step.dependencies for step in pending}
                raise PlanError(f"Plan contains missing or cyclic dependencies: {unresolved}")
        return task, plan

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
            if not key or not description or not capability or key in seen:
                raise PlanError("Plan steps require unique key, description and capability.")
            seen.add(key)
            dependencies = tuple(str(x) for x in item.get("dependencies", []) if str(x).strip())
            satisfies = tuple(int(x) for x in item.get("satisfies_criteria", []))
            steps.append(PlannedStep(key, description, capability, dependencies, satisfies))
        notes = tuple(str(x) for x in data.get("notes", []) if str(x).strip())
        return TaskPlan(tuple(steps), notes)
