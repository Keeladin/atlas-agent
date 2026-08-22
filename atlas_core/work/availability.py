from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from atlas_core.advanced.brief import TaskBrief

from .contract import WorkContract
from .inventory import DeploymentInventory
from .resolve import ImplementationResolver, ResolveReport
from .work import WorkError


_PDF_RE = re.compile(r"\bpdfs?\b|\.pdf\b", re.IGNORECASE)

_CAPABILITY_PHRASE = {
    "knowledge.index": "knowledge indexing",
    "knowledge.ingest_text": "text knowledge ingestion",
    "knowledge.search": "local knowledge search",
    "knowledge.answer": "grounded knowledge answers",
    "documents.multimodal": "document interpretation",
    "automation.workflow.create": "creating automation workflows",
    "automation.workflow.execute": "running automation workflows",
    "communication.email.send": "sending email",
    "coding.software_engineering": "software implementation",
}


@dataclass(frozen=True)
class UnavailableAcceptance:
    """Typed accept refusal. Not Work and not a TaskBrief."""

    objective: str
    reason: str
    capabilities: tuple[str, ...]
    unarmed: tuple[str, ...]
    mismatches: tuple[str, ...]
    status: Literal["unavailable"] = "unavailable"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "objective": self.objective,
            "reason": self.reason,
            "capabilities": list(self.capabilities),
            "unarmed": list(self.unarmed),
            "mismatches": list(self.mismatches),
        }


class UnavailableWork(WorkError):
    """Required capabilities cannot execute on this deployment."""

    def __init__(self, result: UnavailableAcceptance) -> None:
        super().__init__(result.reason)
        self.result = result


def assess_executability(
    contract: WorkContract,
    inventory,
    tools,
) -> ResolveReport:
    return ImplementationResolver().resolve(contract, inventory, tools)


def refuse_if_unexecutable(
    *,
    brief: TaskBrief,
    contract: WorkContract,
    inventory: DeploymentInventory,
    tools,
) -> ResolveReport:
    """Raise UnavailableWork when required pins cannot run here.

    Does not invent substitute capabilities or re-pin the contract.
    """

    report = assess_executability(contract, inventory, tools)
    unarmed = report.unarmed
    mismatches = tuple(item.capability_id for item in report.mismatches)
    if not unarmed and not mismatches:
        return report
    result = UnavailableAcceptance(
        objective=brief.objective,
        reason=explain_unavailable(brief, unarmed=unarmed, mismatches=mismatches),
        capabilities=tuple(pin.capability_id for pin in contract.capabilities),
        unarmed=unarmed,
        mismatches=mismatches,
    )
    raise UnavailableWork(result)


def explain_unavailable(
    brief: TaskBrief,
    *,
    unarmed: tuple[str, ...],
    mismatches: tuple[str, ...],
) -> str:
    missing = tuple(dict.fromkeys((*unarmed, *mismatches)))
    blob = " ".join(
        part
        for part in (brief.objective, brief.notes or "", *brief.constraints)
        if part
    )
    pdf = bool(_PDF_RE.search(blob))
    if pdf and any(
        item in missing for item in ("knowledge.index", "knowledge.ingest_text", "documents.multimodal")
    ):
        return (
            "Atlas can't do this yet because PDF ingestion is not available on this host."
        )
    if "knowledge.index" in missing:
        return (
            "Atlas can't do this yet because knowledge indexing isn't available on this host."
        )
    if missing:
        phrases = [_CAPABILITY_PHRASE.get(item, item.replace(".", " ")) for item in missing]
        if len(phrases) == 1:
            need = phrases[0]
        else:
            need = ", ".join(phrases[:-1]) + f", and {phrases[-1]}"
        return f"Atlas can't do this yet because {need} is not available on this host."
    return "Atlas can't do this yet because the required capability is not available on this host."
