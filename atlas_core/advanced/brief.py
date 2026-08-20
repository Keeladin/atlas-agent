from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas_core.authority import AUTHORITY_LEVELS, validate_authority
from atlas_core.capabilities.awareness import CapabilityAwareness


_EXPECTED_EFFECT = {
    "automation.workflow.create": "Create an automation workflow",
    "automation.workflow.execute": "Execute an automation workflow",
    "communication.email.send": "external communication",
    "knowledge.index": "Index local knowledge",
}

_DELIVERABLE_KIND = {
    "automation.workflow.create": "automation",
    "automation.workflow.execute": "automation",
    "communication.email.send": "communication",
    "knowledge.index": "knowledge",
}


@dataclass(frozen=True)
class TaskBrief:
    """Desired Atlas work. A value object. Not a task and not an execution."""

    objective: str
    capabilities: tuple[str, ...]
    required_authority: str
    expected_effect: str
    constraints: tuple[str, ...] = ()
    deliverable_kind: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        objective = self.objective.strip()
        if not objective:
            raise ValueError("TaskBrief objective must not be empty")
        object.__setattr__(self, "objective", objective)
        if not self.capabilities:
            raise ValueError("TaskBrief requires at least one capability id")
        object.__setattr__(self, "required_authority", validate_authority(self.required_authority))
        effect = self.expected_effect.strip()
        if not effect:
            raise ValueError("TaskBrief expected_effect must not be empty")
        object.__setattr__(self, "expected_effect", effect)
        for capability_id in self.capabilities:
            _reject_vendor_identity(capability_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "capabilities": list(self.capabilities),
            "required_authority": self.required_authority,
            "expected_effect": self.expected_effect,
            "constraints": list(self.constraints),
            "deliverable_kind": self.deliverable_kind,
            "notes": self.notes,
        }


def assemble_brief(
    *,
    objective: str,
    capability_ids: tuple[str, ...],
    catalog: tuple[CapabilityAwareness, ...],
    expected_effect: str | None = None,
    constraints: tuple[str, ...] = (),
    deliverable_kind: str | None = None,
    notes: str | None = None,
) -> TaskBrief:
    selected = resolve_capabilities(capability_ids, catalog)
    effect = (expected_effect or "").strip() or classify_expected_effect(selected)
    kind = (deliverable_kind or "").strip() or classify_deliverable_kind(selected)
    return TaskBrief(
        objective=objective,
        capabilities=tuple(item.id for item in selected),
        required_authority=required_authority_for(selected),
        expected_effect=effect,
        constraints=constraints,
        deliverable_kind=kind or None,
        notes=notes,
    )


def resolve_capabilities(
    capability_ids: tuple[str, ...],
    catalog: tuple[CapabilityAwareness, ...],
) -> tuple[CapabilityAwareness, ...]:
    index = {item.id: item for item in catalog}
    selected: list[CapabilityAwareness] = []
    seen: set[str] = set()
    for raw in capability_ids:
        capability_id = str(raw or "").strip()
        _reject_vendor_identity(capability_id)
        if capability_id not in index:
            raise ValueError(f"Unknown or non-briefable capability: {capability_id}")
        if capability_id in seen:
            continue
        seen.add(capability_id)
        selected.append(index[capability_id])
    if not selected:
        raise ValueError("TaskBrief requires at least one capability id")
    return tuple(selected)


def required_authority_for(selected: tuple[CapabilityAwareness, ...]) -> str:
    return max(
        (validate_authority(item.required_authority) for item in selected),
        key=lambda level: AUTHORITY_LEVELS.index(level),
    )


def classify_expected_effect(selected: tuple[CapabilityAwareness, ...]) -> str:
    for item in selected:
        if item.id in _EXPECTED_EFFECT:
            return _EXPECTED_EFFECT[item.id]
    return selected[0].description


def classify_deliverable_kind(selected: tuple[CapabilityAwareness, ...]) -> str | None:
    for item in selected:
        if item.id in _DELIVERABLE_KIND:
            return _DELIVERABLE_KIND[item.id]
    return None


def _reject_vendor_identity(capability_id: str) -> None:
    if not capability_id:
        raise ValueError("capability id must not be empty")
    lowered = capability_id.casefold()
    if "n8n" in lowered or lowered.split(".", 1)[0] == "mcp":
        raise ValueError(f"TaskBrief cannot name a provider implementation: {capability_id}")
