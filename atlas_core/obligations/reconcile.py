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
        basis_rows = [row for row in evidence_rows if row["kind"] != "obligation_fulfilment_verification"]
        authoritative = next(
            (
                row for row in reversed(basis_rows)
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
            "evidence": basis_rows,
        }
        return {
            **snapshot,
            "digest": _digest(snapshot),
            "authoritative_evidence_id": authoritative["evidence_id"] if authoritative else None,
        }

    def _support(self, obligation) -> dict[str, Any] | None:
        """Return evidence from mechanisms explicitly bound to this obligation only."""
        for binding in self.work_store.servicing(obligation.obligation_id):
            mechanism_kind = str(binding.get("mechanism_kind") or "")
            mechanism_id = str(binding.get("mechanism_id") or "")
            occurrence_id = ""
            work_id = binding.get("work_id")
            if mechanism_kind == "occurrence":
                occurrence_id = mechanism_id
            elif mechanism_kind == "work_step":
                try:
                    step = self.work_store.step(mechanism_id)
                except KeyError:
                    continue
                if work_id and step.work_id != work_id:
                    continue
                occurrence_id = str(step.occurrence_id or "")
            else:
                continue
            if not occurrence_id:
                continue
            evidence = self._occurrence_evidence(occurrence_id)
            if evidence is None:
                continue
            if evidence["status"] == "blocked" and evidence["policy_decision"] == "NO":
                return {
                    "kind": "declined_policy",
                    "resolution_ref": f"action:{occurrence_id}",
                    "work_id": work_id,
                    "mechanism_kind": mechanism_kind,
                    "mechanism_id": mechanism_id,
                    "records": [evidence],
                    "digest": evidence["digest"],
                }
            if evidence["status"] != "succeeded" or not evidence["authoritative_evidence_id"]:
                continue
            return {
                "kind": "fulfilled",
                "resolution_ref": f"evidence:{evidence['authoritative_evidence_id']}",
                "work_id": work_id,
                "mechanism_kind": mechanism_kind,
                "mechanism_id": mechanism_id,
                "records": [evidence],
                "digest": evidence["digest"],
            }
        return None

    def _verify_state_change(self, obligation, support: dict[str, Any]) -> str | None:
        """Verify that bound action evidence actually proves this specific outcome."""
        record = support["records"][-1]
        occurrence_id = str(record["occurrence_id"])
        evidence_digest = str(support["digest"])
        for existing in self.evidence.for_occurrence(occurrence_id):
            payload = existing.payload if isinstance(existing.payload, dict) else {}
            if (
                existing.kind == "obligation_fulfilment_verification"
                and payload.get("obligation_id") == obligation.obligation_id
                and payload.get("evidence_digest") == evidence_digest
                and payload.get("fulfilled") is True
            ):
                return existing.evidence_id

        basis = {
            "obligation": {
                "obligation_id": obligation.obligation_id,
                "text": obligation.text,
                "grounding_excerpt": obligation.grounding_excerpt,
            },
            "action_evidence": self._bounded_record(record),
            "evidence_digest": evidence_digest,
        }
        try:
            response = self.provider.generate(ModelRequest(
                capability_id="obligation.state_change_verify",
                system=(
                    "Decide only whether the supplied durable action/observation evidence proves the exact owner obligation. "
                    "A servicing binding is not proof. Work status is not proof. Return JSON with fulfilled boolean and reason string. "
                    "Fail closed when the evidence could be unrelated, incomplete, merely attempted, or only preparatory."
                ),
                input=json.dumps(basis, ensure_ascii=False, default=str),
                max_output_chars=1200,
                metadata={"response_format": {"type": "json_object"}},
            ))
            parsed = json.loads(str(response.text or "").strip())
        except Exception:
            logger.warning("state-change obligation verification failed", exc_info=True)
            return None
        if not isinstance(parsed, dict) or not isinstance(parsed.get("fulfilled"), bool):
            return None
        reason = str(parsed.get("reason") or "").strip()
        if parsed["fulfilled"] is not True:
            return None
        verification = self.evidence.add(
            occurrence_id,
            "obligation_fulfilment_verification",
            {
                "obligation_id": obligation.obligation_id,
                "evidence_digest": evidence_digest,
                "fulfilled": True,
                "reason": reason,
                "provider": getattr(response, "provider_key", None),
                "model": getattr(response, "model", None),
            },
        )
        return verification.evidence_id

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
            verification_id = self._verify_state_change(obligation, support)
            if verification_id is None:
                continue
            self.obligations.resolve(
                obligation.obligation_id,
                base_revision=obligation.revision,
                resolution_kind="fulfilled",
                resolution_ref=f"evidence:{verification_id}",
            )
            changed.append(obligation.obligation_id)
        return tuple(changed)

    def _reportable(self) -> dict[str, list[tuple[Any, dict[str, Any]]]]:
        """Group reportable communication obligations by owner turn: one turn, one commitment set."""
        grouped: dict[str, list[tuple[Any, dict[str, Any]]]] = {}
        for obligation in self.obligations.list_open(limit=5000):
            if obligation.kind != "communication":
                continue
            support = self._support(obligation)
            if support and support["kind"] == "fulfilled":
                grouped.setdefault(obligation.owner_turn_id, []).append((obligation, support))
        return grouped

    def _servicing_state(self, obligation) -> str:
        """Deterministic servicing truth for an open obligation, derived from bound evidence."""
        support = self._support(obligation)
        if support is None:
            return "unserviced"
        return "declined_policy" if support["kind"] == "declined_policy" else "awaiting_verification"

    def _fulfilled_in_turn(self, owner_turn_id: str) -> list[dict[str, Any]]:
        """Verified outcomes already resolved for this owner turn, so the report may state them."""
        rows: list[dict[str, Any]] = []
        for item in self.obligations.for_turn(owner_turn_id):
            if item.status != "resolved" or item.resolution_kind != "fulfilled":
                continue
            ref = str(item.resolution_ref or "")
            if not ref.startswith("evidence:"):
                continue
            try:
                record = self.evidence.get(ref.split(":", 1)[1])
            except KeyError:
                continue
            occurrence = self._occurrence_evidence(record.occurrence_id)
            if occurrence is None:
                continue
            rows.append({
                "obligation_id": item.obligation_id, "text": item.text, "kind": item.kind,
                "verification_evidence_id": record.evidence_id,
                "evidence": [self._bounded_record(occurrence)],
            })
        return rows

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

    def _generate_report(self, reportable, outstanding, states, fulfilled) -> tuple[str, dict[str, Any]]:
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
                {
                    "obligation_id": item.obligation_id, "text": item.text, "kind": item.kind,
                    "servicing_state": states[item.obligation_id],
                }
                for item in outstanding
            ],
            "fulfilled_in_turn": list(fulfilled),
        }
        response = self.provider.generate(ModelRequest(
            capability_id="chat.obligation_report",
            system=(
                "Report only the supplied durable evidence to the owner. Cover every reportable communication obligation. "
                "If still_open is non-empty, explicitly say those commitments remain outstanding without inventing an outcome. "
                "A still_open entry whose servicing_state is awaiting_verification has already been acted on with durable "
                "evidence: describe it as serviced with the outcome not yet verified, never as not done and never as fulfilled. "
                "A fulfilled_in_turn entry is a verified outcome from this same owner turn and may be reported as done. "
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

    def _snapshot_current(self, owner_turn_id: str, reportable) -> tuple[dict[str, int], tuple[str, ...], dict[str, str]]:
        all_open = [
            item for item in self.obligations.list_open(limit=5000)
            if item.owner_turn_id == owner_turn_id
        ]
        revisions = {item.obligation_id: item.revision for item in all_open}
        reportable_ids = tuple(item.obligation_id for item, _support in reportable)
        digests = {item.obligation_id: support["digest"] for item, support in reportable}
        return revisions, reportable_ids, digests

    def report_communications(self, *, limit_turns: int = 4) -> tuple[str, ...]:
        persisted: list[str] = []
        grouped = self._reportable()
        for owner_turn_id, reportable in list(grouped.items())[:max(1, int(limit_turns))]:
            conversation_id = reportable[0][0].conversation_id
            snapshot_revisions, reportable_ids, snapshot_digests = self._snapshot_current(
                owner_turn_id, reportable
            )
            reportable_set = set(reportable_ids)
            outstanding = [
                self.obligations.get(oid)
                for oid in snapshot_revisions
                if oid not in reportable_set
            ]
            outstanding_states = {
                item.obligation_id: self._servicing_state(item) for item in outstanding
            }
            if "awaiting_verification" in outstanding_states.values():
                # A commitment from this same owner turn is already serviced by durable
                # evidence and only awaits verification. Reporting now would describe a
                # done thing as outstanding, so hold the turn rather than mislead the owner.
                continue
            fulfilled = self._fulfilled_in_turn(owner_turn_id)
            try:
                candidate, generation = self._generate_report(
                    reportable, outstanding, outstanding_states, fulfilled
                )
                verification = self._verify_report(candidate, generation["basis"])
            except Exception:
                logger.warning("obligation report generation/verification failed", exc_info=True)
                continue
            if not verification["grounded"]:
                continue

            current = [
                item for item in self.obligations.list_open(limit=5000)
                if item.owner_turn_id == owner_turn_id
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
            if any(
                self._servicing_state(item) == "awaiting_verification"
                for item in current if item.obligation_id not in reportable_set
            ):
                # Servicing evidence can land while the report is being generated; the
                # revision/digest checks above do not observe that direction.
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
                        "owner_turn_id": owner_turn_id,
                        "obligation_ids": list(reportable_ids),
                        "obligation_revisions": revisions_to_resolve,
                        "outstanding_obligation_revisions": {
                            oid: revision for oid, revision in snapshot_revisions.items()
                            if oid not in reportable_set
                        },
                        "outstanding_obligation_states": dict(outstanding_states),
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
