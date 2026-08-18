from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from atlas_core.tasks.store import TaskStore


CONTEXT_PROFILES: dict[str, str] = {
    "research": "Investigate before concluding. Separate evidence, uncertainty and inference. Do not take side effects unless explicitly required.",
    "plan": "Produce a bounded plan with explicit dependencies and success criteria. Do not execute the plan.",
    "execute": "Perform only the bounded step. Respect constraints, authority and supplied evidence. Return explicit outputs and limitations.",
    "review": "Review supplied work against its contract. Do not silently repair it; identify pass, rework, abstain or fail with reasons.",
    "verify": "Verify claimed completion using supplied artifacts and criteria. Prefer deterministic evidence and abstain when evidence is insufficient.",
    "present": "Present accepted results faithfully without adding unsupported facts or claiming actions that lack receipts.",
}


@dataclass(frozen=True)
class ContextPack:
    payload: dict[str, Any]
    chars: int
    omitted_artifact_ids: tuple[str, ...]

    def as_text(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, sort_keys=True)


class ContextBuilder:
    """Build bounded projections of durable task state for one execution frame."""

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def build(
        self,
        task_id: str,
        step_id: str,
        *,
        artifact_ids: tuple[str, ...],
        profile: str,
        max_chars: int,
    ) -> ContextPack:
        if profile not in CONTEXT_PROFILES:
            raise ValueError(f"Unknown context profile: {profile}")
        task = self.store.get_task(task_id)
        step = self.store.get_step(step_id)
        if step.task_id != task_id:
            raise ValueError("Step does not belong to task.")
        criteria = [asdict(item) for item in self.store.list_criteria(task_id)]
        claims = [asdict(item) for item in self.store.list_claims(task_id)]
        base: dict[str, Any] = {
            "task": {
                "id": task.id,
                "objective": task.objective,
                "success_criteria": list(task.success_criteria),
                "constraints": list(task.constraints),
                "authority_scope": task.authority_scope,
                "status": task.status,
            },
            "step": asdict(step),
            "criteria": criteria,
            "claims": claims,
            "context_profile": {"name": profile, "instruction": CONTEXT_PROFILES[profile]},
            "artifacts": [],
            "omitted_artifacts": [],
        }
        omitted: list[str] = []
        for artifact_id in artifact_ids:
            artifact = self.store.get_artifact(artifact_id)
            if artifact.task_id != task_id:
                raise ValueError("Context artifact belongs to another task.")
            full = {
                "id": artifact.id,
                "kind": artifact.kind,
                "sha256": artifact.sha256,
                "metadata": artifact.metadata,
                "payload": artifact.payload,
            }
            tentative = dict(base)
            tentative["artifacts"] = [*base["artifacts"], full]
            size = len(json.dumps(tentative, ensure_ascii=False, sort_keys=True))
            if size <= max_chars:
                base["artifacts"].append(full)
            else:
                omitted.append(artifact.id)
                base["omitted_artifacts"].append({
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "sha256": artifact.sha256,
                    "metadata": artifact.metadata,
                    "reason": "payload omitted from bounded frame; retrieve explicitly if required",
                })
        chars = len(json.dumps(base, ensure_ascii=False, sort_keys=True))
        if chars > max_chars:
            base["claims"] = [
                {"id": claim["id"], "kind": claim["kind"], "subject": claim["subject"], "evidence_artifact_ids": claim["evidence_artifact_ids"]}
                for claim in claims
            ]
            chars = len(json.dumps(base, ensure_ascii=False, sort_keys=True))
        if chars > max_chars:
            raise ValueError(
                f"Essential execution context is {chars} chars, above capability budget {max_chars}; split the step or increase its explicit budget."
            )
        return ContextPack(base, chars, tuple(omitted))
