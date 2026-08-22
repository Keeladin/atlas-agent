from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from atlas_core.authority import AUTHORITY_LEVELS, validate_authority
from atlas_core.capabilities.awareness import CapabilityAwareness, catalog as product_catalog


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

_DEFAULT_UNSUPPORTED_REASON = (
    "None of Atlas's briefable capabilities match this objective, "
    "so it cannot become Work yet."
)


@dataclass(frozen=True)
class TaskCriterion:
    text: str
    satisfaction_policy: Literal["deliverable", "evidence_grounded"] = "deliverable"
    semantic_verification: Literal["none", "required"] = "none"

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Task criterion text must not be empty")
        if self.satisfaction_policy not in {"deliverable", "evidence_grounded"}:
            raise ValueError("Unsupported task criterion policy")
        if self.semantic_verification not in {"none", "required"}:
            raise ValueError("Unsupported task criterion verification policy")

    def as_dict(self) -> dict[str, str]:
        return {
            "text": self.text,
            "satisfaction_policy": self.satisfaction_policy,
            "semantic_verification": self.semantic_verification,
        }


@dataclass(frozen=True)
class TaskCriterionBinding:
    criterion_ordinal: int
    capability_ordinal: int

    def __post_init__(self) -> None:
        if self.criterion_ordinal < 1 or self.capability_ordinal < 1:
            raise ValueError("Task criterion binding ordinals must be positive")

    def as_dict(self) -> dict[str, int]:
        return {
            "criterion_ordinal": self.criterion_ordinal,
            "capability_ordinal": self.capability_ordinal,
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
    completion_grounding_policy: Literal["none", "evidence_required"] = "none"
    criteria: tuple[TaskCriterion, ...] = ()
    criterion_bindings: tuple[TaskCriterionBinding, ...] = ()

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
        if self.completion_grounding_policy not in {"none", "evidence_required"}:
            raise ValueError("Unsupported completion grounding policy")
        if self.criteria:
            for binding in self.criterion_bindings:
                if binding.criterion_ordinal > len(self.criteria):
                    raise ValueError("Task criterion binding names an unknown criterion")
                if binding.capability_ordinal > len(self.capabilities):
                    raise ValueError("Task criterion binding names an unknown capability occurrence")
        elif self.criterion_bindings:
            raise ValueError("Task criterion bindings require structured criteria")
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
            "completion_grounding_policy": self.completion_grounding_policy,
            "criteria": [item.as_dict() for item in self.criteria],
            "criterion_bindings": [item.as_dict() for item in self.criterion_bindings],
        }


@dataclass(frozen=True)
class UnsupportedBrief:
    """Non-executable Advanced outcome. Not a TaskBrief and not Work."""

    objective: str
    reason: str
    closest_capability: str | None = None
    status: Literal["unsupported"] = "unsupported"

    def __post_init__(self) -> None:
        objective = self.objective.strip()
        if not objective:
            raise ValueError("UnsupportedBrief objective must not be empty")
        object.__setattr__(self, "objective", objective)
        reason = self.reason.strip()
        if not reason:
            raise ValueError("UnsupportedBrief reason must not be empty")
        object.__setattr__(self, "reason", reason)
        closest = (self.closest_capability or "").strip() or None
        if closest is not None:
            _reject_vendor_identity(closest)
            known = {item.id for item in product_catalog()}
            if closest not in known:
                raise ValueError(f"Unknown closest capability: {closest}")
        object.__setattr__(self, "closest_capability", closest)
        object.__setattr__(self, "status", "unsupported")

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "unsupported",
            "objective": self.objective,
            "reason": self.reason,
            "closest_capability": self.closest_capability,
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
    completion_grounding_policy: Literal["none", "evidence_required"] = "none",
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
        completion_grounding_policy=completion_grounding_policy,
    )


def unsupported_brief(
    *,
    objective: str,
    reason: str | None = None,
    closest_capability: str | None = None,
    candidate_capability_ids: tuple[str, ...] = (),
) -> UnsupportedBrief:
    """Build a typed non-executable result. Never invents a capability id."""

    known = {item.id for item in product_catalog()}
    closest = _known_capability_id(closest_capability, known)
    if closest is None:
        for raw in candidate_capability_ids:
            closest = _known_capability_id(raw, known)
            if closest is not None:
                break
    text = (reason or "").strip() or _DEFAULT_UNSUPPORTED_REASON
    return UnsupportedBrief(
        objective=objective,
        reason=text,
        closest_capability=closest,
    )


def _known_capability_id(raw: Any, known: set[str]) -> str | None:
    capability_id = str(raw or "").strip()
    if not capability_id:
        return None
    try:
        _reject_vendor_identity(capability_id)
    except ValueError:
        return None
    if capability_id not in known:
        return None
    return capability_id


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
