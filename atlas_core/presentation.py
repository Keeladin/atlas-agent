from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from atlas_core.work.store import WorkStore


def work_failure_reason(store: WorkStore, work_id: str) -> str | None:
    """Human-facing reason for failed work, or None if the work is not failed."""
    work = store.get_work(work_id)
    if work.status != "failed":
        return None
    for item in reversed(store.list_executions(work_id)):
        if item.status in {"fail", "blocked", "abstain", "rework"}:
            if item.error:
                return item.error
            return f"{item.capability} ended in {item.status}"
    for event in reversed(store.list_events(work_id)):
        if event.name == "work.failed":
            reason = (event.payload or {}).get("reason")
            if reason:
                return str(reason)
    failed_steps = [step for step in store.list_steps(work_id) if step.status == "failed"]
    if failed_steps:
        step = failed_steps[-1]
        return f"Step {step.ordinal} ({step.capability}) failed."
    return "Work failed without a recorded execution error."


@dataclass(frozen=True)
class WorkPresentation:
    work_id: str
    status: str
    objective: str
    criteria: tuple[dict[str, Any], ...]
    outputs: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]
    pending_approvals: tuple[dict[str, Any], ...]
    failures: tuple[dict[str, Any], ...]
    failure_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "status": self.status,
            "objective": self.objective,
            "criteria": list(self.criteria),
            "outputs": list(self.outputs),
            "claims": list(self.claims),
            "pending_approvals": list(self.pending_approvals),
            "failures": list(self.failures),
            "failure_reason": self.failure_reason,
        }

    def render_markdown(self) -> str:
        lines = [
            f"# Atlas work {self.work_id}",
            "",
            f"**Status:** {self.status}",
        ]
        if self.failure_reason:
            lines.extend(["", f"**Failure:** {self.failure_reason}"])
        lines.extend(["", self.objective, "", "## Success criteria"])
        for item in self.criteria:
            marker = "✓" if item["status"] == "accepted" else "✗" if item["status"] == "rejected" else "•"
            evidence = ", ".join(item["evidence_artifact_ids"]) or "no accepted evidence"
            lines.append(f"- {marker} {item['text']} — `{item['status']}` — evidence: {evidence}")
        if self.failures:
            lines.extend(["", "## Why it failed"])
            for item in self.failures:
                lines.append(
                    f"- `{item['capability']}` → `{item['status']}`: {item['error'] or 'no error text'}"
                )
        answers = [item for item in self.outputs if item["kind"] == "grounded_answer"]
        searches = [item for item in self.outputs if item["kind"] == "knowledge_search_results"]
        other = [item for item in self.outputs if item["kind"] not in {"grounded_answer", "knowledge_search_results"}]
        if answers:
            lines.extend(["", "## Grounded answer"])
            for item in answers:
                lines.append(item["preview"])
        if searches:
            lines.extend(["", "## Retrieved sources"])
            for item in searches:
                lines.append(item["preview"])
        if other:
            lines.extend(["", "## Accepted outputs"])
            for item in other:
                lines.append(
                    f"- `{item['kind']}` `{item['id']}` sha256 `{item['sha256'][:16]}…`: {item['preview']}"
                )
        remaining_claims = self.claims if not searches else tuple(
            item for item in self.claims if item["kind"] != "retrieved"
        )
        if remaining_claims:
            lines.extend(["", "## Recorded claims"])
            for item in remaining_claims:
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
        return "\n".join(lines).rstrip() + "\n"


def _preview(value: Any, limit: int = 1200) -> str:
    if isinstance(value, str):
        text = " ".join(value.split())
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clip(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _format_search_results(payload: Any) -> str:
    if not isinstance(payload, dict):
        return _preview(payload)
    results = payload.get("results") or []
    lines = [
        f"Query: {payload.get('query') or ''}",
        f"{len(results)} source-grounded hit(s).",
        "",
    ]
    for index, hit in enumerate(results, start=1):
        if not isinstance(hit, dict):
            continue
        title = hit.get("title") or hit.get("source_uri") or "Untitled"
        source = hit.get("source_uri") or ""
        digest = (hit.get("sha256") or "")[:12]
        lines.append(f"{index}. {title}")
        if source and source != title:
            lines.append(f"   {source}")
        lines.append(f"   {_clip(str(hit.get('text') or ''), 360)}")
        if digest:
            lines.append(f"   hash {digest}…")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_claim_value(kind: str, value: Any) -> str:
    if kind == "retrieved" and isinstance(value, dict):
        title = value.get("title") or value.get("source_uri") or "retrieved chunk"
        return f"{title}: {_clip(str(value.get('text') or ''), 220)}"
    return _preview(value)


class WorkPresenter:
    """Deterministic user-facing projection of durable runtime truth."""

    _INTERNAL_KINDS = {
        "verification_result",
        "execution_receipt",
        "task_plan",
        "planning_request",
        "morning_request",
        "knowledge_ingest_request",
        "knowledge_search_request",
    }

    def __init__(self, store: WorkStore) -> None:
        self.store = store

    def build(self, work_id: str) -> WorkPresentation:
        work = self.store.get_work(work_id)
        criteria = tuple(
            {
                "ordinal": item.ordinal,
                "text": item.text,
                "status": item.status,
                "evidence_artifact_ids": list(item.evidence_artifact_ids),
                "note": item.note,
            }
            for item in self.store.list_criteria(work_id)
        )
        accepted_evidence = {
            artifact_id
            for criterion in self.store.list_criteria(work_id)
            if criterion.status == "accepted"
            for artifact_id in criterion.evidence_artifact_ids
        }
        outputs = []
        for artifact in self.store.list_artifacts(work_id):
            if artifact.kind in self._INTERNAL_KINDS:
                continue
            if work.status == "completed" and accepted_evidence and artifact.id not in accepted_evidence:
                # Completed presentations privilege criterion-backed outputs.
                continue
            if artifact.kind == "capability_result" and work.status != "completed":
                continue
            if artifact.kind == "knowledge_search_results":
                preview = _format_search_results(artifact.payload)
                hits = artifact.payload.get("results") if isinstance(artifact.payload, dict) else []
            elif artifact.kind == "grounded_answer" and isinstance(artifact.payload, str):
                preview = artifact.payload.strip()
                hits = []
            else:
                preview = _preview(artifact.payload)
                hits = []
            outputs.append(
                {
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "sha256": artifact.sha256,
                    "preview": preview,
                    "hits": hits if isinstance(hits, list) else [],
                }
            )
        claims = tuple(
            {
                "id": item.id,
                "kind": item.kind,
                "subject": item.subject,
                "preview": _format_claim_value(item.kind, item.value),
                "evidence_artifact_ids": list(item.evidence_artifact_ids),
                "confidence": item.confidence,
            }
            for item in self.store.list_claims(work_id)
        )
        approvals = tuple(
            {
                "id": item.id,
                "step_id": item.step_id,
                "required_authority": item.required_authority,
                "requested_action": item.requested_action,
            }
            for item in self.store.list_approvals(work_id, status="pending")
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
            for item in self.store.list_executions(work_id)
            if item.status in {"fail", "blocked", "abstain", "rework"}
        )
        return WorkPresentation(
            work_id=work.id,
            status=work.status,
            objective=work.objective,
            criteria=criteria,
            outputs=tuple(outputs),
            claims=claims,
            pending_approvals=approvals,
            failures=failures,
            failure_reason=work_failure_reason(self.store, work_id),
        )
