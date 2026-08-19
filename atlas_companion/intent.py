from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from atlas_core.deliverable import has_quality_criteria, infer_deliverable
from atlas_core.knowledge import (
    is_knowledge_question,
    parse_ingest_objective,
    parse_search_objective,
)


@dataclass(frozen=True)
class TaskIntent:
    objective: str
    criteria: tuple[str, ...]
    authority: str
    deliverable_kind: str
    inferred_criteria: bool
    inferred_authority: bool
    workflow: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "criteria": list(self.criteria),
            "authority": self.authority,
            "deliverable_kind": self.deliverable_kind,
            "inferred_criteria": self.inferred_criteria,
            "inferred_authority": self.inferred_authority,
            "workflow": self.workflow,
        }


def normalize_criteria(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = value.splitlines()
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise ValueError("criteria must be a list of non-empty strings")
    return tuple(str(item).strip() for item in items if str(item).strip())


def infer_criteria(objective: str, *, supplied: Iterable[str] = ()) -> tuple[str, ...]:
    given = tuple(item for item in supplied if item)
    if given:
        return given
    text = (objective or "").strip()
    if parse_ingest_objective(text):
        return ("The source is durably indexed with chunk provenance.",)
    query = parse_search_objective(text)
    if query:
        if is_knowledge_question(query):
            return (
                "A source-grounded local knowledge search result is produced.",
                "An evidence-grounded answer cites retrieved sources.",
            )
        return ("A source-grounded local knowledge search result is produced.",)
    contract = infer_deliverable(text, ())
    if contract.kind == "narrative":
        criteria = [
            f"Produce {contract.must_produce}.",
            "Include the requested premise in the artifact.",
        ]
        if has_quality_criteria((text,)):
            criteria.append("Make it believable and coherent.")
        return tuple(criteria)
    if contract.kind == "code":
        return (f"Produce {contract.must_produce}.",)
    if contract.kind == "analysis":
        return (f"Produce {contract.must_produce}.",)
    if contract.kind == "conversation":
        return ("A direct conversational reply is produced.",)
    return ("Produce a truthful answer.",)


def infer_authority(objective: str, *, supplied: str | None = None) -> str:
    raw = (supplied or "").strip().casefold()
    if raw and raw not in {"auto", "automatic"}:
        return supplied.strip()
    text = (objective or "").strip()
    if parse_ingest_objective(text):
        return "modify_internal"
    query = parse_search_objective(text)
    if query and not is_knowledge_question(query):
        return "read"
    return "interpret"


def preview_intent(
    objective: str,
    *,
    criteria: Any = None,
    authority: str | None = None,
) -> TaskIntent:
    text = (objective or "").strip()
    if not text:
        raise ValueError("Task objective must not be empty.")
    supplied = normalize_criteria(criteria)
    resolved = infer_criteria(text, supplied=supplied)
    granted = infer_authority(text, supplied=authority)
    workflow = None
    if parse_ingest_objective(text):
        workflow = "knowledge_ingest"
    elif parse_search_objective(text):
        workflow = "knowledge_search"
    contract = infer_deliverable(text, resolved)
    return TaskIntent(
        objective=text,
        criteria=resolved,
        authority=granted,
        deliverable_kind=contract.kind,
        inferred_criteria=not supplied,
        inferred_authority=(supplied_authority_is_auto(authority)),
        workflow=workflow,
    )


def supplied_authority_is_auto(value: str | None) -> bool:
    raw = (value or "").strip().casefold()
    return raw in {"", "auto", "automatic"}
