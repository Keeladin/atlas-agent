from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from atlas_core.providers import ModelRequest

logger = logging.getLogger(__name__)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ObligationReconciler:
    """Resolve commitments from durable evidence; Work status alone is never authority."""

    def __init__(self, obligations, work_store, actions, evidence, chat_store, provider) -> None:
        self.obligations = obligations
        self.work_store = work_store
        self.actions = actions
        self.evidence = evidence
        self.chat_store = chat_store
        self.provider = provider

    def _occurrence_evidence(self, occurrence_id: str) -> dict[str, Any] | None:
        try:
            occurrence = self.actions.get(occurrence_id)
        except KeyError:
            return None
        evidence_rows = [row.as_dict() for row in self.evidence.for_occurrence(occurrence_id)]
        authoritative = next(
            (
                row for row in reversed(evidence_rows)
                if row["kind"] == "execution_receipt"
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("ok") is True
            ),
            None,
        )
        snapshot = {
            "occurrence_id": occurrence.occurrence_id,
            "capability_id": occurrence.capability_id,
            "status": occurrence.status,
            "policy_decision": occurrence.policy_decision,
            "result": occurrence.result,
            "receipt": occurrence.receipt,
            "evidence": evidence_rows,
        }
        return {
            **snapshot,
            "digest": _digest(snapshot),
            "authoritative_evidence_id": authoritative["evidence_id"] if authoritative else None,
        }

    def _support(self, obligation) -> dict[str, Any] | None:
        for binding in self.work_store.servicing(obligation.obligation_id):
            try:
                work = self.work_store.get(binding["work_id"])
                steps = self.work_store.steps(work.work_id)
            except KeyError:
                continue
            for step in steps:
                if not step.occurrence_id:
                    continue
                evidence = self._occurrence_evidence(step.occurrence_id)
                if evidence and evidence["status"] == "blocked" and evidence["policy_decision"] == "NO":
                    return {
                        "kind": "declined_policy",
                        "resolution_ref": f"action:{step.occurrence_id}",
                        "work_id": work.work_id,
                        "records": [evidence],
                        "digest": evidence["digest"],
                    }
            if work.status != "completed" or not steps or any(step.status != "completed" for step in steps):
                continue
            records: list[dict[str, Any]] = []
            valid = True
            for step in steps:
                evidence = self._occurrence_evidence(step.occurrence_id or "")
                if not evidence or evidence["status"] != "succeeded" or not evidence["authoritative_evidence_id"]:
                    valid = False
                    break
                records.append(evidence)
            if valid and records:
                return {
                    "kind": "fulfilled",
                    "resolution_ref": f"evidence:{records[-1]['authoritative_evidence_id']}",
                    "work_id": work.work_id,
                    "records": records,
                    "digest": _digest([row["digest"] for row in records]),
                }
        return None

    def reconcile_noncommunication(self) -> tuple[str, ...]:
        changed: list[str] = []
        for obligation in self.obligations.list_open(limit=5000):
            support = self._support(obligation)
            if support is None:
                continue
            if support["kind"] == "declined_policy":
                self.obligations.resolve(
                    obligation.obligation_id,
                    base_revision=obligation.revision,
                    resolution_kind="declined_policy",
                    resolution_ref=support["resolution_ref"],
                )
                changed.append(obligation.obligation_id)
                continue
            if obligation.kind != "state_change":
                continue
            self.obligations.resolve(
                obligation.obligation_id,
                base_revision=obligation.revision,
                resolution_kind="fulfilled",
                resolution_ref=support["resolution_ref"],
            )
            changed.append(obligation.obligation_id)
        return tuple(changed)

    def _reportable(self) -> dict[str, list[tuple[Any, dict[str, Any]]]]:
        grouped: dict[str, list[tuple[Any, dict[str, Any]]]] = {}
        for obligation in self.obligations.list_open(limit=5000):
            if obligation.kind != "communication":
                continue
            support = self._support(obligation)
            if support and support["kind"] == "fulfilled":
                grouped.setdefault(obligation.conversation_id, []).append((obligation, support))
        return grouped

    @staticmethod
    def _bounded_record(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "occurrence_id": record["occurrence_id"],
            "capability_id": record["capability_id"],
            "status": record["status"],
            "result": record.get("result"),
            "receipt": record.get("receipt"),
            "evidence_ids": [row["evidence_id"] for row in record.get("evidence", [])],
        }

    def _generate_report(self, reportable, outstanding) -> tuple[str, dict[str, Any]]:
        payload = {
            "reportable": [
                {
                    "obligation_id": obligation.obligation_id,
                    "text": obligation.text,
                    "evidence": [self._bounded_record(row) for row in support["records"]],
                }
                for obligation, support in reportable
            ],
            "still_open": [
                {"obligation_id": item.obligation_id, "text": item.text, "kind": item.kind}
                for item in outstanding
            ],
        }
        response = self.provider.generate(ModelRequest(
            capability_id="chat.obligation_report",
            system=(
                "Report only the supplied durable evidence to the owner. Cover every reportable communication obligation. "
                "If still_open is non-empty, explicitly say those commitments remain outstanding without inventing an outcome. "
                "Do not claim that Work completion alone proves an owner commitment. Return JSON only as {\"kind\":\"reply\",\"reply\":\"...\"}."
            ),
            input=json.dumps(payload, ensure_ascii=False, default=str),
            max_output_chars=3000,
            metadata={"response_format": {"type": "json_object"}},
        ))
        try:
            parsed = json.loads(str(response.text or "").strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("obligation report was not valid JSON") from exc
        if not isinstance(parsed, dict) or parsed.get("kind") != "reply":
            raise RuntimeError("obligation report shape is invalid")
        reply = str(parsed.get("reply") or "").strip()
        if not reply:
            raise RuntimeError("obligation report is empty")
        return reply, {
            "provider": getattr(response, "provider_key", None),
            "model": getattr(response, "model", None),
            "metrics": dict(getattr(response, "metrics", {}) or {}),
            "basis": payload,
        }

    def _verify_report(self, candidate: str, basis: dict[str, Any]) -> dict[str, Any]:
        response = self.provider.generate(ModelRequest(
            capability_id="chat.obligation_report_verify",
            system=(
                "Verify the candidate owner-facing report only against the supplied report basis. "
                "Every factual claim must be supported, every reportable obligation must be covered, and still-open obligations "
                "must not be described as fulfilled. Return JSON with grounded boolean and unsupported_claims array."
            ),
            input=json.dumps({"candidate": candidate, "basis": basis}, ensure_ascii=False, default=str),
            max_output_chars=1800,
            metadata={"response_format": {"type": "json_object"}},
        ))
        try:
            parsed = json.loads(str(response.text or "").strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("obligation report verifier was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("obligation report verifier shape is invalid")
        grounded = parsed.get("grounded")
        unsupported = parsed.get("unsupported_claims")
        if not isinstance(grounded, bool) or not isinstance(unsupported, list):
            raise RuntimeError("obligation report verifier shape is invalid")
        if not all(isinstance(item, str) for item in unsupported):
            raise RuntimeError("obligation report verifier claims are invalid")
        return {
            "grounded": bool(grounded and not unsupported),
            "unsupported_claims": unsupported,
            "provider": getattr(response, "provider_key", None),
            "model": getattr(response, "model", None),
        }

    def _snapshot_current(self, conversation_id: str, reportable) -> tuple[dict[str, int], tuple[str, ...], dict[str, str]]:
        all_open = [
            item for item in self.obligations.list_open(limit=5000)
            if item.conversation_id == conversation_id
        ]
        revisions = {item.obligation_id: item.revision for item in all_open}
        reportable_ids = tuple(item.obligation_id for item, _support in reportable)
        digests = {item.obligation_id: support["digest"] for item, support in reportable}
        return revisions, reportable_ids, digests

    def report_communications(self, *, limit_conversations: int = 4) -> tuple[str, ...]:
        persisted: list[str] = []
        grouped = self._reportable()
        for conversation_id, reportable in list(grouped.items())[:max(1, int(limit_conversations))]:
            snapshot_revisions, reportable_ids, snapshot_digests = self._snapshot_current(
                conversation_id, reportable
            )
            reportable_set = set(reportable_ids)
            outstanding = [
                self.obligations.get(oid)
                for oid in snapshot_revisions
                if oid not in reportable_set
            ]
            try:
                candidate, generation = self._generate_report(reportable, outstanding)
                verification = self._verify_report(candidate, generation["basis"])
            except Exception:
                logger.warning("obligation report generation/verification failed", exc_info=True)
                continue
            if not verification["grounded"]:
                continue

            current = [
                item for item in self.obligations.list_open(limit=5000)
                if item.conversation_id == conversation_id
            ]
            current_revisions = {item.obligation_id: item.revision for item in current}
            if current_revisions != snapshot_revisions:
                continue
            current_reportable: list[tuple[Any, dict[str, Any]]] = []
            current_digests: dict[str, str] = {}
            for item in current:
                if item.kind != "communication":
                    continue
                support = self._support(item)
                if support and support["kind"] == "fulfilled":
                    current_reportable.append((item, support))
                    current_digests[item.obligation_id] = support["digest"]
            if tuple(item.obligation_id for item, _support in current_reportable) != reportable_ids:
                continue
            if current_digests != snapshot_digests:
                continue
            revisions_to_resolve = {
                item.obligation_id: snapshot_revisions[item.obligation_id]
                for item, _support in reportable
            }
            turn = self.obligations.persist_communication_report(
                conversation_id,
                candidate,
                obligation_revisions=revisions_to_resolve,
                metadata={
                    "obligation_report": {
                        "obligation_ids": list(reportable_ids),
                        "obligation_revisions": revisions_to_resolve,
                        "outstanding_obligation_revisions": {
                            oid: revision for oid, revision in snapshot_revisions.items()
                            if oid not in reportable_set
                        },
                        "evidence_digests": snapshot_digests,
                        "evidence_ids": sorted({
                            evidence_row["evidence_id"]
                            for _item, support in reportable
                            for record in support["records"]
                            for evidence_row in record.get("evidence", [])
                        }),
                        "generation": {k: v for k, v in generation.items() if k != "basis"},
                        "verification": verification,
                    }
                },
            )
            persisted.append(turn["turn_id"])
        return tuple(persisted)

    def tick(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        lapsed = self.obligations.observe_lapses(now)
        resolved = self.reconcile_noncommunication()
        reports = self.report_communications()
        return {"lapsed": lapsed, "resolved": resolved, "reports": reports}
