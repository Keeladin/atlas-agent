from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from atlas_core.tasks import TaskStore


@dataclass(frozen=True)
class TaskPresentation:
    task_id: str
    status: str
    objective: str
    criteria: tuple[dict[str, Any], ...]
    outputs: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]
    pending_approvals: tuple[dict[str, Any], ...]
    failures: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "objective": self.objective,
            "criteria": list(self.criteria),
            "outputs": list(self.outputs),
            "claims": list(self.claims),
            "pending_approvals": list(self.pending_approvals),
            "failures": list(self.failures),
        }

    def render_markdown(self) -> str:
        lines = [
            f"# Atlas task {self.task_id}",
            "",
            f"**Status:** {self.status}",
            "",
            self.objective,
            "",
            "## Success criteria",
        ]
        for item in self.criteria:
            marker = "✓" if item["status"] == "accepted" else "✗" if item["status"] == "rejected" else "•"
            evidence = ", ".join(item["evidence_artifact_ids"]) or "no accepted evidence"
            lines.append(f"- {marker} {item['text']} — `{item['status']}` — evidence: {evidence}")
        if self.outputs:
            lines.extend(["", "## Accepted outputs"])
            for item in self.outputs:
                lines.append(
                    f"- `{item['kind']}` `{item['id']}` sha256 `{item['sha256'][:16]}…`: {item['preview']}"
                )
        if self.claims:
            lines.extend(["", "## Recorded claims"])
            for item in self.claims:
                lines.append(
                    f"- **{item['kind']}** `{item['subject']}` = {item['preview']} "
                    f"(evidence: {', '.join(item['evidence_artifact_ids']) or 'none'})"
                )
        if self.pending_approvals:
            lines.extend(["", "## Waiting for approval"])
            for item in self.pending_approvals:
                lines.append(
                    f"- `{item['id']}` requires `{item['required_authority']}`: {item['requested_action']}"
                )
        if self.failures:
            lines.extend(["", "## Execution failures / blocks"])
            for item in self.failures:
                lines.append(
                    f"- `{item['execution_id']}` `{item['capability']}` → `{item['status']}`: {item['error'] or 'no error text'}"
                )
        return "\n".join(lines).rstrip() + "\n"


def _preview(value: Any, limit: int = 1200) -> str:
    if isinstance(value, str):
        text = " ".join(value.split())
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class TaskPresenter:
    """Deterministic user-facing projection of durable runtime truth."""

    _INTERNAL_KINDS = {
        "verification_result",
        "execution_receipt",
        "task_plan",
        "morning_request",
        "knowledge_ingest_request",
        "knowledge_search_request",
    }

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def build(self, task_id: str) -> TaskPresentation:
        task = self.store.get_task(task_id)
        criteria = tuple(
            {
                "ordinal": item.ordinal,
                "text": item.text,
                "status": item.status,
                "evidence_artifact_ids": list(item.evidence_artifact_ids),
                "note": item.note,
            }
            for item in self.store.list_criteria(task_id)
        )
        accepted_evidence = {
            artifact_id
            for criterion in self.store.list_criteria(task_id)
            if criterion.status == "accepted"
            for artifact_id in criterion.evidence_artifact_ids
        }
        outputs = []
        for artifact in self.store.list_artifacts(task_id):
            if artifact.kind in self._INTERNAL_KINDS:
                continue
            if task.status == "completed" and accepted_evidence and artifact.id not in accepted_evidence:
                # Completed presentations privilege criterion-backed outputs.
                continue
            outputs.append(
                {
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "sha256": artifact.sha256,
                    "preview": _preview(artifact.payload),
                }
            )
        claims = tuple(
            {
                "id": item.id,
                "kind": item.kind,
                "subject": item.subject,
                "preview": _preview(item.value),
                "evidence_artifact_ids": list(item.evidence_artifact_ids),
                "confidence": item.confidence,
            }
            for item in self.store.list_claims(task_id)
        )
        approvals = tuple(
            {
                "id": item.id,
                "step_id": item.step_id,
                "required_authority": item.required_authority,
                "requested_action": item.requested_action,
            }
            for item in self.store.list_approvals(task_id, status="pending")
        )
        failures = tuple(
            {
                "execution_id": item.id,
                "step_id": item.step_id,
                "capability": item.capability,
                "provider": item.provider,
                "status": item.status,
                "error": item.error,
            }
            for item in self.store.list_executions(task_id)
            if item.status in {"fail", "blocked", "abstain", "rework"}
        )
        return TaskPresentation(
            task_id=task.id,
            status=task.status,
            objective=task.objective,
            criteria=criteria,
            outputs=tuple(outputs),
            claims=claims,
            pending_approvals=approvals,
            failures=failures,
        )
